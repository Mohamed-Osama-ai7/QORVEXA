"""Runtime contracts and execution lifecycle schemas for QORVEXA Solver Fabric.

Defines strict, deeply immutable Pydantic v2 domain models for:
- Job lifecycle states (with a validated transition table) and solver termination reasons
- Mathematical outcome classifications
- Verification claims, statuses and levels
- Structured evidence: model lowering, numerical duality gap, exact Farkas, exact unboundedness
- Cross-field consistency validation of the above

Scope of DTO validation (important):
    These models validate *structure and internal consistency* of what a producer reports
    (finite numbers, dimensions, matching identities, non-contradictory claims, required
    evidence present). They do NOT perform mathematical verification. A VERIFIED claim is a
    statement made by an independent verifier component; this module only refuses
    representations that are structurally inconsistent with it. No verifier exists yet.

Immutability:
    Models are frozen, vectors are stored as read-only mappings copied on validation (so no
    caller-held reference can mutate them), and sequences are tuples. `model_copy(update=...)`
    is refused because it bypasses validation. `model_construct` cannot be blocked by
    Pydantic and must not be used for results.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from enum import StrEnum
from fractions import Fraction
from types import MappingProxyType
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

# Canonical exactness scope constant
CANONICALIZED_INPUT_VALUES = "canonicalized_input_values"
ExactnessScopeLiteral = Literal["canonicalized_input_values"]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RATIONAL_RE = re.compile(r"^-?(0|[1-9][0-9]*)(/[1-9][0-9]*)?$")
_MAX_RATIONAL_CHARS = 4096
_GAP_RECONCILE_RTOL = 1e-9


# ---------------------------------------------------------------------------
# 1. Enums
# ---------------------------------------------------------------------------


class JobState(StrEnum):
    """Lifecycle state of a Solver Fabric runtime execution job."""

    QUEUED = "QUEUED"
    COMPILING = "COMPILING"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SolverTermination(StrEnum):
    """Low-level solver process termination status (metadata only; never gates outcome)."""

    OPTIMAL = "OPTIMAL"
    INFEASIBLE = "INFEASIBLE"
    UNBOUNDED = "UNBOUNDED"
    UNBOUNDED_OR_INFEASIBLE = "UNBOUNDED_OR_INFEASIBLE"
    TIME_LIMIT = "TIME_LIMIT"
    ITERATION_LIMIT = "ITERATION_LIMIT"
    NUMERICAL_ERROR = "NUMERICAL_ERROR"
    INTERRUPTED = "INTERRUPTED"
    SUPERVISOR_KILLED = "SUPERVISOR_KILLED"
    CRASHED = "CRASHED"
    MEMORY_LIMIT = "MEMORY_LIMIT"
    NOT_RUN = "NOT_RUN"
    OTHER = "OTHER"


class MathOutcome(StrEnum):
    """Authoritative mathematical outcome after independent verification."""

    SOLVED = "SOLVED"
    INFEASIBLE = "INFEASIBLE"
    UNBOUNDED = "UNBOUNDED"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"


class VerificationClaimKind(StrEnum):
    """Type of certificate or claim evaluated by the verification layer."""

    MODEL_LOWERING_VALIDATED = "MODEL_LOWERING_VALIDATED"
    PRIMAL_FEASIBILITY = "PRIMAL_FEASIBILITY"
    DUAL_GAP = "DUAL_GAP"
    INFEASIBILITY = "INFEASIBILITY"
    UNBOUNDEDNESS = "UNBOUNDEDNESS"


class VerificationStatus(StrEnum):
    """Verdict of the independent mathematical verification."""

    VERIFIED = "VERIFIED"
    REFUTED = "REFUTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"


class VerificationLevel(StrEnum):
    """Epistemic rigor level of verification."""

    NUMERICAL = "NUMERICAL"
    EXACT_RATIONAL = "EXACT_RATIONAL"


class OptimalityTier(StrEnum):
    """Certified tier of optimality. GAP_NUMERICAL is never an exact optimality proof."""

    NONE = "NONE"
    GAP_NUMERICAL = "GAP_NUMERICAL"


# ---------------------------------------------------------------------------
# 2. Immutable building blocks
# ---------------------------------------------------------------------------


def _check_finite(v: float) -> float:
    if not math.isfinite(v):
        raise ValueError("non-finite number (NaN/Infinity) is not allowed")
    return v


FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]


def _check_sha256(v: str) -> str:
    if not _SHA256_RE.match(v):
        raise ValueError("expected a lowercase 64-hex-character SHA-256 digest")
    return v


Sha256Hex = Annotated[str, AfterValidator(_check_sha256)]


def parse_rational(text: str) -> Fraction:
    """Parse a canonical rational string 'p' or 'p/q' (lowest terms, q > 0)."""
    if len(text) > _MAX_RATIONAL_CHARS or not _RATIONAL_RE.match(text):
        raise ValueError(f"invalid canonical rational literal: {text[:40]!r}")
    frac = Fraction(text)
    if str(frac) != text:
        raise ValueError(f"rational literal is not in canonical lowest terms: {text[:40]!r}")
    return frac


def _check_rational(v: str) -> str:
    parse_rational(v)
    return v


RationalStr = Annotated[str, AfterValidator(_check_rational)]


def _freeze_vector(v: Mapping[str, Any]) -> Mapping[str, Any]:
    """Copy into a read-only mapping so no external reference can mutate it."""
    out: dict[str, Any] = {}
    for key, val in v.items():
        if not isinstance(key, str) or not key:
            raise ValueError("vector keys must be non-empty strings")
        out[key] = val
    return MappingProxyType(out)


def _serialize_mapping(v: Mapping[str, Any]) -> dict[str, Any]:
    return dict(v)


FloatVector = Annotated[
    Mapping[str, FiniteFloat],
    AfterValidator(_freeze_vector),
    PlainSerializer(_serialize_mapping, return_type=dict[str, float]),
]
RationalVector = Annotated[
    Mapping[str, RationalStr],
    AfterValidator(_freeze_vector),
    PlainSerializer(_serialize_mapping, return_type=dict[str, str]),
]
StringMap = Annotated[
    Mapping[str, str],
    AfterValidator(_freeze_vector),
    PlainSerializer(_serialize_mapping, return_type=dict[str, str]),
]


class _FrozenModel(BaseModel):
    """Base for all runtime DTOs: strict, frozen, camelCase aliases."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        alias_generator=to_camel,
    )

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Any:
        if update:
            raise TypeError(
                "model_copy(update=...) bypasses validation; construct a new instance instead."
            )
        return super().model_copy(deep=deep)


# ---------------------------------------------------------------------------
# 3. Verification claims and structured evidence
# ---------------------------------------------------------------------------


class VerificationClaim(_FrozenModel):
    """Claim reported by an independent verification component."""

    claim_kind: VerificationClaimKind = Field(..., alias="claimKind")
    status: VerificationStatus
    level: VerificationLevel
    exactness_scope: str | None = Field(default=None, alias="exactnessScope")
    details: str | None = None
    residual: FiniteFloat | None = None

    @field_validator("exactness_scope")
    @classmethod
    def validate_scope_string(cls, v: str | None) -> str | None:
        if v is not None and v != CANONICALIZED_INPUT_VALUES:
            raise ValueError(
                f"Unsupported exactnessScope: '{v}'. Only '{CANONICALIZED_INPUT_VALUES}' is valid."
            )
        return v

    @model_validator(mode="after")
    def validate_exactness_invariants(self) -> VerificationClaim:
        if self.level == VerificationLevel.EXACT_RATIONAL:
            if self.exactness_scope != CANONICALIZED_INPUT_VALUES:
                raise ValueError(
                    f"Exact rational claims require exactnessScope='{CANONICALIZED_INPUT_VALUES}'."
                )
        elif self.exactness_scope is not None:
            raise ValueError("Numerical claims must not specify an exactnessScope.")
        return self


class RoundingSummary(_FrozenModel):
    """Summary of exact-to-binary64 lowering effects, from the lowering component."""

    coefficients_total: int = Field(..., ge=0, alias="coefficientsTotal")
    coefficients_rounded: int = Field(..., ge=0, alias="coefficientsRounded")
    subnormal_count: int = Field(..., ge=0, alias="subnormalCount")
    max_abs_rounding_error: RationalStr = Field(..., alias="maxAbsRoundingError")
    overflow_policy: Literal["REJECT"] = Field(default="REJECT", alias="overflowPolicy")
    underflow_policy: Literal["REJECT_NONZERO_TO_ZERO"] = Field(
        default="REJECT_NONZERO_TO_ZERO", alias="underflowPolicy"
    )
    signed_zero_normalized: Literal[True] = Field(default=True, alias="signedZeroNormalized")

    @model_validator(mode="after")
    def _check_counts(self) -> RoundingSummary:
        if self.coefficients_rounded > self.coefficients_total:
            raise ValueError("coefficientsRounded exceeds coefficientsTotal")
        if self.subnormal_count > self.coefficients_total:
            raise ValueError("subnormalCount exceeds coefficientsTotal")
        if parse_rational(self.max_abs_rounding_error) < 0:
            raise ValueError("maxAbsRoundingError must be non-negative")
        if self.coefficients_rounded == 0 and parse_rational(self.max_abs_rounding_error) != 0:
            raise ValueError("maxAbsRoundingError must be 0 when no coefficient was rounded")
        return self


class LoweringEvidence(_FrozenModel):
    """Evidence that a specific model was lowered to a specific compiled IR."""

    model_sha256: Sha256Hex = Field(..., alias="modelSha256")
    ir_sha256: Sha256Hex = Field(..., alias="irSha256")
    compiler_version: str = Field(..., min_length=1, max_length=64, alias="compilerVersion")
    verifier_version: str = Field(..., min_length=1, max_length=64, alias="verifierVersion")
    exactness_scope: ExactnessScopeLiteral = Field(
        default="canonicalized_input_values", alias="exactnessScope"
    )
    num_variables: int = Field(..., ge=0, alias="numVariables")
    num_rows: int = Field(..., ge=0, alias="numRows")
    rounding: RoundingSummary


class GapEvidence(_FrozenModel):
    """Numerical primal/dual objective gap. NOT an exact optimality proof."""

    model_sha256: Sha256Hex = Field(..., alias="modelSha256")
    policy_id: str = Field(..., min_length=1, max_length=128, alias="policyId")
    primal_objective: FiniteFloat = Field(..., alias="primalObjective")
    dual_objective: FiniteFloat = Field(..., alias="dualObjective")
    absolute_gap: FiniteFloat = Field(..., ge=0, alias="absoluteGap")
    relative_gap: FiniteFloat = Field(..., ge=0, alias="relativeGap")
    absolute_tolerance: FiniteFloat = Field(..., gt=0, alias="absoluteTolerance")
    relative_tolerance: FiniteFloat = Field(..., gt=0, alias="relativeTolerance")

    @model_validator(mode="after")
    def _check_gap(self) -> GapEvidence:
        diff = abs(self.primal_objective - self.dual_objective)
        scale = max(1.0, abs(self.primal_objective), abs(self.dual_objective))
        if abs(self.absolute_gap - diff) > _GAP_RECONCILE_RTOL * scale:
            raise ValueError("absoluteGap does not match |primalObjective - dualObjective|")
        if self.absolute_gap > self.absolute_tolerance and (
            self.relative_gap > self.relative_tolerance
        ):
            raise ValueError("reported gap exceeds both the absolute and relative tolerance")
        return self


class FarkasEvidence(_FrozenModel):
    """Exact rational Farkas certificate, as reported by a verifier (c0 excluded)."""

    model_sha256: Sha256Hex = Field(..., alias="modelSha256")
    multipliers: RationalVector
    certificate_value: RationalStr = Field(..., alias="certificateValue")
    objective_constant_excluded: Literal[True] = Field(
        default=True, alias="objectiveConstantExcluded"
    )

    @model_validator(mode="after")
    def _check(self) -> FarkasEvidence:
        if not any(parse_rational(v) != 0 for v in self.multipliers.values()):
            raise ValueError("Farkas multipliers must contain a nonzero entry")
        if parse_rational(self.certificate_value) <= 0:
            raise ValueError("Farkas certificateValue (D0, c0 excluded) must be > 0")
        return self


class UnboundedEvidence(_FrozenModel):
    """Exact feasible anchor plus exact improving nonzero recession ray."""

    model_sha256: Sha256Hex = Field(..., alias="modelSha256")
    feasible_anchor: RationalVector = Field(..., alias="feasibleAnchor")
    improving_ray: RationalVector = Field(..., alias="improvingRay")
    objective_slope_min_form: RationalStr = Field(..., alias="objectiveSlopeMinForm")

    @model_validator(mode="after")
    def _check(self) -> UnboundedEvidence:
        if set(self.feasible_anchor) != set(self.improving_ray):
            raise ValueError("feasibleAnchor and improvingRay must cover the same variables")
        if not any(parse_rational(v) != 0 for v in self.improving_ray.values()):
            raise ValueError("improvingRay must be nonzero")
        if parse_rational(self.objective_slope_min_form) >= 0:
            raise ValueError("objectiveSlopeMinForm (c^T d in minimization form) must be < 0")
        return self


# ---------------------------------------------------------------------------
# 4. Solver Runtime Result
# ---------------------------------------------------------------------------

_K = VerificationClaimKind
_S = VerificationStatus
_L = VerificationLevel


class SolverRuntimeResult(_FrozenModel):
    """Result of a solver run coupled with verification claims and structured evidence.

    `primal_solution`, `dual_solution`, `infeasibility_ray` and `unbounded_ray` are RAW
    solver candidates (finite binary64). They are never evidence on their own. Mathematical
    evidence lives in the *_evidence fields and is only meaningful together with the matching
    VERIFIED claim produced by an independent verifier.
    """

    outcome: MathOutcome
    solver_termination: SolverTermination = Field(..., alias="solverTermination")
    optimality_tier: OptimalityTier = Field(default=OptimalityTier.NONE, alias="optimalityTier")
    claims: tuple[VerificationClaim, ...] = ()
    objective_value: FiniteFloat | None = Field(default=None, alias="objectiveValue")
    primal_solution: FloatVector | None = Field(default=None, alias="primalSolution")
    dual_solution: FloatVector | None = Field(default=None, alias="dualSolution")
    infeasibility_ray: FloatVector | None = Field(default=None, alias="infeasibilityRay")
    unbounded_ray: FloatVector | None = Field(default=None, alias="unboundedRay")
    lowering_evidence: LoweringEvidence | None = Field(default=None, alias="loweringEvidence")
    gap_evidence: GapEvidence | None = Field(default=None, alias="gapEvidence")
    infeasibility_evidence: FarkasEvidence | None = Field(
        default=None, alias="infeasibilityEvidence"
    )
    unbounded_evidence: UnboundedEvidence | None = Field(default=None, alias="unboundedEvidence")
    failure_details: str | None = Field(default=None, alias="failureDetails")
    metadata: StringMap = Field(default_factory=lambda: MappingProxyType({}))

    def find_claim(
        self,
        kind: VerificationClaimKind,
        status: VerificationStatus | None = None,
        level: VerificationLevel | None = None,
    ) -> VerificationClaim | None:
        """Locate a claim in the claim set."""
        for c in self.claims:
            if c.claim_kind != kind:
                continue
            if status is not None and c.status != status:
                continue
            if level is not None and c.level != level:
                continue
            return c
        return None

    def _verified(
        self, kind: VerificationClaimKind, level: VerificationLevel | None = None
    ) -> bool:
        return self.find_claim(kind, status=_S.VERIFIED, level=level) is not None

    @model_validator(mode="after")
    def validate_mathematical_invariants(self) -> SolverRuntimeResult:
        # Solver termination is metadata only: it never gates or determines the outcome.
        if self.optimality_tier != OptimalityTier.NONE and self.outcome != MathOutcome.SOLVED:
            raise ValueError("optimalityTier other than NONE is only valid when outcome is SOLVED.")

        self._check_claim_set()
        self._check_evidence_coupling()
        self._check_contradictions()
        self._check_outcome_requirements()
        self._check_dimensions()

        if self.outcome == MathOutcome.FAILED:
            if not self.failure_details or not self.failure_details.strip():
                raise ValueError("FAILED outcome requires non-empty failureDetails.")
        elif self.failure_details is not None:
            raise ValueError("failureDetails must be null when outcome is not FAILED.")
        return self

    # -- helpers -----------------------------------------------------------------------

    def _check_claim_set(self) -> None:
        seen: set[tuple[VerificationClaimKind, VerificationLevel]] = set()
        for c in self.claims:
            key = (c.claim_kind, c.level)
            if key in seen:
                raise ValueError(f"Duplicate claim {c.claim_kind.value}/{c.level.value}.")
            seen.add(key)

    def _check_evidence_coupling(self) -> None:
        low = self.lowering_evidence
        if self._verified(_K.MODEL_LOWERING_VALIDATED):
            if low is None:
                raise ValueError(
                    "A VERIFIED MODEL_LOWERING_VALIDATED claim requires loweringEvidence."
                )
            if not self._verified(_K.MODEL_LOWERING_VALIDATED, _L.EXACT_RATIONAL):
                raise ValueError("MODEL_LOWERING_VALIDATED must be EXACT_RATIONAL level.")
        elif low is not None:
            raise ValueError("loweringEvidence requires a VERIFIED MODEL_LOWERING_VALIDATED claim.")

        exact_infeas = self._verified(_K.INFEASIBILITY, _L.EXACT_RATIONAL)
        if exact_infeas != (self.infeasibility_evidence is not None):
            raise ValueError(
                "infeasibilityEvidence must be present iff an EXACT_RATIONAL VERIFIED "
                "INFEASIBILITY claim is present."
            )
        exact_unb = self._verified(_K.UNBOUNDEDNESS, _L.EXACT_RATIONAL)
        if exact_unb != (self.unbounded_evidence is not None):
            raise ValueError(
                "unboundedEvidence must be present iff an EXACT_RATIONAL VERIFIED "
                "UNBOUNDEDNESS claim is present."
            )

        gap_claim = self._verified(_K.DUAL_GAP, _L.NUMERICAL)
        tier_gap = self.optimality_tier == OptimalityTier.GAP_NUMERICAL
        if tier_gap and (self.gap_evidence is None or not gap_claim):
            raise ValueError(
                "Optimality tier GAP_NUMERICAL requires gapEvidence and a VERIFIED NUMERICAL "
                "DUAL_GAP claim. A numerical gap is never 'proven optimal'."
            )
        if self.gap_evidence is not None and not tier_gap:
            raise ValueError("gapEvidence is only valid with optimalityTier GAP_NUMERICAL.")
        if self._verified(_K.DUAL_GAP, _L.EXACT_RATIONAL):
            raise ValueError("Exact optimality proofs are not supported; DUAL_GAP is NUMERICAL.")

        if low is not None:
            digests = {
                "gapEvidence": self.gap_evidence.model_sha256 if self.gap_evidence else None,
                "infeasibilityEvidence": (
                    self.infeasibility_evidence.model_sha256
                    if self.infeasibility_evidence
                    else None
                ),
                "unboundedEvidence": (
                    self.unbounded_evidence.model_sha256 if self.unbounded_evidence else None
                ),
            }
            for name, digest in digests.items():
                if digest is not None and digest != low.model_sha256:
                    raise ValueError(f"{name} is bound to a different model than loweringEvidence.")
        elif (
            self.gap_evidence is not None
            or self.infeasibility_evidence is not None
            or self.unbounded_evidence is not None
        ):
            raise ValueError("Structured evidence requires loweringEvidence for model identity.")

    def _check_contradictions(self) -> None:
        v_inf = self._verified(_K.INFEASIBILITY)
        v_unb = self._verified(_K.UNBOUNDEDNESS)
        v_primal = self._verified(_K.PRIMAL_FEASIBILITY)
        if v_inf and v_primal:
            raise ValueError(
                "Contradictory claims: verified INFEASIBILITY cannot coexist with verified "
                "PRIMAL_FEASIBILITY."
            )
        if v_inf and v_unb:
            raise ValueError(
                "Contradictory claims: verified INFEASIBILITY cannot coexist with verified "
                "UNBOUNDEDNESS."
            )
        if v_unb and self._verified(_K.DUAL_GAP):
            raise ValueError(
                "Contradictory claims: UNBOUNDEDNESS cannot coexist with a verified DUAL_GAP."
            )

    def _check_outcome_requirements(self) -> None:
        o = self.outcome
        if o in (MathOutcome.SOLVED, MathOutcome.INFEASIBLE, MathOutcome.UNBOUNDED) and (
            self.lowering_evidence is None
        ):
            raise ValueError(
                f"{o.value} outcome requires a VERIFIED MODEL_LOWERING_VALIDATED claim."
            )

        refuted = {c.claim_kind for c in self.claims if c.status == _S.REFUTED}

        if o == MathOutcome.SOLVED:
            if not self._verified(_K.PRIMAL_FEASIBILITY):
                raise ValueError("SOLVED outcome requires a VERIFIED PRIMAL_FEASIBILITY claim.")
            if self.primal_solution is None:
                raise ValueError("SOLVED outcome requires a non-null primalSolution.")
            if self.objective_value is None:
                raise ValueError("SOLVED outcome requires a finite objectiveValue.")
            if self.infeasibility_evidence or self.unbounded_evidence:
                raise ValueError("SOLVED cannot carry infeasibility or unboundedness evidence.")
            if _K.PRIMAL_FEASIBILITY in refuted:
                raise ValueError("SOLVED conflicts with a REFUTED PRIMAL_FEASIBILITY claim.")

        elif o == MathOutcome.INFEASIBLE:
            if not self._verified(_K.INFEASIBILITY, _L.EXACT_RATIONAL):
                raise ValueError(
                    "INFEASIBLE outcome requires an EXACT_RATIONAL VERIFIED INFEASIBILITY claim. "
                    "Numerical Farkas evidence alone cannot produce INFEASIBLE."
                )
            if _K.INFEASIBILITY in refuted:
                raise ValueError("INFEASIBLE conflicts with a REFUTED INFEASIBILITY claim.")

        elif o == MathOutcome.UNBOUNDED:
            if not self._verified(_K.UNBOUNDEDNESS, _L.EXACT_RATIONAL):
                raise ValueError(
                    "UNBOUNDED outcome requires an EXACT_RATIONAL VERIFIED UNBOUNDEDNESS claim."
                )
            if not self._verified(_K.PRIMAL_FEASIBILITY, _L.EXACT_RATIONAL):
                raise ValueError(
                    "UNBOUNDED outcome requires an EXACT_RATIONAL VERIFIED "
                    "PRIMAL_FEASIBILITY claim for the feasible anchor."
                )
            if _K.UNBOUNDEDNESS in refuted:
                raise ValueError("UNBOUNDED conflicts with a REFUTED UNBOUNDEDNESS claim.")

    def _check_dimensions(self) -> None:
        low = self.lowering_evidence
        if low is None:
            return
        n, m = low.num_variables, low.num_rows
        if self.primal_solution is not None and len(self.primal_solution) != n:
            raise ValueError(
                f"primalSolution has {len(self.primal_solution)} entries; model has {n} variables."
            )
        if self.unbounded_ray is not None and len(self.unbounded_ray) != n:
            raise ValueError("unboundedRay length does not match the model variable count.")
        if self.dual_solution is not None and len(self.dual_solution) != m:
            raise ValueError("dualSolution length does not match the model row count.")
        if self.infeasibility_ray is not None and len(self.infeasibility_ray) != m:
            raise ValueError("infeasibilityRay length does not match the model row count.")
        if self.unbounded_evidence is not None and (
            len(self.unbounded_evidence.feasible_anchor) != n
        ):
            raise ValueError("unboundedEvidence vectors do not match the model variable count.")
        if self.infeasibility_evidence is not None and (
            len(self.infeasibility_evidence.multipliers) != m
        ):
            raise ValueError("Farkas multipliers do not match the model row count.")


# ---------------------------------------------------------------------------
# 5. Solver Runtime Job (validated lifecycle)
# ---------------------------------------------------------------------------

_ALLOWED_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.QUEUED: frozenset({JobState.COMPILING, JobState.FAILED, JobState.CANCELLED}),
    JobState.COMPILING: frozenset({JobState.RUNNING, JobState.FAILED, JobState.CANCELLED}),
    JobState.RUNNING: frozenset({JobState.VERIFYING, JobState.FAILED, JobState.CANCELLED}),
    JobState.VERIFYING: frozenset({JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}),
    JobState.COMPLETED: frozenset(),
    JobState.FAILED: frozenset(),
    JobState.CANCELLED: frozenset(),
}


class SolverRuntimeJob(_FrozenModel):
    """Runtime execution job tracking solver lifecycle and result."""

    job_id: str = Field(..., min_length=1, max_length=128, alias="jobId")
    state: JobState
    result: SolverRuntimeResult | None = None
    error_message: str | None = Field(default=None, alias="errorMessage")

    @model_validator(mode="after")
    def validate_job_lifecycle(self) -> SolverRuntimeJob:
        if not self.job_id.strip():
            raise ValueError("jobId must not be blank.")

        if self.state == JobState.CANCELLED:
            if self.result is not None:
                raise ValueError("CANCELLED job must have null result (null outcome).")

        elif self.state in (
            JobState.QUEUED,
            JobState.COMPILING,
            JobState.RUNNING,
            JobState.VERIFYING,
        ):
            if self.result is not None:
                raise ValueError(
                    f"Job in active state {self.state.value} must not have a result attached."
                )

        elif self.state == JobState.COMPLETED:
            if self.result is None:
                raise ValueError("COMPLETED job requires a non-null result.")
            if self.result.outcome == MathOutcome.FAILED:
                raise ValueError("COMPLETED job cannot carry a FAILED result outcome.")
            if self.error_message is not None:
                raise ValueError("COMPLETED job must not have an errorMessage.")

        elif self.state == JobState.FAILED:
            if self.result is not None and self.result.outcome != MathOutcome.FAILED:
                raise ValueError("FAILED job cannot contain a non-FAILED result outcome.")
            if self.result is None and (not self.error_message or not self.error_message.strip()):
                raise ValueError("FAILED job without result requires a non-empty errorMessage.")

        return self

    def transition(
        self,
        new_state: JobState,
        *,
        result: SolverRuntimeResult | None = None,
        error_message: str | None = None,
    ) -> SolverRuntimeJob:
        """Return a new job in `new_state`, enforcing the lifecycle transition table."""
        if new_state not in _ALLOWED_TRANSITIONS[self.state]:
            raise ValueError(f"Illegal job transition {self.state.value} -> {new_state.value}.")
        return SolverRuntimeJob(
            job_id=self.job_id,
            state=new_state,
            result=result,
            error_message=error_message,
        )

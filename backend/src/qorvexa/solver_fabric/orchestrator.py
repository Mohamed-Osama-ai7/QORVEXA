"""End-to-end continuous-LP orchestration (S3, Phase A) with dependency-injected solver.

Raw JSON -> strict ingress -> M1 spec -> LP intake -> Encoding A -> exact canonical LP ->
binary64 lowering -> independent lowering check -> solver adapter -> independent verifier ->
SolverRuntimeResult inside a validated SolverRuntimeJob lifecycle.

Mathematical outcome rules (solver status/termination is metadata only):
* INFEASIBLE   exact Farkas certificate (solver ray or own exact interval conflict) only.
* UNBOUNDED    exact feasible anchor + exact improving recession ray only.
* SOLVED       numerically feasible primal + exactly recomputed dual bound within tolerance,
               tier GAP_NUMERICAL (never an exact optimality proof).
* UNKNOWN      anything else that ran (numerical-only infeasible/unbounded, no usable gap,
               timeout without certificate).
* FAILED       input rejected, lowering invalid, worker failure/mismatch, verifier error.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from fractions import Fraction

from qorvexa.contracts.solver_fabric import GeneralizedProblemSpec
from qorvexa.contracts.solver_runtime import (
    CANONICALIZED_INPUT_VALUES,
    FarkasEvidence,
    GapEvidence,
    JobState,
    LoweringEvidence,
    MathOutcome,
    OptimalityTier,
    RoundingSummary,
    SolverRuntimeJob,
    SolverRuntimeResult,
    SolverTermination,
    UnboundedEvidence,
    VerificationClaim,
    VerificationClaimKind,
    VerificationLevel,
    VerificationStatus,
)
from qorvexa.solver_fabric.affine import AffineEvaluationError
from qorvexa.solver_fabric.canonical_lp import (
    CanonicalLPError,
    CanonicalRangedLP,
    LoweredLP,
    ModelLimits,
    lower_ranged_lp,
)
from qorvexa.solver_fabric.encoding_a import NormalizationError, build_model_from_derived
from qorvexa.solver_fabric.exact import ExactResourceError
from qorvexa.solver_fabric.lp_intake import IntakeError, IntakeLimits, derive_problem_forms
from qorvexa.solver_fabric.raw_ingress import (
    RawIngressError,
    RawIngressLimits,
    parse_problem_spec_json,
)
from qorvexa.solver_fabric.verifier import (
    VERIFIER_VERSION,
    VerificationPolicy,
    anchor_candidates,
    check_primal,
    dual_bound_min_form,
    evaluate_gap,
    exact_vector,
    find_interval_conflict,
    map_vector,
    repair_dual,
    safe_float,
    to_original_sense,
    vector_variants,
    verify_farkas,
    verify_lowering,
    verify_unbounded,
)
from qorvexa.solver_fabric.worker_contract import (
    LoweredLPPayload,
    SolverAdapter,
    SolverOptions,
    WorkerFailure,
    WorkerFailureCode,
    WorkerRequest,
    WorkerResponse,
    WorkerStatus,
)

_K = VerificationClaimKind
_S = VerificationStatus
_L = VerificationLevel

_COMPILE_ERRORS = (
    RawIngressError,
    IntakeError,
    NormalizationError,
    CanonicalLPError,
    AffineEvaluationError,
    ExactResourceError,
)


@dataclass(frozen=True)
class OrchestratorConfig:
    ingress_limits: RawIngressLimits | None = None
    intake_limits: IntakeLimits | None = None
    model_limits: ModelLimits | None = None
    solver_options: SolverOptions = field(default_factory=SolverOptions)
    policy: VerificationPolicy = field(default_factory=VerificationPolicy)


@dataclass(frozen=True)
class PipelineRejection:
    stage: str
    code: str
    path: str
    message: str


@dataclass(frozen=True)
class OrchestrationOutcome:
    job: SolverRuntimeJob
    rejection: PipelineRejection | None = None
    model_sha256: str | None = None
    ir_sha256: str | None = None

    @property
    def result(self) -> SolverRuntimeResult | None:
        return self.job.result


def _failed_result(
    code: str,
    message: str,
    stage: str,
    termination: SolverTermination = SolverTermination.NOT_RUN,
    meta: Mapping[str, str] | None = None,
    lowering_claims: tuple[VerificationClaim, ...] = (),
) -> SolverRuntimeResult:
    metadata = {"stage": stage, "code": code, "verifier_version": VERIFIER_VERSION}
    metadata.update(meta or {})
    return SolverRuntimeResult(
        outcome=MathOutcome.FAILED,
        solver_termination=termination,
        claims=lowering_claims,
        failure_details=f"{code}: {message}"[:2000],
        metadata=metadata,
    )


class LPOrchestrator:
    """Runs the validated continuous-LP pipeline against an injected `SolverAdapter`."""

    def __init__(self, adapter: SolverAdapter, config: OrchestratorConfig | None = None) -> None:
        self._adapter = adapter
        self._config = config or OrchestratorConfig()

    # -- public entry points ------------------------------------------------------------

    def solve_json(self, raw: bytes | str, job_id: str | None = None) -> OrchestrationOutcome:
        jid = job_id or uuid.uuid4().hex
        job = SolverRuntimeJob(job_id=jid, state=JobState.QUEUED).transition(JobState.COMPILING)
        try:
            spec = parse_problem_spec_json(raw, self._config.ingress_limits)
        except _COMPILE_ERRORS as err:
            return self._reject(job, "ingress", err)
        return self._run(spec, job)

    def solve_spec(
        self, spec: GeneralizedProblemSpec, job_id: str | None = None
    ) -> OrchestrationOutcome:
        """Entry for a spec already parsed elsewhere. Raw-JSON guarantees then do NOT apply."""
        jid = job_id or uuid.uuid4().hex
        job = SolverRuntimeJob(job_id=jid, state=JobState.QUEUED).transition(JobState.COMPILING)
        return self._run(spec, job)

    # -- pipeline -----------------------------------------------------------------------

    def _reject(self, job: SolverRuntimeJob, stage: str, err: Exception) -> OrchestrationOutcome:
        code = str(getattr(err, "code", type(err).__name__))
        path = str(getattr(err, "path", getattr(err, "location", "")))
        message = str(getattr(err, "message", err))
        result = _failed_result(code, message, stage)
        failed = job.transition(JobState.FAILED, result=result)
        return OrchestrationOutcome(failed, PipelineRejection(stage, code, path, message))

    def _run(self, spec: GeneralizedProblemSpec, job: SolverRuntimeJob) -> OrchestrationOutcome:
        cfg = self._config
        try:
            derived = derive_problem_forms(spec, cfg.intake_limits)
            model = build_model_from_derived(derived, cfg.model_limits)
            ir, report = lower_ranged_lp(model)
        except _COMPILE_ERRORS as err:
            return self._reject(job, "compile", err)

        check = verify_lowering(model, ir, report)
        if not check.valid:
            result = _failed_result(
                "LOWERING_VERIFICATION_FAILED", "; ".join(check.mismatches[:5]), "lowering"
            )
            return OrchestrationOutcome(
                job.transition(JobState.FAILED, result=result),
                PipelineRejection(
                    "lowering", "LOWERING_VERIFICATION_FAILED", "", "; ".join(check.mismatches[:5])
                ),
                model.model_sha256,
                ir.ir_sha256,
            )

        low_claim = VerificationClaim(
            claim_kind=_K.MODEL_LOWERING_VALIDATED,
            status=_S.VERIFIED,
            level=_L.EXACT_RATIONAL,
            exactness_scope=CANONICALIZED_INPUT_VALUES,
            details="IR equals the independent correctly rounded lowering of the exact model.",
        )
        low_evidence = LoweringEvidence(
            model_sha256=model.model_sha256,
            ir_sha256=ir.ir_sha256,
            compiler_version=report.compiler_version,
            verifier_version=VERIFIER_VERSION,
            num_variables=model.num_variables,
            num_rows=model.num_rows,
            rounding=RoundingSummary(
                coefficients_total=check.values_total,
                coefficients_rounded=check.values_rounded,
                subnormal_count=check.subnormal_count,
                max_abs_rounding_error=str(check.max_abs_rounding_error),
            ),
        )

        job = job.transition(JobState.RUNNING)
        request = WorkerRequest(
            request_id=job.job_id,
            model_sha256=model.model_sha256,
            ir_sha256=ir.ir_sha256,
            compiler_version=report.compiler_version,
            problem=LoweredLPPayload.from_lowered(ir),
            options=cfg.solver_options,
        )
        response = self._call_adapter(request)

        job = job.transition(JobState.VERIFYING)
        mismatch = self._binding_mismatch(request, response)
        if mismatch is not None:
            result = _failed_result(
                "WORKER_BINDING_MISMATCH",
                mismatch,
                "worker",
                meta={"solver_adapter": self._adapter_name()},
                lowering_claims=(),
            )
            return OrchestrationOutcome(
                job.transition(JobState.FAILED, result=result),
                PipelineRejection("worker", "WORKER_BINDING_MISMATCH", "", mismatch),
                model.model_sha256,
                ir.ir_sha256,
            )
        try:
            result = self._verify(model, ir, response, low_claim, low_evidence)
        except (ValueError, ArithmeticError) as err:
            result = _failed_result("VERIFIER_ERROR", str(err), "verify")
        if result.outcome == MathOutcome.FAILED:
            return OrchestrationOutcome(
                job.transition(JobState.FAILED, result=result),
                PipelineRejection(
                    str(result.metadata.get("stage", "verify")),
                    str(result.metadata.get("code", "FAILED")),
                    "",
                    result.failure_details or "",
                ),
                model.model_sha256,
                ir.ir_sha256,
            )
        return OrchestrationOutcome(
            job.transition(JobState.COMPLETED, result=result),
            None,
            model.model_sha256,
            ir.ir_sha256,
        )

    # -- worker -------------------------------------------------------------------------

    def _adapter_name(self) -> str:
        try:
            return str(self._adapter.name)[:64]
        except Exception:
            return "unknown"

    def _call_adapter(self, request: WorkerRequest) -> WorkerResponse:
        try:
            response = self._adapter.solve(request)
        except Exception as err:
            return self._synthetic_failure(request, f"{type(err).__name__}: {err}"[:1000])
        if not isinstance(response, WorkerResponse):
            return self._synthetic_failure(request, "adapter returned a non-WorkerResponse value")
        return response

    @staticmethod
    def _synthetic_failure(request: WorkerRequest, message: str) -> WorkerResponse:
        return WorkerResponse(
            request_id=request.request_id,
            model_sha256=request.model_sha256,
            ir_sha256=request.ir_sha256,
            status=WorkerStatus.FAILED,
            termination=SolverTermination.CRASHED,
            failure=WorkerFailure(code=WorkerFailureCode.CRASHED, message=message),
        )

    @staticmethod
    def _binding_mismatch(request: WorkerRequest, response: WorkerResponse) -> str | None:
        if response.contract_version != request.contract_version:
            return "contract version mismatch"
        if response.request_id != request.request_id:
            return "request id mismatch"
        if response.model_sha256 != request.model_sha256:
            return "model hash mismatch"
        if response.ir_sha256 != request.ir_sha256:
            return "IR hash mismatch"
        return None

    # -- verification -------------------------------------------------------------------

    def _metadata(
        self, model: CanonicalRangedLP, response: WorkerResponse, extra: Mapping[str, str]
    ) -> dict[str, str]:
        meta = {
            "verifier_version": VERIFIER_VERSION,
            "policy_id": self._config.policy.policy_id,
            "solver_adapter": self._adapter_name(),
            "solver_name": response.solver_name,
            "solver_version": response.solver_version,
            "solver_raw_status": response.raw_solver_status,
            "worker_status": response.status.value,
            "runtime_seconds": repr(response.runtime_seconds),
            "objective_sense": model.original_sense,
            "request_id": response.request_id,
        }
        if response.iterations is not None:
            meta["iterations"] = str(response.iterations)
        meta.update(extra)
        return meta

    def _verify(
        self,
        model: CanonicalRangedLP,
        ir: LoweredLP,
        response: WorkerResponse,
        low_claim: VerificationClaim,
        low_evidence: LoweringEvidence,
    ) -> SolverRuntimeResult:
        n, m = model.num_variables, model.num_rows
        policy = self._config.policy
        sha = model.model_sha256
        notes: dict[str, str] = {}

        def vec(raw: tuple[float, ...] | None, size: int, name: str) -> tuple[float, ...] | None:
            if raw is None:
                return None
            if len(raw) != size:
                notes[f"{name}_ignored"] = f"length {len(raw)} != {size}"
                return None
            return raw

        x_raw = vec(response.primal_solution, n, "primal_solution")
        y_raw = vec(response.dual_solution, m, "dual_solution")
        far_raw = vec(response.farkas_ray, m, "farkas_ray")
        ray_raw = vec(response.primal_ray, n, "primal_ray")

        def build(**kw: object) -> SolverRuntimeResult:
            return SolverRuntimeResult(
                solver_termination=response.termination,
                lowering_evidence=low_evidence,
                metadata=self._metadata(model, response, notes),
                primal_solution=map_vector(model.variable_ids, x_raw) if x_raw else None,
                dual_solution=map_vector(model.row_ids, y_raw) if y_raw else None,
                **kw,
            )

        # 1. exact infeasibility (solver ray first, then own exact interval conflict)
        farkas = None
        if far_raw is not None:
            for cand in vector_variants(far_raw, m, "farkas_ray"):
                v = verify_farkas(model, cand, "solver_farkas_ray")
                if v.verified:
                    farkas = v
                    break
        if farkas is None:
            farkas = find_interval_conflict(model)
        if farkas is not None and farkas.certificate_value is not None:
            return SolverRuntimeResult(
                outcome=MathOutcome.INFEASIBLE,
                solver_termination=response.termination,
                claims=(
                    low_claim,
                    VerificationClaim(
                        claim_kind=_K.INFEASIBILITY,
                        status=_S.VERIFIED,
                        level=_L.EXACT_RATIONAL,
                        exactness_scope=CANONICALIZED_INPUT_VALUES,
                        details=f"exact Farkas certificate (source: {farkas.source})",
                    ),
                ),
                lowering_evidence=low_evidence,
                infeasibility_evidence=FarkasEvidence(
                    model_sha256=sha,
                    multipliers={
                        rid: str(mult)
                        for rid, mult in zip(model.row_ids, farkas.multipliers, strict=True)
                    },
                    certificate_value=str(farkas.certificate_value),
                ),
                infeasibility_ray=map_vector(model.row_ids, far_raw) if far_raw else None,
                metadata=self._metadata(
                    model, response, {**notes, "certificate_source": farkas.source}
                ),
            )

        # 2. exact unboundedness
        if ray_raw is not None:
            anchors = anchor_candidates(model, x_raw)
            for ray in vector_variants(ray_raw, n, "primal_ray"):
                for anchor_name, anchor in anchors:
                    u = verify_unbounded(model, anchor, ray)
                    if u.verified and u.slope_min_form is not None:
                        notes["anchor_source"] = anchor_name
                        return SolverRuntimeResult(
                            outcome=MathOutcome.UNBOUNDED,
                            solver_termination=response.termination,
                            claims=(
                                low_claim,
                                VerificationClaim(
                                    claim_kind=_K.PRIMAL_FEASIBILITY,
                                    status=_S.VERIFIED,
                                    level=_L.EXACT_RATIONAL,
                                    exactness_scope=CANONICALIZED_INPUT_VALUES,
                                    details="exactly feasible anchor",
                                    residual=0.0,
                                ),
                                VerificationClaim(
                                    claim_kind=_K.UNBOUNDEDNESS,
                                    status=_S.VERIFIED,
                                    level=_L.EXACT_RATIONAL,
                                    exactness_scope=CANONICALIZED_INPUT_VALUES,
                                    details=u.reason,
                                ),
                            ),
                            lowering_evidence=low_evidence,
                            unbounded_evidence=UnboundedEvidence(
                                model_sha256=sha,
                                feasible_anchor={
                                    vid: str(a)
                                    for vid, a in zip(model.variable_ids, u.anchor, strict=True)
                                },
                                improving_ray={
                                    vid: str(d)
                                    for vid, d in zip(model.variable_ids, u.ray, strict=True)
                                },
                                objective_slope_min_form=str(u.slope_min_form),
                            ),
                            unbounded_ray=map_vector(model.variable_ids, ray_raw),
                            metadata=self._metadata(model, response, notes),
                        )

        # 3. numerical optimality gap from an exactly recomputed primal/dual pair
        claims: list[VerificationClaim] = [low_claim]
        if x_raw is not None:
            primal = check_primal(model, exact_vector(x_raw, n, "primal_solution"))
            violation = safe_float(primal.max_violation)
            feasible = primal.max_violation <= Fraction(policy.primal_tolerance)
            claims.append(
                VerificationClaim(
                    claim_kind=_K.PRIMAL_FEASIBILITY,
                    status=_S.VERIFIED if feasible else _S.REFUTED,
                    level=_L.EXACT_RATIONAL if primal.exactly_feasible else _L.NUMERICAL,
                    exactness_scope=CANONICALIZED_INPUT_VALUES if primal.exactly_feasible else None,
                    details=("exact max violation " + str(primal.max_violation))[:500],
                    residual=violation if violation is not None else 0.0,
                )
            )
            if feasible and y_raw is not None:
                y_exact = exact_vector(y_raw, m, "dual_solution")
                gap = evaluate_gap(model, primal, y_exact, policy)
                if not gap.accepted and dual_bound_min_form(model, y_exact) is None:
                    # D(y) = -inf: try exact-verified repairs of the dual candidate (never trusted)
                    for src, base in (
                        ("exact", y_exact),
                        *(
                            (f"den{c}", [v.limit_denominator(c) for v in y_exact])
                            for c in (10**6, 10**9)
                        ),
                    ):
                        rep = repair_dual(model, base)
                        if rep is None:
                            continue
                        retry = evaluate_gap(model, primal, rep[1], policy)
                        if retry.accepted:
                            gap = retry
                            notes["dual_source"] = f"repaired_from_{src}"
                            break
                obj = safe_float(to_original_sense(model, primal.objective_min_form))
                if (
                    gap.accepted
                    and obj is not None
                    and gap.primal_original is not None
                    and gap.dual_original is not None
                    and gap.absolute_gap is not None
                    and gap.relative_gap is not None
                ):
                    claims.append(
                        VerificationClaim(
                            claim_kind=_K.DUAL_GAP,
                            status=_S.VERIFIED,
                            level=_L.NUMERICAL,
                            details="numerical gap only; not an exact optimality proof",
                            residual=gap.absolute_gap,
                        )
                    )
                    return SolverRuntimeResult(
                        outcome=MathOutcome.SOLVED,
                        solver_termination=response.termination,
                        optimality_tier=OptimalityTier.GAP_NUMERICAL,
                        claims=tuple(claims),
                        objective_value=obj,
                        primal_solution=map_vector(model.variable_ids, x_raw),
                        dual_solution=map_vector(model.row_ids, y_raw),
                        lowering_evidence=low_evidence,
                        gap_evidence=GapEvidence(
                            model_sha256=sha,
                            policy_id=policy.policy_id,
                            primal_objective=gap.primal_original,
                            dual_objective=gap.dual_original,
                            absolute_gap=gap.absolute_gap,
                            relative_gap=gap.relative_gap,
                            absolute_tolerance=policy.absolute_gap_tolerance,
                            relative_tolerance=policy.relative_gap_tolerance,
                        ),
                        metadata=self._metadata(
                            model,
                            response,
                            {**notes, "optimality_note": "GAP_NUMERICAL is not an exact proof"},
                        ),
                    )
                claims.append(
                    VerificationClaim(
                        claim_kind=_K.DUAL_GAP,
                        status=_S.INCONCLUSIVE,
                        level=_L.NUMERICAL,
                        details=gap.reason[:500],
                    )
                )

        # 4. nothing mathematically justified
        if response.status == WorkerStatus.FAILED:
            failure = response.failure
            return _failed_result(
                "WORKER_FAILURE",
                f"{failure.code.value}: {failure.message}" if failure else "worker failed",
                "worker",
                response.termination,
                self._metadata(model, response, notes),
            )
        if response.termination in (
            SolverTermination.INFEASIBLE,
            SolverTermination.UNBOUNDED,
            SolverTermination.UNBOUNDED_OR_INFEASIBLE,
        ):
            kind = (
                _K.INFEASIBILITY
                if response.termination == SolverTermination.INFEASIBLE
                else _K.UNBOUNDEDNESS
            )
            claims.append(
                VerificationClaim(
                    claim_kind=kind,
                    status=_S.INCONCLUSIVE,
                    level=_L.NUMERICAL,
                    details="solver status without an exact certificate is not evidence",
                )
            )
        return build(outcome=MathOutcome.UNKNOWN, claims=tuple(claims))

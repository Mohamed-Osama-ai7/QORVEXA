"""Tests for strict mathematical result invariants, evidence binding and immutability (S1)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from qorvexa.contracts.solver_runtime import (
    CANONICALIZED_INPUT_VALUES,
    FarkasEvidence,
    GapEvidence,
    LoweringEvidence,
    MathOutcome,
    OptimalityTier,
    RoundingSummary,
    SolverRuntimeResult,
    SolverTermination,
    UnboundedEvidence,
    VerificationClaim,
    VerificationClaimKind,
    VerificationLevel,
    VerificationStatus,
    parse_rational,
)

K = VerificationClaimKind
SHA_A = "a" * 64
SHA_B = "b" * 64
IR = "c" * 64


def claim(
    kind: VerificationClaimKind,
    level: VerificationLevel = VerificationLevel.NUMERICAL,
    status: VerificationStatus = VerificationStatus.VERIFIED,
) -> VerificationClaim:
    return VerificationClaim(
        claim_kind=kind,
        status=status,
        level=level,
        exactness_scope=(
            CANONICALIZED_INPUT_VALUES if level == VerificationLevel.EXACT_RATIONAL else None
        ),
    )


def exact(kind: VerificationClaimKind) -> VerificationClaim:
    return claim(kind, VerificationLevel.EXACT_RATIONAL)


def lowering(n: int = 2, m: int = 1, sha: str = SHA_A) -> LoweringEvidence:
    return LoweringEvidence(
        model_sha256=sha,
        ir_sha256=IR,
        compiler_version="s3-test-1",
        verifier_version="none-yet",
        num_variables=n,
        num_rows=m,
        rounding=RoundingSummary(
            coefficients_total=5,
            coefficients_rounded=2,
            subnormal_count=0,
            max_abs_rounding_error="1/1000000000000000000000",
        ),
    )


LOWER = exact(K.MODEL_LOWERING_VALIDATED)


def solved(**over: Any) -> SolverRuntimeResult:
    kw: dict[str, Any] = {
        "outcome": MathOutcome.SOLVED,
        "solver_termination": SolverTermination.OPTIMAL,
        "objective_value": 4.0,
        "primal_solution": {"x0": 1.0, "x1": 2.0},
        "claims": [claim(K.PRIMAL_FEASIBILITY), LOWER],
        "lowering_evidence": lowering(),
    }
    kw.update(over)
    return SolverRuntimeResult(**kw)


def farkas(sha: str = SHA_A, m: int = 1) -> FarkasEvidence:
    return FarkasEvidence(
        model_sha256=sha,
        multipliers={f"r{i}": ("1" if i == 0 else "0") for i in range(m)},
        certificate_value="3/2",
    )


def infeasible(**over: Any) -> SolverRuntimeResult:
    kw: dict[str, Any] = {
        "outcome": MathOutcome.INFEASIBLE,
        "solver_termination": SolverTermination.INFEASIBLE,
        "claims": [exact(K.INFEASIBILITY), LOWER],
        "lowering_evidence": lowering(),
        "infeasibility_evidence": farkas(),
    }
    kw.update(over)
    return SolverRuntimeResult(**kw)


def unb_evidence(sha: str = SHA_A) -> UnboundedEvidence:
    return UnboundedEvidence(
        model_sha256=sha,
        feasible_anchor={"x0": "0", "x1": "0"},
        improving_ray={"x0": "1", "x1": "0"},
        objective_slope_min_form="-1",
    )


def unbounded(**over: Any) -> SolverRuntimeResult:
    kw: dict[str, Any] = {
        "outcome": MathOutcome.UNBOUNDED,
        "solver_termination": SolverTermination.UNBOUNDED,
        "claims": [exact(K.UNBOUNDEDNESS), exact(K.PRIMAL_FEASIBILITY), LOWER],
        "lowering_evidence": lowering(),
        "unbounded_evidence": unb_evidence(),
    }
    kw.update(over)
    return SolverRuntimeResult(**kw)


def gap(sha: str = SHA_A, **over: Any) -> GapEvidence:
    kw: dict[str, Any] = {
        "model_sha256": sha,
        "policy_id": "gap-policy-v1",
        "primal_objective": 10.0,
        "dual_objective": 9.999999,
        "absolute_gap": 1e-6,
        "relative_gap": 1e-7,
        "absolute_tolerance": 1e-6,
        "relative_tolerance": 1e-6,
    }
    kw.update(over)
    return GapEvidence(**kw)


class TestSolvedInvariants:
    def test_valid_solved_result(self) -> None:
        r = solved()
        assert r.outcome == MathOutcome.SOLVED
        assert r.optimality_tier == OptimalityTier.NONE

    def test_requires_verified_primal_feasibility(self) -> None:
        with pytest.raises(ValidationError, match="VERIFIED PRIMAL_FEASIBILITY"):
            solved(claims=[LOWER])
        with pytest.raises(ValidationError, match="VERIFIED PRIMAL_FEASIBILITY"):
            solved(
                claims=[
                    claim(K.PRIMAL_FEASIBILITY, status=VerificationStatus.REFUTED),
                    LOWER,
                ]
            )

    def test_requires_primal_solution_and_objective(self) -> None:
        with pytest.raises(ValidationError, match="non-null primalSolution"):
            solved(primal_solution=None)
        with pytest.raises(ValidationError, match="finite objectiveValue"):
            solved(objective_value=None)

    def test_requires_lowering_claim_and_evidence(self) -> None:
        with pytest.raises(ValidationError, match="MODEL_LOWERING_VALIDATED"):
            solved(claims=[claim(K.PRIMAL_FEASIBILITY)], lowering_evidence=None)
        with pytest.raises(ValidationError, match="requires loweringEvidence"):
            solved(lowering_evidence=None)
        with pytest.raises(ValidationError, match="requires a VERIFIED MODEL_LOWERING"):
            solved(claims=[claim(K.PRIMAL_FEASIBILITY)])

    def test_lowering_claim_must_be_exact_level(self) -> None:
        with pytest.raises(ValidationError, match="EXACT_RATIONAL level"):
            solved(claims=[claim(K.PRIMAL_FEASIBILITY), claim(K.MODEL_LOWERING_VALIDATED)])

    def test_refuted_lowering_claim_does_not_count(self) -> None:
        refuted = claim(
            K.MODEL_LOWERING_VALIDATED,
            VerificationLevel.EXACT_RATIONAL,
            VerificationStatus.REFUTED,
        )
        with pytest.raises(ValidationError, match="loweringEvidence requires a VERIFIED"):
            solved(claims=[claim(K.PRIMAL_FEASIBILITY), refuted])


class TestD1ContradictoryClaims:
    def test_solved_cannot_carry_verified_infeasibility(self) -> None:
        with pytest.raises(ValidationError, match="Contradictory claims"):
            solved(
                claims=[claim(K.PRIMAL_FEASIBILITY), LOWER, exact(K.INFEASIBILITY)],
                infeasibility_evidence=farkas(),
            )

    def test_solved_cannot_carry_numerical_infeasibility_claim(self) -> None:
        with pytest.raises(ValidationError, match="Contradictory claims"):
            solved(claims=[claim(K.PRIMAL_FEASIBILITY), LOWER, claim(K.INFEASIBILITY)])

    def test_infeasible_cannot_carry_verified_primal_feasibility(self) -> None:
        with pytest.raises(ValidationError, match="Contradictory claims"):
            infeasible(claims=[exact(K.INFEASIBILITY), LOWER, claim(K.PRIMAL_FEASIBILITY)])

    def test_infeasible_and_unboundedness_conflict(self) -> None:
        with pytest.raises(ValidationError, match="Contradictory claims"):
            infeasible(
                claims=[exact(K.INFEASIBILITY), LOWER, exact(K.UNBOUNDEDNESS)],
                unbounded_evidence=unb_evidence(),
            )

    def test_unknown_with_infeasibility_and_primal_feasibility_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Contradictory claims"):
            SolverRuntimeResult(
                outcome=MathOutcome.UNKNOWN,
                solver_termination=SolverTermination.OPTIMAL,
                claims=[claim(K.INFEASIBILITY), claim(K.PRIMAL_FEASIBILITY)],
            )

    def test_infeasible_conflicts_with_refuted_infeasibility_claim(self) -> None:
        refuted = claim(K.INFEASIBILITY, status=VerificationStatus.REFUTED)
        with pytest.raises(ValidationError, match="REFUTED INFEASIBILITY"):
            infeasible(claims=[exact(K.INFEASIBILITY), LOWER, refuted])

    def test_duplicate_claims_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate claim"):
            solved(claims=[claim(K.PRIMAL_FEASIBILITY), claim(K.PRIMAL_FEASIBILITY), LOWER])


class TestD2FiniteAndDimensions:
    def test_nan_and_inf_rejected_everywhere(self) -> None:
        nan, inf = float("nan"), float("inf")
        with pytest.raises(ValidationError):
            solved(primal_solution={"x0": nan, "x1": 1.0})
        with pytest.raises(ValidationError):
            solved(primal_solution={"x0": inf, "x1": 1.0})
        with pytest.raises(ValidationError):
            solved(objective_value=inf)
        with pytest.raises(ValidationError):
            solved(objective_value=nan)
        with pytest.raises(ValidationError):
            solved(dual_solution={"r0": -inf})
        with pytest.raises(ValidationError):
            VerificationClaim(
                claim_kind=K.PRIMAL_FEASIBILITY,
                status=VerificationStatus.VERIFIED,
                level=VerificationLevel.NUMERICAL,
                residual=nan,
            )
        with pytest.raises(ValidationError):
            gap(primal_objective=inf)

    def test_json_non_finite_tokens_rejected(self) -> None:
        payload = solved().model_dump_json(by_alias=True)
        bad = payload.replace('"x0":1.0', '"x0":NaN')
        assert bad != payload
        with pytest.raises(ValidationError):
            SolverRuntimeResult.model_validate_json(bad)

    def test_empty_primal_invalid_when_model_has_variables(self) -> None:
        with pytest.raises(ValidationError, match="primalSolution has 0 entries"):
            solved(primal_solution={})

    def test_empty_primal_valid_for_zero_variable_model(self) -> None:
        r = solved(
            primal_solution={},
            lowering_evidence=lowering(n=0, m=0),
            objective_value=3.0,
        )
        assert len(r.primal_solution or {}) == 0

    def test_wrong_dimensions_rejected(self) -> None:
        with pytest.raises(ValidationError, match="primalSolution has 3 entries"):
            solved(primal_solution={"a": 1.0, "b": 1.0, "c": 1.0})
        with pytest.raises(ValidationError, match="dualSolution length"):
            solved(dual_solution={"r0": 1.0, "r1": 2.0})
        with pytest.raises(ValidationError, match="unboundedRay length"):
            solved(unbounded_ray={"x0": 1.0})
        with pytest.raises(ValidationError, match="infeasibilityRay length"):
            solved(infeasibility_ray={"r0": 1.0, "r1": 1.0})

    def test_negative_dimensions_rejected(self) -> None:
        with pytest.raises(ValidationError):
            lowering(n=-1)
        with pytest.raises(ValidationError):
            lowering(m=-1)

    def test_blank_vector_key_rejected(self) -> None:
        with pytest.raises(ValidationError, match="non-empty strings"):
            solved(primal_solution={"": 1.0, "x1": 1.0})


class TestD3DeepImmutability:
    def test_attribute_assignment_rejected(self) -> None:
        r = solved()
        with pytest.raises(ValidationError):
            r.outcome = MathOutcome.UNKNOWN  # type: ignore[misc]
        with pytest.raises(ValidationError):
            r.lowering_evidence = None  # type: ignore[misc]

    def test_vector_is_read_only(self) -> None:
        r = solved()
        assert r.primal_solution is not None
        with pytest.raises(TypeError):
            r.primal_solution["x0"] = 99.0  # type: ignore[index]
        with pytest.raises(TypeError):
            del r.primal_solution["x0"]  # type: ignore[attr-defined]

    def test_caller_dict_mutation_does_not_leak(self) -> None:
        src = {"x0": 1.0, "x1": 2.0}
        r = solved(primal_solution=src)
        src["x0"] = 1e9
        src["evil"] = 1.0
        assert dict(r.primal_solution or {}) == {"x0": 1.0, "x1": 2.0}

    def test_shared_reference_between_fields_is_severed(self) -> None:
        shared = {"x0": 1.0, "x1": 2.0}
        r = solved(primal_solution=shared, unbounded_ray=shared)
        assert r.primal_solution is not r.unbounded_ray
        shared["x0"] = -5.0
        assert (r.primal_solution or {})["x0"] == 1.0
        assert (r.unbounded_ray or {})["x0"] == 1.0

    def test_claims_are_tuple_and_caller_list_mutation_does_not_leak(self) -> None:
        claims = [claim(K.PRIMAL_FEASIBILITY), LOWER]
        r = solved(claims=claims)
        claims.append(exact(K.INFEASIBILITY))
        assert isinstance(r.claims, tuple)
        assert len(r.claims) == 2

    def test_nested_models_are_frozen(self) -> None:
        r = solved()
        assert r.lowering_evidence is not None
        with pytest.raises(ValidationError):
            r.lowering_evidence.num_variables = 99  # type: ignore[misc]
        with pytest.raises(ValidationError):
            r.lowering_evidence.rounding.coefficients_total = 0  # type: ignore[misc]
        with pytest.raises(ValidationError):
            r.claims[0].status = VerificationStatus.REFUTED  # type: ignore[misc]

    def test_evidence_vectors_read_only_and_copied(self) -> None:
        anchor = {"x0": "0", "x1": "0"}
        ev = UnboundedEvidence(
            model_sha256=SHA_A,
            feasible_anchor=anchor,
            improving_ray={"x0": "1", "x1": "0"},
            objective_slope_min_form="-1",
        )
        anchor["x0"] = "7"
        assert ev.feasible_anchor["x0"] == "0"
        with pytest.raises(TypeError):
            ev.improving_ray["x0"] = "5"  # type: ignore[index]

    def test_metadata_read_only(self) -> None:
        r = solved(metadata={"k": "v"})
        with pytest.raises(TypeError):
            r.metadata["k"] = "other"  # type: ignore[index]

    def test_model_copy_update_refused(self) -> None:
        r = solved()
        with pytest.raises(TypeError, match="bypasses validation"):
            r.model_copy(update={"outcome": MathOutcome.UNKNOWN})
        assert r.model_copy().outcome == MathOutcome.SOLVED

    def test_serialization_round_trip(self) -> None:
        r = solved(dual_solution={"r0": 0.5}, metadata={"a": "b"})
        data = json.loads(r.model_dump_json(by_alias=True))
        assert data["primalSolution"] == {"x0": 1.0, "x1": 2.0}
        r2 = SolverRuntimeResult.model_validate(data)
        assert r2 == r
        r3 = SolverRuntimeResult.model_validate(r.model_dump(by_alias=True))
        assert r3 == r


class TestD4RawCandidateVsExactEvidence:
    def test_valid_exact_unbounded(self) -> None:
        assert unbounded().outcome == MathOutcome.UNBOUNDED

    def test_unbounded_requires_exact_evidence_object(self) -> None:
        with pytest.raises(ValidationError, match="unboundedEvidence must be present"):
            unbounded(unbounded_evidence=None)

    def test_evidence_without_claim_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unboundedEvidence must be present"):
            solved(unbounded_evidence=unb_evidence())

    def test_numerical_ray_alone_cannot_produce_unbounded(self) -> None:
        with pytest.raises(ValidationError, match="EXACT_RATIONAL VERIFIED UNBOUNDEDNESS"):
            SolverRuntimeResult(
                outcome=MathOutcome.UNBOUNDED,
                solver_termination=SolverTermination.UNBOUNDED,
                unbounded_ray={"x0": 1.0, "x1": 0.0},
                claims=[claim(K.UNBOUNDEDNESS), LOWER],
                lowering_evidence=lowering(),
            )

    def test_ray_without_exact_anchor_claim_rejected(self) -> None:
        with pytest.raises(ValidationError, match="feasible anchor"):
            unbounded(claims=[exact(K.UNBOUNDEDNESS), LOWER])

    def test_zero_ray_rejected(self) -> None:
        with pytest.raises(ValidationError, match="improvingRay must be nonzero"):
            UnboundedEvidence(
                model_sha256=SHA_A,
                feasible_anchor={"x0": "0"},
                improving_ray={"x0": "0"},
                objective_slope_min_form="-1",
            )

    def test_non_improving_ray_rejected(self) -> None:
        for slope in ("0", "1", "1/2"):
            with pytest.raises(ValidationError, match="must be < 0"):
                UnboundedEvidence(
                    model_sha256=SHA_A,
                    feasible_anchor={"x0": "0"},
                    improving_ray={"x0": "1"},
                    objective_slope_min_form=slope,
                )

    def test_anchor_and_ray_must_cover_same_variables(self) -> None:
        with pytest.raises(ValidationError, match="same variables"):
            UnboundedEvidence(
                model_sha256=SHA_A,
                feasible_anchor={"x0": "0"},
                improving_ray={"x1": "1"},
                objective_slope_min_form="-1",
            )

    def test_unbounded_conflicts_with_refuted_claim(self) -> None:
        refuted = claim(K.UNBOUNDEDNESS, status=VerificationStatus.REFUTED)
        with pytest.raises(ValidationError, match="REFUTED UNBOUNDEDNESS"):
            unbounded(claims=[exact(K.UNBOUNDEDNESS), exact(K.PRIMAL_FEASIBILITY), LOWER, refuted])

    def test_infeasible_valid_exact(self) -> None:
        assert infeasible().outcome == MathOutcome.INFEASIBLE

    def test_numerical_farkas_cannot_produce_infeasible(self) -> None:
        with pytest.raises(ValidationError, match="EXACT_RATIONAL VERIFIED INFEASIBILITY"):
            SolverRuntimeResult(
                outcome=MathOutcome.INFEASIBLE,
                solver_termination=SolverTermination.INFEASIBLE,
                claims=[claim(K.INFEASIBILITY), LOWER],
                lowering_evidence=lowering(),
            )

    def test_numerical_farkas_candidate_is_unknown(self) -> None:
        r = SolverRuntimeResult(
            outcome=MathOutcome.UNKNOWN,
            solver_termination=SolverTermination.INFEASIBLE,
            claims=[claim(K.INFEASIBILITY)],
            infeasibility_ray={"r0": 1.0},
        )
        assert r.outcome == MathOutcome.UNKNOWN

    def test_farkas_requires_nonzero_multipliers_and_positive_d0(self) -> None:
        with pytest.raises(ValidationError, match="nonzero entry"):
            FarkasEvidence(model_sha256=SHA_A, multipliers={"r0": "0"}, certificate_value="1")
        for d0 in ("0", "-1/2"):
            with pytest.raises(ValidationError, match="must be > 0"):
                FarkasEvidence(model_sha256=SHA_A, multipliers={"r0": "1"}, certificate_value=d0)

    def test_farkas_multiplier_count_must_match_rows(self) -> None:
        with pytest.raises(ValidationError, match="multipliers do not match"):
            infeasible(infeasibility_evidence=farkas(m=2))

    def test_rational_strings_must_be_canonical(self) -> None:
        for bad in ("1/0", "2/4", "+1", "0.5", "1e3", " 1", "01", "-0", "1/-2", "abc", ""):
            with pytest.raises(ValueError, match="rational"):
                parse_rational(bad)
        for ok in ("0", "-3", "7/9", "-1/3", "100"):
            parse_rational(ok)

    def test_exact_optimality_claim_not_supported(self) -> None:
        with pytest.raises(ValidationError, match="Exact optimality proofs are not supported"):
            solved(
                claims=[claim(K.PRIMAL_FEASIBILITY), LOWER, exact(K.DUAL_GAP)],
            )


class TestD5LoweringEvidence:
    def test_identity_fields_validated(self) -> None:
        with pytest.raises(ValidationError):
            lowering(sha="XYZ")
        with pytest.raises(ValidationError):
            lowering(sha="A" * 64)
        with pytest.raises(ValidationError):
            LoweringEvidence(
                model_sha256=SHA_A,
                ir_sha256="short",
                compiler_version="v",
                verifier_version="v",
                num_variables=1,
                num_rows=1,
                rounding=lowering().rounding,
            )

    def test_versions_required(self) -> None:
        with pytest.raises(ValidationError):
            LoweringEvidence(
                model_sha256=SHA_A,
                ir_sha256=IR,
                compiler_version="",
                verifier_version="v",
                num_variables=1,
                num_rows=1,
                rounding=lowering().rounding,
            )

    def test_exactness_scope_fixed(self) -> None:
        assert lowering().exactness_scope == CANONICALIZED_INPUT_VALUES
        with pytest.raises(ValidationError):
            LoweringEvidence(
                model_sha256=SHA_A,
                ir_sha256=IR,
                compiler_version="v",
                verifier_version="v",
                exactness_scope="decimal_as_authored",
                num_variables=1,
                num_rows=1,
                rounding=lowering().rounding,
            )

    def test_rounding_summary_consistency(self) -> None:
        with pytest.raises(ValidationError, match="exceeds coefficientsTotal"):
            RoundingSummary(
                coefficients_total=1,
                coefficients_rounded=2,
                subnormal_count=0,
                max_abs_rounding_error="0",
            )
        with pytest.raises(ValidationError, match="must be 0 when no coefficient was rounded"):
            RoundingSummary(
                coefficients_total=1,
                coefficients_rounded=0,
                subnormal_count=0,
                max_abs_rounding_error="1/10",
            )
        with pytest.raises(ValidationError, match="non-negative"):
            RoundingSummary(
                coefficients_total=1,
                coefficients_rounded=1,
                subnormal_count=0,
                max_abs_rounding_error="-1/10",
            )
        with pytest.raises(ValidationError, match="subnormalCount exceeds"):
            RoundingSummary(
                coefficients_total=1,
                coefficients_rounded=1,
                subnormal_count=2,
                max_abs_rounding_error="1/10",
            )

    def test_evidence_bound_to_same_model(self) -> None:
        with pytest.raises(ValidationError, match="different model"):
            infeasible(infeasibility_evidence=farkas(sha=SHA_B))
        with pytest.raises(ValidationError, match="different model"):
            unbounded(unbounded_evidence=unb_evidence(sha=SHA_B))
        with pytest.raises(ValidationError, match="different model"):
            solved(
                optimality_tier=OptimalityTier.GAP_NUMERICAL,
                gap_evidence=gap(sha=SHA_B),
                claims=[claim(K.PRIMAL_FEASIBILITY), claim(K.DUAL_GAP), LOWER],
            )

    def test_structured_evidence_requires_lowering_identity(self) -> None:
        with pytest.raises(ValidationError):
            SolverRuntimeResult(
                outcome=MathOutcome.UNKNOWN,
                solver_termination=SolverTermination.OPTIMAL,
                gap_evidence=gap(),
            )

    def test_extra_fields_forbidden_on_evidence(self) -> None:
        with pytest.raises(ValidationError):
            LoweringEvidence.model_validate({**lowering().model_dump(), "bogus": 1})


class TestGapNumerical:
    def _ok(self, **over: Any) -> SolverRuntimeResult:
        kw: dict[str, Any] = {
            "optimality_tier": OptimalityTier.GAP_NUMERICAL,
            "gap_evidence": gap(),
            "claims": [claim(K.PRIMAL_FEASIBILITY), claim(K.DUAL_GAP), LOWER],
        }
        kw.update(over)
        return solved(**kw)

    def test_valid_gap_numerical(self) -> None:
        r = self._ok()
        assert r.optimality_tier == OptimalityTier.GAP_NUMERICAL
        assert r.outcome == MathOutcome.SOLVED

    def test_requires_gap_evidence_and_claim(self) -> None:
        with pytest.raises(ValidationError, match="requires gapEvidence"):
            self._ok(gap_evidence=None)
        with pytest.raises(ValidationError, match="requires gapEvidence"):
            self._ok(claims=[claim(K.PRIMAL_FEASIBILITY), LOWER])

    def test_gap_evidence_requires_tier(self) -> None:
        with pytest.raises(ValidationError, match="only valid with optimalityTier"):
            solved(
                gap_evidence=gap(),
                claims=[claim(K.PRIMAL_FEASIBILITY), claim(K.DUAL_GAP), LOWER],
            )

    def test_tier_only_valid_for_solved(self) -> None:
        with pytest.raises(ValidationError, match="only valid when outcome is SOLVED"):
            SolverRuntimeResult(
                outcome=MathOutcome.UNKNOWN,
                solver_termination=SolverTermination.OPTIMAL,
                optimality_tier=OptimalityTier.GAP_NUMERICAL,
            )

    def test_gap_arithmetic_must_reconcile(self) -> None:
        with pytest.raises(ValidationError, match="does not match"):
            gap(absolute_gap=0.5)

    def test_gap_beyond_both_tolerances_rejected(self) -> None:
        with pytest.raises(ValidationError, match="exceeds both"):
            gap(
                primal_objective=10.0,
                dual_objective=9.0,
                absolute_gap=1.0,
                relative_gap=0.1,
            )

    def test_tolerances_and_gaps_validated(self) -> None:
        with pytest.raises(ValidationError):
            gap(absolute_tolerance=0.0)
        with pytest.raises(ValidationError):
            gap(relative_tolerance=-1.0)
        with pytest.raises(ValidationError):
            gap(absolute_gap=-1e-6)
        with pytest.raises(ValidationError):
            gap(policy_id="")


class TestTerminationIsMetadataOnly:
    @pytest.mark.parametrize(
        "term",
        [
            SolverTermination.TIME_LIMIT,
            SolverTermination.ITERATION_LIMIT,
            SolverTermination.CRASHED,
            SolverTermination.SUPERVISOR_KILLED,
            SolverTermination.INFEASIBLE,
            SolverTermination.NUMERICAL_ERROR,
        ],
    )
    def test_verified_feasible_iterate_is_solved(self, term: SolverTermination) -> None:
        assert solved(solver_termination=term).outcome == MathOutcome.SOLVED

    @pytest.mark.parametrize(
        "term",
        [
            SolverTermination.TIME_LIMIT,
            SolverTermination.ITERATION_LIMIT,
            SolverTermination.OPTIMAL,
            SolverTermination.CRASHED,
        ],
    )
    def test_valid_exact_certificate_survives_any_termination(
        self, term: SolverTermination
    ) -> None:
        assert infeasible(solver_termination=term).outcome == MathOutcome.INFEASIBLE
        assert unbounded(solver_termination=term).outcome == MathOutcome.UNBOUNDED

    def test_infeasible_termination_without_certificate_is_not_infeasible(self) -> None:
        with pytest.raises(ValidationError, match="EXACT_RATIONAL VERIFIED INFEASIBILITY"):
            SolverRuntimeResult(
                outcome=MathOutcome.INFEASIBLE,
                solver_termination=SolverTermination.INFEASIBLE,
                claims=[LOWER],
                lowering_evidence=lowering(),
            )


class TestFailedInvariants:
    def test_failed_outcome_requires_details(self) -> None:
        for details in (None, "   "):
            with pytest.raises(ValidationError, match="requires non-empty failureDetails"):
                SolverRuntimeResult(
                    outcome=MathOutcome.FAILED,
                    solver_termination=SolverTermination.CRASHED,
                    failure_details=details,
                )
        res = SolverRuntimeResult(
            outcome=MathOutcome.FAILED,
            solver_termination=SolverTermination.CRASHED,
            failure_details="Simplex pivot singularity",
        )
        assert res.failure_details == "Simplex pivot singularity"

    def test_non_failed_outcome_with_failure_details_raises(self) -> None:
        with pytest.raises(ValidationError, match="failureDetails must be null"):
            SolverRuntimeResult(
                outcome=MathOutcome.UNKNOWN,
                solver_termination=SolverTermination.TIME_LIMIT,
                failure_details="Some details",
            )

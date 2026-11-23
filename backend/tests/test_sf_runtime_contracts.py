"""Tests for Solver Fabric runtime contracts and execution lifecycle schemas (S1)."""

import pytest
from pydantic import ValidationError

from qorvexa.contracts.solver_runtime import (
    CANONICALIZED_INPUT_VALUES,
    JobState,
    MathOutcome,
    OptimalityTier,
    SolverRuntimeJob,
    SolverRuntimeResult,
    SolverTermination,
    VerificationClaim,
    VerificationClaimKind,
    VerificationLevel,
    VerificationStatus,
)


class TestSolverRuntimeEnums:
    """Verifies complete enumeration sets for S1 runtime contracts."""

    def test_job_state_members(self) -> None:
        expected = {
            "QUEUED",
            "COMPILING",
            "RUNNING",
            "VERIFYING",
            "COMPLETED",
            "FAILED",
            "CANCELLED",
        }
        assert {s.value for s in JobState} == expected

    def test_solver_termination_members(self) -> None:
        expected = {
            "OPTIMAL",
            "INFEASIBLE",
            "UNBOUNDED",
            "UNBOUNDED_OR_INFEASIBLE",
            "TIME_LIMIT",
            "ITERATION_LIMIT",
            "NUMERICAL_ERROR",
            "INTERRUPTED",
            "SUPERVISOR_KILLED",
            "CRASHED",
            "MEMORY_LIMIT",
            "NOT_RUN",
            "OTHER",
        }
        assert {t.value for t in SolverTermination} == expected

    def test_math_outcome_members(self) -> None:
        expected = {"SOLVED", "INFEASIBLE", "UNBOUNDED", "UNKNOWN", "FAILED"}
        assert {o.value for o in MathOutcome} == expected

    def test_verification_claim_kinds(self) -> None:
        expected = {
            "MODEL_LOWERING_VALIDATED",
            "PRIMAL_FEASIBILITY",
            "DUAL_GAP",
            "INFEASIBILITY",
            "UNBOUNDEDNESS",
        }
        assert {k.value for k in VerificationClaimKind} == expected

    def test_verification_status(self) -> None:
        expected = {"VERIFIED", "REFUTED", "INCONCLUSIVE", "NOT_ATTEMPTED"}
        assert {s.value for s in VerificationStatus} == expected

    def test_verification_level(self) -> None:
        expected = {"NUMERICAL", "EXACT_RATIONAL"}
        assert {lvl.value for lvl in VerificationLevel} == expected

    def test_optimality_tier(self) -> None:
        expected = {"NONE", "GAP_NUMERICAL"}
        assert {tier.value for tier in OptimalityTier} == expected


class TestVerificationClaimValidation:
    """Tests VerificationClaim exactness scope requirements and constraints."""

    def test_valid_exact_claim(self) -> None:
        claim = VerificationClaim(
            claim_kind=VerificationClaimKind.PRIMAL_FEASIBILITY,
            status=VerificationStatus.VERIFIED,
            level=VerificationLevel.EXACT_RATIONAL,
            exactness_scope=CANONICALIZED_INPUT_VALUES,
        )
        assert claim.exactness_scope == CANONICALIZED_INPUT_VALUES
        assert claim.level == VerificationLevel.EXACT_RATIONAL

    def test_exact_claim_missing_scope_raises(self) -> None:
        with pytest.raises(ValidationError, match="Exact rational claims require exactnessScope"):
            VerificationClaim(
                claim_kind=VerificationClaimKind.INFEASIBILITY,
                status=VerificationStatus.VERIFIED,
                level=VerificationLevel.EXACT_RATIONAL,
                exactness_scope=None,
            )

    def test_exact_claim_invalid_scope_raises(self) -> None:
        with pytest.raises(ValidationError, match="Unsupported exactnessScope"):
            VerificationClaim(
                claim_kind=VerificationClaimKind.INFEASIBILITY,
                status=VerificationStatus.VERIFIED,
                level=VerificationLevel.EXACT_RATIONAL,
                exactness_scope="decimal_as_authored",
            )

    def test_numerical_claim_with_scope_raises(self) -> None:
        with pytest.raises(
            ValidationError, match="Numerical claims must not specify an exactnessScope"
        ):
            VerificationClaim(
                claim_kind=VerificationClaimKind.PRIMAL_FEASIBILITY,
                status=VerificationStatus.VERIFIED,
                level=VerificationLevel.NUMERICAL,
                exactness_scope=CANONICALIZED_INPUT_VALUES,
            )

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(
            ValidationError, match=r"extra_forbidden|Extra inputs are not permitted"
        ):
            VerificationClaim(
                claim_kind=VerificationClaimKind.PRIMAL_FEASIBILITY,
                status=VerificationStatus.VERIFIED,
                level=VerificationLevel.NUMERICAL,
                unauthorized_extra_field=True,  # type: ignore[call-arg]
            )

    def test_camel_case_aliases(self) -> None:
        claim = VerificationClaim.model_validate(
            {
                "claimKind": "PRIMAL_FEASIBILITY",
                "status": "VERIFIED",
                "level": "EXACT_RATIONAL",
                "exactnessScope": "canonicalized_input_values",
                "residual": 0.0,
            }
        )
        assert claim.claim_kind == VerificationClaimKind.PRIMAL_FEASIBILITY
        assert claim.exactness_scope == CANONICALIZED_INPUT_VALUES


def _result(outcome: MathOutcome = MathOutcome.UNKNOWN) -> SolverRuntimeResult:
    return SolverRuntimeResult(outcome=outcome, solver_termination=SolverTermination.OTHER)


def _failed_result() -> SolverRuntimeResult:
    return SolverRuntimeResult(
        outcome=MathOutcome.FAILED,
        solver_termination=SolverTermination.CRASHED,
        failure_details="worker crashed",
    )


class TestSolverRuntimeJobLifecycle:
    """SolverRuntimeJob consistency invariants and validated state transitions."""

    def test_completed_job_requires_result(self) -> None:
        with pytest.raises(ValidationError, match="COMPLETED job requires a non-null result"):
            SolverRuntimeJob(job_id="job-1", state=JobState.COMPLETED, result=None)

    def test_completed_job_with_error_raises(self) -> None:
        with pytest.raises(ValidationError, match="COMPLETED job must not have an errorMessage"):
            SolverRuntimeJob(
                job_id="job-1", state=JobState.COMPLETED, result=_result(), error_message="boom"
            )

    def test_completed_job_cannot_carry_failed_result(self) -> None:
        with pytest.raises(ValidationError, match="cannot carry a FAILED result"):
            SolverRuntimeJob(job_id="j", state=JobState.COMPLETED, result=_failed_result())

    def test_completed_job_with_unknown_result_is_valid(self) -> None:
        job = SolverRuntimeJob(job_id="j", state=JobState.COMPLETED, result=_result())
        assert job.state == JobState.COMPLETED

    def test_cancelled_job_must_have_null_result(self) -> None:
        with pytest.raises(ValidationError, match="CANCELLED job must have null result"):
            SolverRuntimeJob(job_id="job-2", state=JobState.CANCELLED, result=_result())
        assert SolverRuntimeJob(job_id="job-2", state=JobState.CANCELLED).result is None

    def test_active_job_cannot_have_result(self) -> None:
        for st in (JobState.QUEUED, JobState.COMPILING, JobState.RUNNING, JobState.VERIFYING):
            with pytest.raises(ValidationError, match="must not have a result attached"):
                SolverRuntimeJob(job_id="job-3", state=st, result=_result())

    def test_failed_job_validation(self) -> None:
        with pytest.raises(ValidationError, match="requires a non-empty errorMessage"):
            SolverRuntimeJob(job_id="j4", state=JobState.FAILED, result=None, error_message="")
        job = SolverRuntimeJob(
            job_id="j4", state=JobState.FAILED, result=None, error_message="compile error"
        )
        assert job.state == JobState.FAILED
        with pytest.raises(ValidationError, match="non-FAILED result outcome"):
            SolverRuntimeJob(job_id="j4", state=JobState.FAILED, result=_result())
        ok = SolverRuntimeJob(job_id="j4", state=JobState.FAILED, result=_failed_result())
        assert ok.result is not None

    def test_blank_job_id_rejected(self) -> None:
        for bad in ("", "   "):
            with pytest.raises(ValidationError):
                SolverRuntimeJob(job_id=bad, state=JobState.QUEUED)

    def test_job_is_frozen(self) -> None:
        job = SolverRuntimeJob(job_id="j", state=JobState.QUEUED)
        with pytest.raises(ValidationError):
            job.state = JobState.COMPLETED  # type: ignore[misc]
        with pytest.raises(TypeError, match="bypasses validation"):
            job.model_copy(update={"state": JobState.COMPLETED})

    def test_happy_path_transitions(self) -> None:
        job = SolverRuntimeJob(job_id="j", state=JobState.QUEUED)
        job = job.transition(JobState.COMPILING)
        job = job.transition(JobState.RUNNING)
        job = job.transition(JobState.VERIFYING)
        job = job.transition(JobState.COMPLETED, result=_result())
        assert job.state == JobState.COMPLETED

    @pytest.mark.parametrize(
        ("src", "dst"),
        [
            (JobState.QUEUED, JobState.RUNNING),
            (JobState.QUEUED, JobState.COMPLETED),
            (JobState.COMPILING, JobState.VERIFYING),
            (JobState.RUNNING, JobState.COMPLETED),
            (JobState.RUNNING, JobState.QUEUED),
            (JobState.VERIFYING, JobState.RUNNING),
        ],
    )
    def test_illegal_transitions_rejected(self, src: JobState, dst: JobState) -> None:
        job = SolverRuntimeJob(job_id="j", state=src)
        with pytest.raises(ValueError, match="Illegal job transition"):
            job.transition(dst, result=_result() if dst == JobState.COMPLETED else None)

    @pytest.mark.parametrize("terminal", [JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED])
    def test_terminal_states_are_final(self, terminal: JobState) -> None:
        if terminal == JobState.COMPLETED:
            job = SolverRuntimeJob(job_id="j", state=terminal, result=_result())
        elif terminal == JobState.FAILED:
            job = SolverRuntimeJob(job_id="j", state=terminal, error_message="x")
        else:
            job = SolverRuntimeJob(job_id="j", state=terminal)
        for dst in JobState:
            with pytest.raises(ValueError, match="Illegal job transition"):
                job.transition(dst)

    def test_transition_revalidates_target_state(self) -> None:
        job = SolverRuntimeJob(job_id="j", state=JobState.VERIFYING)
        with pytest.raises(ValidationError, match="requires a non-null result"):
            job.transition(JobState.COMPLETED)
        with pytest.raises(ValidationError, match="cannot carry a FAILED result"):
            job.transition(JobState.COMPLETED, result=_failed_result())
        failed = job.transition(JobState.FAILED, error_message="verifier crashed")
        assert failed.state == JobState.FAILED

    def test_active_state_can_be_cancelled_or_failed(self) -> None:
        for st in (JobState.QUEUED, JobState.COMPILING, JobState.RUNNING, JobState.VERIFYING):
            job = SolverRuntimeJob(job_id="j", state=st)
            assert job.transition(JobState.CANCELLED).state == JobState.CANCELLED
            assert job.transition(JobState.FAILED, error_message="e").state == JobState.FAILED

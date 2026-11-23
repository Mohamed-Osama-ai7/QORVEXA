"""End-to-end orchestration tests with a controlled fake adapter (no real solver)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from qorvexa.contracts.solver_runtime import (
    JobState,
    MathOutcome,
    OptimalityTier,
    SolverTermination,
    VerificationClaimKind,
    VerificationLevel,
    VerificationStatus,
)
from qorvexa.solver_fabric.orchestrator import LPOrchestrator
from qorvexa.solver_fabric.worker_contract import (
    WorkerFailure,
    WorkerFailureCode,
    WorkerRequest,
    WorkerResponse,
    WorkerStatus,
)

K = VerificationClaimKind
S = VerificationStatus
L = VerificationLevel


def var(i: str = "VAR-01") -> dict[str, Any]:
    return {"type": "variable_ref", "variableId": i}


def lit(v: Any) -> dict[str, Any]:
    return {"type": "literal", "dataType": "real", "value": v}


def binop(op: str, left: Any, right: Any) -> dict[str, Any]:
    return {"type": "binary_op", "operator": op, "left": left, "right": right}


def variable(i: str, lo: Any = 0, hi: Any = 3) -> dict[str, Any]:
    return {
        "id": i,
        "name": i,
        "symbol": i,
        "domain": "continuous",
        "lowerBound": lo,
        "upperBound": hi,
    }


def constraint(ast: Any, relation: str, bound: Any, upper: Any = None) -> dict[str, Any]:
    c: dict[str, Any] = {
        "id": "CON-01",
        "name": "c",
        "category": "capacity",
        "relation": relation,
        "expressionSummary": "s",
        "expressionAst": ast,
        "boundValue": bound,
    }
    if upper is not None:
        c["upperBoundValue"] = upper
    return c


def problem(
    variables: list[dict[str, Any]],
    objective: Any,
    cons: list[dict[str, Any]],
    direction: str = "minimize",
    **over: Any,
) -> dict[str, Any]:
    d: dict[str, Any] = {
        "title": "t",
        "problemDescription": "d",
        "problemType": "linear_programming",
        "timeHorizon": {"isSpecified": False},
        "solverHandoffNotes": {"recommendedSolverFamily": "lp"},
        "decisionVariables": variables,
        "objectives": [
            {
                "id": "OBJ-01",
                "direction": direction,
                "name": "o",
                "expressionSummary": "s",
                "expressionAst": objective,
            }
        ],
        "constraints": cons,
    }
    d.update(over)
    return d


def analytic(direction: str = "minimize") -> dict[str, Any]:
    # min x1 + 2 x2 ; 1 <= x1 + x2 <= 4 ; x in [0,3]^2  -> optimum 1 at (1,0), y = 1
    # max x1 + x2 over the same set -> optimum 4
    obj = binop("add", var("VAR-01"), binop("mul", lit(2), var("VAR-02")))
    if direction == "maximize":
        obj = binop("add", var("VAR-01"), var("VAR-02"))
    return problem(
        [variable("VAR-01"), variable("VAR-02")],
        obj,
        [constraint(binop("add", var("VAR-01"), var("VAR-02")), "range", 1, 4)],
        direction,
    )


def infeasible_doc() -> dict[str, Any]:  # x in [0,1], x >= 2
    return problem([variable("VAR-01", 0, 1)], var(), [constraint(var(), ">=", 2)])


def unbounded_doc() -> dict[str, Any]:  # min -x, x >= 0 unbounded above, x >= 1
    return problem(
        [variable("VAR-01", 0, None)],
        binop("mul", lit(-1), var()),
        [constraint(var(), ">=", 1)],
    )


class FakeAdapter:
    name = "fake"

    def __init__(self, reply: dict[str, Any] | Callable[[WorkerRequest], dict[str, Any]]) -> None:
        self.reply = reply
        self.requests: list[WorkerRequest] = []

    def solve(self, request: WorkerRequest) -> WorkerResponse:
        self.requests.append(request)
        extra = self.reply(request) if callable(self.reply) else self.reply
        base: dict[str, Any] = {
            "request_id": request.request_id,
            "model_sha256": request.model_sha256,
            "ir_sha256": request.ir_sha256,
            "status": WorkerStatus.FINISHED,
            "termination": SolverTermination.OPTIMAL,
            "solver_name": "fake",
        }
        base.update(extra)
        return WorkerResponse(**base)


class RaisingAdapter:
    name = "raising"

    def solve(self, request: WorkerRequest) -> WorkerResponse:
        raise RuntimeError("solver exploded")


def run(doc: dict[str, Any], adapter: Any):  # type: ignore[no-untyped-def]
    return LPOrchestrator(adapter).solve_json(json.dumps(doc), job_id="job-1")


def kinds(result: Any) -> dict[tuple[K, L], S]:
    return {(c.claim_kind, c.level): c.status for c in result.claims}


class TestSolved:
    def test_analytic_minimization(self) -> None:
        a = FakeAdapter(
            {"primal_solution": (1.0, 0.0), "dual_solution": (1.0,), "objective_value": 999.0}
        )
        out = run(analytic(), a)
        r = out.result
        assert out.job.state == JobState.COMPLETED and r is not None
        assert r.outcome == MathOutcome.SOLVED and r.optimality_tier == OptimalityTier.GAP_NUMERICAL
        assert r.objective_value == 1.0  # recomputed exactly, never the solver's 999
        assert r.gap_evidence is not None and r.gap_evidence.absolute_gap == 0.0
        assert kinds(r)[(K.DUAL_GAP, L.NUMERICAL)] == S.VERIFIED
        assert kinds(r)[(K.MODEL_LOWERING_VALIDATED, L.EXACT_RATIONAL)] == S.VERIFIED
        assert (K.DUAL_GAP, L.EXACT_RATIONAL) not in kinds(r)
        assert r.metadata["optimality_note"].startswith("GAP_NUMERICAL")

    def test_analytic_maximization(self) -> None:
        a = FakeAdapter({"primal_solution": (3.0, 1.0), "dual_solution": (-1.0,)})
        r = run(analytic("maximize"), a).result
        assert r is not None and r.outcome == MathOutcome.SOLVED and r.objective_value == 4.0
        assert r.metadata["objective_sense"] == "maximize"

    def test_request_is_bound_and_serializable(self) -> None:
        a = FakeAdapter({"primal_solution": (1.0, 0.0), "dual_solution": (1.0,)})
        out = run(analytic(), a)
        (req,) = a.requests
        assert req.model_sha256 == out.model_sha256 and req.ir_sha256 == out.ir_sha256
        assert req.request_id == "job-1"
        assert WorkerRequest.from_json_bytes(req.to_json_bytes()) == req
        r = out.result
        assert r is not None and r.lowering_evidence is not None
        assert r.lowering_evidence.model_sha256 == out.model_sha256
        assert r.lowering_evidence.ir_sha256 == out.ir_sha256

    def test_timeout_does_not_invalidate_a_verifiable_candidate(self) -> None:
        a = FakeAdapter(
            {
                "status": WorkerStatus.TIMED_OUT,
                "termination": SolverTermination.TIME_LIMIT,
                "primal_solution": (1.0, 0.0),
                "dual_solution": (1.0,),
            }
        )
        r = run(analytic(), a).result
        assert r is not None and r.outcome == MathOutcome.SOLVED
        assert r.solver_termination == SolverTermination.TIME_LIMIT


class TestNeverTrustTheSolver:
    def test_optimal_status_with_infeasible_primal_is_unknown(self) -> None:
        a = FakeAdapter({"primal_solution": (0.0, 0.0), "dual_solution": (1.0,)})
        out = run(analytic(), a)
        r = out.result
        assert r is not None and r.outcome == MathOutcome.UNKNOWN
        assert kinds(r)[(K.PRIMAL_FEASIBILITY, L.NUMERICAL)] == S.REFUTED
        assert out.job.state == JobState.COMPLETED

    def test_optimal_status_with_weak_dual_is_unknown(self) -> None:
        a = FakeAdapter({"primal_solution": (1.0, 0.0), "dual_solution": (0.0,)})
        r = run(analytic(), a).result
        assert r is not None and r.outcome == MathOutcome.UNKNOWN
        assert kinds(r)[(K.DUAL_GAP, L.NUMERICAL)] == S.INCONCLUSIVE
        assert r.gap_evidence is None and r.optimality_tier == OptimalityTier.NONE

    def test_optimal_status_without_vectors_is_unknown(self) -> None:
        r = run(analytic(), FakeAdapter({})).result
        assert r is not None and r.outcome == MathOutcome.UNKNOWN

    def test_wrong_length_vectors_are_ignored(self) -> None:
        r = run(
            analytic(), FakeAdapter({"primal_solution": (1.0,), "dual_solution": (1.0, 2.0)})
        ).result
        assert r is not None and r.outcome == MathOutcome.UNKNOWN
        assert "primal_solution_ignored" in r.metadata

    def test_infeasible_status_alone_is_not_evidence(self) -> None:
        a = FakeAdapter({"termination": SolverTermination.INFEASIBLE})
        r = run(analytic(), a).result
        assert r is not None and r.outcome == MathOutcome.UNKNOWN
        assert kinds(r)[(K.INFEASIBILITY, L.NUMERICAL)] == S.INCONCLUSIVE
        assert r.infeasibility_evidence is None

    def test_unbounded_status_alone_is_not_evidence(self) -> None:
        a = FakeAdapter({"termination": SolverTermination.UNBOUNDED})
        r = run(analytic(), a).result
        assert r is not None and r.outcome == MathOutcome.UNKNOWN

    def test_bogus_farkas_ray_is_not_accepted(self) -> None:
        a = FakeAdapter({"termination": SolverTermination.INFEASIBLE, "farkas_ray": (1.0,)})
        r = run(analytic(), a).result  # the model is feasible
        assert r is not None and r.outcome == MathOutcome.UNKNOWN


class TestInfeasible:
    def test_exact_farkas_from_solver_ray(self) -> None:
        a = FakeAdapter({"termination": SolverTermination.INFEASIBLE, "farkas_ray": (1.0,)})
        r = run(infeasible_doc(), a).result
        assert r is not None and r.outcome == MathOutcome.INFEASIBLE
        assert r.infeasibility_evidence is not None
        assert r.infeasibility_evidence.certificate_value == "1"
        assert kinds(r)[(K.INFEASIBILITY, L.EXACT_RATIONAL)] == S.VERIFIED
        assert r.metadata["certificate_source"] == "solver_farkas_ray"

    def test_own_exact_interval_conflict_without_solver_help(self) -> None:
        a = FakeAdapter({"termination": SolverTermination.OTHER})
        r = run(infeasible_doc(), a).result
        assert r is not None and r.outcome == MathOutcome.INFEASIBLE
        assert r.metadata["certificate_source"].startswith("interval_conflict:")

    def test_certificate_survives_worker_timeout_and_crash(self) -> None:
        for reply in (
            {"status": WorkerStatus.TIMED_OUT, "termination": SolverTermination.TIME_LIMIT},
            {
                "status": WorkerStatus.FAILED,
                "termination": SolverTermination.CRASHED,
                "failure": WorkerFailure(code=WorkerFailureCode.CRASHED, message="x"),
            },
        ):
            out = run(infeasible_doc(), FakeAdapter(reply))
            assert out.job.state == JobState.COMPLETED
            assert out.result is not None and out.result.outcome == MathOutcome.INFEASIBLE

    def test_numerical_only_infeasibility_is_unknown(self) -> None:
        # coupled rows: x>=1 and x<=0 -> a wrong (non-certifying) ray must not produce INFEASIBLE
        d = problem(
            [variable("VAR-01", None, None)],
            var(),
            [constraint(var(), ">=", 1)],
        )
        a = FakeAdapter({"termination": SolverTermination.INFEASIBLE, "farkas_ray": (0.5,)})
        r = run(d, a).result
        assert r is not None and r.outcome == MathOutcome.UNKNOWN


class TestUnbounded:
    def test_exact_anchor_and_ray(self) -> None:
        a = FakeAdapter(
            {
                "termination": SolverTermination.UNBOUNDED,
                "primal_solution": (1.0,),
                "primal_ray": (1.0,),
            }
        )
        r = run(unbounded_doc(), a).result
        assert r is not None and r.outcome == MathOutcome.UNBOUNDED
        ev = r.unbounded_evidence
        assert ev is not None and dict(ev.feasible_anchor) == {"VAR-01": "1"}
        assert dict(ev.improving_ray) == {"VAR-01": "1"} and ev.objective_slope_min_form == "-1"
        assert kinds(r)[(K.PRIMAL_FEASIBILITY, L.EXACT_RATIONAL)] == S.VERIFIED

    def test_ray_without_exact_anchor_is_unknown(self) -> None:
        a = FakeAdapter(
            {
                "termination": SolverTermination.UNBOUNDED,
                "primal_solution": (0.5,),
                "primal_ray": (1.0,),
            }
        )
        r = run(unbounded_doc(), a).result  # 0.5 violates x >= 1; clamp(0)=0 violates too
        assert r is not None and r.outcome == MathOutcome.UNKNOWN

    def test_non_improving_ray_is_unknown(self) -> None:
        a = FakeAdapter(
            {
                "termination": SolverTermination.UNBOUNDED,
                "primal_solution": (1.0,),
                "primal_ray": (-1.0,),
            }
        )
        r = run(unbounded_doc(), a).result
        assert r is not None and r.outcome == MathOutcome.UNKNOWN

    def test_bounded_problem_cannot_be_declared_unbounded(self) -> None:
        a = FakeAdapter(
            {
                "termination": SolverTermination.UNBOUNDED,
                "primal_solution": (1.0, 0.0),
                "primal_ray": (1.0, 0.0),
            }
        )
        r = run(analytic(), a).result
        assert r is not None and r.outcome == MathOutcome.UNKNOWN


class TestWorkerFailures:
    def test_timeout_without_certificate_is_unknown(self) -> None:
        a = FakeAdapter(
            {"status": WorkerStatus.TIMED_OUT, "termination": SolverTermination.TIME_LIMIT}
        )
        out = run(analytic(), a)
        assert out.job.state == JobState.COMPLETED
        assert out.result is not None and out.result.outcome == MathOutcome.UNKNOWN

    def test_worker_failure(self) -> None:
        a = FakeAdapter(
            {
                "status": WorkerStatus.FAILED,
                "termination": SolverTermination.CRASHED,
                "failure": WorkerFailure(code=WorkerFailureCode.SOLVER_ERROR, message="bad"),
            }
        )
        out = run(analytic(), a)
        assert out.job.state == JobState.FAILED
        assert out.result is not None and out.result.outcome == MathOutcome.FAILED
        assert "SOLVER_ERROR" in (out.result.failure_details or "")

    def test_adapter_exception_is_contained(self) -> None:
        out = run(analytic(), RaisingAdapter())
        assert out.job.state == JobState.FAILED
        assert out.result is not None and "solver exploded" in (out.result.failure_details or "")

    def test_adapter_returning_garbage_is_contained(self) -> None:
        class Garbage:
            name = "g"

            def solve(self, request: WorkerRequest) -> Any:
                return {"status": "OPTIMAL"}

        assert run(analytic(), Garbage()).job.state == JobState.FAILED

    @pytest.mark.parametrize("field", ["model_sha256", "ir_sha256", "request_id"])
    def test_binding_mismatch_rejected(self, field: str) -> None:
        value = "0" * 64 if field != "request_id" else "other-job"
        a = FakeAdapter({field: value, "primal_solution": (1.0, 0.0), "dual_solution": (1.0,)})
        out = run(analytic(), a)
        assert out.job.state == JobState.FAILED
        assert out.rejection is not None and out.rejection.code == "WORKER_BINDING_MISMATCH"


class TestIngressAndNormalizationRejections:
    def check(self, text: str, stage: str, code: str) -> None:
        a = FakeAdapter({})
        out = LPOrchestrator(a).solve_json(text, job_id="job-1")
        assert out.job.state == JobState.FAILED and a.requests == []
        assert out.rejection is not None
        assert (out.rejection.stage, out.rejection.code) == (stage, code)
        assert out.result is not None and out.result.outcome == MathOutcome.FAILED

    def test_duplicate_key(self) -> None:
        self.check('{"title":"a","title":"b"}', "ingress", "DUPLICATE_KEY")

    def test_non_finite_number(self) -> None:
        text = json.dumps(analytic()).replace('"upperBoundValue": 4', '"upperBoundValue": NaN')
        self.check(text, "ingress", "NON_FINITE_NUMBER")

    def test_client_verification_flag_is_not_trusted(self) -> None:
        d = analytic()
        d["mathematicallyVerified"] = True
        self.check(json.dumps(d), "ingress", "VERIFICATION_FLAG_NOT_TRUSTED")

    def test_comparison_node(self) -> None:
        cmp = {"type": "comparison", "operator": "<=", "left": var(), "right": lit(3)}
        d = problem([variable("VAR-01")], var(), [constraint(cmp, "<=", 1)])
        self.check(json.dumps(d), "compile", "COMPARISON_NODE_UNSUPPORTED")

    def test_missing_bound(self) -> None:
        d = problem([variable("VAR-01")], var(), [constraint(var(), "<=", None)])
        self.check(json.dumps(d), "compile", "BOUND_MISSING")

    def test_inverted_range(self) -> None:
        d = problem([variable("VAR-01")], var(), [constraint(var(), "range", 5, 1)])
        self.check(json.dumps(d), "compile", "RANGE_INVERTED")

    def test_ambiguous_bounds(self) -> None:
        d = problem([variable("VAR-01")], var(), [constraint(var(), "<=", 5, 7)])
        self.check(json.dumps(d), "compile", "CONSTRAINT_BOUND_FIELDS")

    def test_variable_bound_contradiction(self) -> None:
        d = problem([variable("VAR-01", 5, 1)], var(), [])
        self.check(json.dumps(d), "compile", "BOUND_CONTRADICTION_EXACT")

    def test_integer_variables_rejected(self) -> None:
        v = variable("VAR-01")
        v["domain"] = "integer"
        self.check(json.dumps(problem([v], var(), [])), "compile", "VARIABLE_DOMAIN_UNSUPPORTED")

    def test_no_objective(self) -> None:
        d = analytic()
        d["objectives"] = []
        self.check(json.dumps(d), "compile", "OBJECTIVE_COUNT")

    def test_nonlinear_expression(self) -> None:
        d = problem([variable("VAR-01")], binop("mul", var(), var()), [])
        out = LPOrchestrator(FakeAdapter({})).solve_json(json.dumps(d))
        assert out.rejection is not None and out.rejection.stage == "compile"

    def test_coefficient_overflow_is_a_typed_rejection(self) -> None:
        d = problem([variable("VAR-01")], binop("mul", lit(10**400), var()), [])
        out = LPOrchestrator(FakeAdapter({})).solve_json(json.dumps(d))
        assert out.job.state == JobState.FAILED and out.rejection is not None


class TestLifecycle:
    def test_adapter_called_exactly_once_and_job_completes(self) -> None:
        a = FakeAdapter({"primal_solution": (1.0, 0.0), "dual_solution": (1.0,)})
        out = run(analytic(), a)
        assert len(a.requests) == 1 and out.job.state == JobState.COMPLETED
        assert out.job.job_id == "job-1" and out.rejection is None

    def test_generated_job_id(self) -> None:
        out = LPOrchestrator(FakeAdapter({})).solve_json(json.dumps(analytic()))
        assert len(out.job.job_id) == 32

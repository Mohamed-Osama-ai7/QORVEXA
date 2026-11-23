"""Phase B integration: real HiGHS subprocess + independent verifier (Solver Fabric level).

No fake solver. Wrapper adapters below delegate to the REAL HighsAdapter; the tampering ones exist
only to prove the verifier (not the solver) is the authority over real candidates.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from fractions import Fraction
from typing import Any

import pytest

from qorvexa.contracts.solver_runtime import (
    JobState,
    MathOutcome,
    OptimalityTier,
    SolverTermination,
)
from qorvexa.contracts.solver_runtime import (
    VerificationClaimKind as K,
)
from qorvexa.contracts.solver_runtime import (
    VerificationLevel as L,
)
from qorvexa.solver_fabric import LPOrchestrator
from qorvexa.solver_fabric.highs_adapter import HighsAdapter
from qorvexa.solver_fabric.worker_contract import (
    WorkerRequest,
    WorkerResponse,
    WorkerStatus,
)
from tests.test_sf_orchestrator import (
    analytic,
    binop,
    constraint,
    infeasible_doc,
    lit,
    problem,
    unbounded_doc,
    var,
    variable,
)

TOL = 1e-6


class Recording:
    """Delegates to the real HighsAdapter; records request/response; optional tamper hook."""

    def __init__(self, tamper: Callable[[WorkerResponse], WorkerResponse] | None = None) -> None:
        self._real = HighsAdapter()
        self._tamper = tamper
        self.requests: list[WorkerRequest] = []
        self.raw: list[WorkerResponse] = []

    @property
    def name(self) -> str:
        return self._real.name

    def solve(self, request: WorkerRequest) -> WorkerResponse:
        self.requests.append(request)
        resp = self._real.solve(request)
        self.raw.append(resp)
        return self._tamper(resp) if self._tamper else resp


def run(doc: dict[str, Any], adapter: Any = None, job: str = "pb-1") -> Any:
    return LPOrchestrator(adapter or Recording()).solve_json(json.dumps(doc), job_id=job)


def con(i: int, ast: Any, rel: str, b: Any) -> dict[str, Any]:
    c = constraint(ast, rel, b)
    c["id"] = f"CON-0{i}"
    return c


class TestRealSolvedPaths:
    def test_analytic_minimization(self) -> None:
        a = Recording()
        out = run(analytic(), a)
        r = out.result
        assert out.job.state == JobState.COMPLETED and r is not None
        assert r.outcome == MathOutcome.SOLVED and r.optimality_tier == OptimalityTier.GAP_NUMERICAL
        assert math.isclose(r.objective_value or 0, 1.0, abs_tol=TOL)
        assert r.dual_solution is not None and math.isclose(
            r.dual_solution["CON-01"], 1.0, abs_tol=1e-9
        )
        assert a.raw[0].solver_name.lower() == "highs"

    def test_maximization_is_not_negated_twice(self) -> None:
        r = run(analytic("maximize")).result
        assert r is not None and r.outcome == MathOutcome.SOLVED
        assert math.isclose(r.objective_value or 0, 4.0, abs_tol=TOL)  # original direction
        assert r.metadata["objective_sense"] == "maximize"
        assert r.dual_solution is not None and r.dual_solution["CON-01"] < 0  # min-form multiplier

    def test_objective_constant_and_maximization(self) -> None:
        doc = analytic("maximize")
        doc["objectives"][0]["expressionAst"] = binop(
            "add", binop("add", var("VAR-01"), var("VAR-02")), lit(10)
        )
        r = run(doc).result
        assert r is not None and r.outcome == MathOutcome.SOLVED
        assert math.isclose(r.objective_value or 0, 14.0, abs_tol=TOL)

    def test_equality_row(self) -> None:
        doc = problem(
            [variable("VAR-01", 0, 10), variable("VAR-02", 0, 10)],
            binop("add", var("VAR-01"), binop("mul", lit(3), var("VAR-02"))),
            [con(1, binop("add", var("VAR-01"), var("VAR-02")), "==", 4)],
        )
        r = run(doc).result
        assert r is not None and r.outcome == MathOutcome.SOLVED
        assert math.isclose(r.objective_value or 0, 4.0, abs_tol=TOL)  # x1 = 4, x2 = 0

    def test_free_variable_lp_is_never_a_false_claim(self) -> None:
        # min x, x free, x >= 2 (row).  Optimum 2.  Either verified SOLVED or an honest UNKNOWN.
        doc = problem([variable("VAR-01", None, None)], var(), [con(1, var(), ">=", 2)])
        r = run(doc).result
        assert r is not None and r.outcome in (MathOutcome.SOLVED, MathOutcome.UNKNOWN)
        if r.outcome == MathOutcome.SOLVED:
            assert math.isclose(r.objective_value or 0, 2.0, abs_tol=TOL)

    def test_degenerate_lp(self) -> None:
        # max 40A+30B ; 2A+B<=120 ; A+B<=80 ; A<=40 -> degenerate vertex (40,40), optimum 2800
        doc = problem(
            [variable("VAR-01", 0, 40), variable("VAR-02", 0, None)],
            binop(
                "add", binop("mul", lit(40), var("VAR-01")), binop("mul", lit(30), var("VAR-02"))
            ),
            [
                con(1, binop("add", binop("mul", lit(2), var("VAR-01")), var("VAR-02")), "<=", 120),
                con(2, binop("add", var("VAR-01"), var("VAR-02")), "<=", 80),
            ],
            "maximize",
        )
        r = run(doc).result
        assert r is not None and r.outcome == MathOutcome.SOLVED
        assert math.isclose(r.objective_value or 0, 2800.0, abs_tol=TOL)

    def test_identity_hashes_and_lowering_evidence_are_preserved(self) -> None:
        a = Recording()
        out = run(analytic(), a, job="pb-identity")
        (req,), (resp,) = a.requests, a.raw
        assert req.request_id == resp.request_id == "pb-identity"
        assert resp.model_sha256 == req.model_sha256 == out.model_sha256
        assert resp.ir_sha256 == req.ir_sha256 == out.ir_sha256
        r = out.result
        assert r is not None and r.lowering_evidence is not None
        assert r.lowering_evidence.model_sha256 == out.model_sha256
        assert r.lowering_evidence.ir_sha256 == out.ir_sha256
        assert any(
            c.claim_kind == K.MODEL_LOWERING_VALIDATED and c.level == L.EXACT_RATIONAL
            for c in r.claims
        )
        assert not any(c.claim_kind == K.DUAL_GAP and c.level == L.EXACT_RATIONAL for c in r.claims)

    def test_decimal_input_is_canonicalized_at_ingress_so_lowering_is_exact(self) -> None:
        # 0.1 becomes its exact binary64 value at M1 ingress (scope: canonicalized input values);
        # the exact model already holds that value, so lowering itself rounds nothing.
        doc = problem(
            [variable("VAR-01", 0, 10)],
            binop("mul", lit(0.1), var()),
            [con(1, var(), "<=", 7)],
            "maximize",
        )
        r = run(doc).result
        assert r is not None and r.outcome == MathOutcome.SOLVED
        assert math.isclose(r.objective_value or 0, 0.7, abs_tol=1e-9)
        assert r.lowering_evidence is not None
        assert r.lowering_evidence.rounding.coefficients_total >= 1
        assert r.lowering_evidence.rounding.coefficients_rounded == 0


class TestRealExactEvidence:
    def test_infeasible_via_exact_farkas_certificate(self) -> None:
        doc = problem(
            [variable("VAR-01", None, None)],
            var(),
            [con(1, var(), ">=", 2), con(2, var(), "<=", 1)],
        )
        r = run(doc).result
        assert r is not None and r.outcome == MathOutcome.INFEASIBLE
        assert r.infeasibility_evidence is not None
        assert Fraction(r.infeasibility_evidence.certificate_value) > 0
        assert r.metadata["certificate_source"] in ("solver_farkas_ray",) or r.metadata[
            "certificate_source"
        ].startswith("interval_conflict")

    def test_single_row_infeasible_from_the_model_itself(self) -> None:
        r = run(infeasible_doc()).result
        assert r is not None and r.outcome == MathOutcome.INFEASIBLE

    def test_unbounded_needs_exact_anchor_and_ray(self) -> None:
        r = run(unbounded_doc()).result
        assert r is not None
        if r.outcome == MathOutcome.UNBOUNDED:
            assert r.unbounded_evidence is not None and "anchor_source" in r.metadata
            assert Fraction(r.unbounded_evidence.objective_slope_min_form) < 0
        else:
            assert r.outcome == MathOutcome.UNKNOWN and r.unbounded_evidence is None

    def test_unbounded_without_solver_ray_stays_unknown(self) -> None:
        # only an empty-row resource exists; HiGHS reports UNBOUNDED but returns no ray
        doc = problem([variable("VAR-01", 0, None)], var(), [con(1, lit(0), "<=", 10)], "maximize")
        a = Recording()
        r = run(doc, a).result
        assert r is not None
        if a.raw[0].primal_ray is None:
            assert r.outcome == MathOutcome.UNKNOWN and r.unbounded_evidence is None
        else:
            assert r.outcome in (MathOutcome.UNBOUNDED, MathOutcome.UNKNOWN)


class TestVerifierIsTheAuthorityOverRealCandidates:
    def test_zeroed_duals_cannot_produce_solved(self) -> None:
        def tamper(r: WorkerResponse) -> WorkerResponse:
            return r.model_copy(update={"dual_solution": tuple(0.0 for _ in r.dual_solution or ())})

        r = run(analytic(), Recording(tamper)).result
        assert r is not None and r.outcome == MathOutcome.UNKNOWN  # gap 1 > tolerance

    def test_infeasible_primal_cannot_produce_solved(self) -> None:
        def tamper(r: WorkerResponse) -> WorkerResponse:
            x = list(r.primal_solution or ())
            x[1] = -0.01
            return r.model_copy(update={"primal_solution": tuple(x)})

        r = run(analytic(), Recording(tamper)).result
        assert r is not None and r.outcome == MathOutcome.UNKNOWN

    def test_lying_objective_is_never_trusted(self) -> None:
        def tamper(r: WorkerResponse) -> WorkerResponse:
            return r.model_copy(update={"objective_value": 123456.0})

        r = run(analytic(), Recording(tamper)).result
        assert r is not None and r.outcome == MathOutcome.SOLVED
        assert math.isclose(r.objective_value or 0, 1.0, abs_tol=TOL)

    def test_status_claim_alone_never_proves_infeasibility(self) -> None:
        def tamper(r: WorkerResponse) -> WorkerResponse:
            return r.model_copy(
                update={"termination": SolverTermination.INFEASIBLE, "farkas_ray": None}
            )

        r = run(analytic(), Recording(tamper)).result  # model is feasible
        assert r is not None and r.outcome != MathOutcome.INFEASIBLE

    def test_wrong_dimension_candidates_are_ignored(self) -> None:
        def tamper(r: WorkerResponse) -> WorkerResponse:
            return r.model_copy(update={"primal_solution": (1.0,), "dual_solution": (1.0, 1.0)})

        r = run(analytic(), Recording(tamper)).result
        assert r is not None and r.outcome == MathOutcome.UNKNOWN

    def test_hash_mismatch_fails_closed(self) -> None:
        def tamper(r: WorkerResponse) -> WorkerResponse:
            return r.model_copy(update={"model_sha256": "c" * 64})

        out = run(analytic(), Recording(tamper))
        assert out.job.state == JobState.FAILED
        assert out.result is None or out.result.outcome != MathOutcome.SOLVED

    def test_worker_failure_is_a_failed_job_not_an_exception(self) -> None:
        class Boom:
            name = "boom"

            def solve(self, request: WorkerRequest) -> WorkerResponse:
                raise RuntimeError("worker exploded")

        out = run(analytic(), Boom())
        assert out.job.state == JobState.FAILED

    def test_timeout_status_with_real_candidates_is_still_verified_not_trusted(self) -> None:
        def tamper(r: WorkerResponse) -> WorkerResponse:
            return r.model_copy(
                update={
                    "status": WorkerStatus.TIMED_OUT,
                    "termination": SolverTermination.TIME_LIMIT,
                }
            )

        r = run(analytic(), Recording(tamper)).result
        # termination is metadata: the exact numerical gap check still decides
        assert r is not None and r.outcome == MathOutcome.SOLVED
        assert r.solver_termination == SolverTermination.TIME_LIMIT


@pytest.mark.parametrize("builder", [analytic])
def test_repeated_runs_are_deterministic(builder: Callable[[], dict[str, Any]]) -> None:
    a, b = run(builder(), job="d1"), run(builder(), job="d1")
    assert a.model_sha256 == b.model_sha256 and a.ir_sha256 == b.ir_sha256
    assert a.result is not None and b.result is not None
    assert a.result.objective_value == b.result.objective_value

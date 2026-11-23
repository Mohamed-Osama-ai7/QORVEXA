"""Independent adversarial LP audit matrix (fake adapters; exact rational oracles).

Every test states: model, mathematically expected result, candidate fed in, trusted outcome.
"""

from __future__ import annotations

import random
from fractions import Fraction
from typing import Any

import pytest
from pydantic import ValidationError

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
from qorvexa.contracts.solver_runtime import (
    VerificationStatus as S,
)
from qorvexa.solver_fabric.canonical_lp import (
    CanonicalRangedLP,
    ColumnSpec,
    RowSpec,
    build_canonical_lp,
)
from qorvexa.solver_fabric.verifier import (
    dual_bound_min_form,
    farkas_value,
    find_interval_conflict,
    repair_dual,
    verify_farkas,
    verify_unbounded,
)
from qorvexa.solver_fabric.worker_contract import (
    WorkerFailure,
    WorkerFailureCode,
    WorkerStatus,
)
from tests.test_sf_orchestrator import (
    FakeAdapter,
    analytic,
    binop,
    constraint,
    infeasible_doc,
    lit,
    problem,
    run,
    var,
    variable,
)

F = Fraction


def lp(
    cols: list[tuple[str, Any, Any]],
    cost: dict[str, Fraction],
    rows: list[tuple[str, dict[str, Fraction], Any, Any]],
    offset: Fraction | None = None,
) -> CanonicalRangedLP:
    return build_canonical_lp(
        [ColumnSpec(i, lo, hi) for i, lo, hi in cols],
        objective_cost=cost,
        objective_constant=F(0) if offset is None else offset,
        objective_sense="minimize",
        rows=[RowSpec(i, co, lo, hi) for i, co, lo, hi in rows],
    )


def con(i: int, ast: Any, rel: str, b: Any, up: Any = None) -> dict[str, Any]:
    c = constraint(ast, rel, b, up)
    c["id"] = f"CON-0{i}"
    return c


# ------------------------------------------------------------------ dual bound, row-sign rules


class TestDualSupportSigns:
    def test_greater_equal_row_needs_nonnegative_multiplier(self) -> None:
        m = lp([("x", F(0), F(10))], {"x": F(1)}, [("r", {"x": F(1)}, F(1), None)])
        assert dual_bound_min_form(m, [F(1)]) == F(1)  # optimum 1
        assert dual_bound_min_form(m, [F(-1)]) is None
        assert dual_bound_min_form(m, [F(0)]) == F(0)

    def test_less_equal_row_needs_nonpositive_multiplier(self) -> None:
        m = lp([("x", F(0), F(10))], {"x": F(-1)}, [("r", {"x": F(1)}, None, F(4))])
        assert dual_bound_min_form(m, [F(-1)]) == F(-4)  # optimum -4
        assert dual_bound_min_form(m, [F(1)]) is None

    def test_equality_row_accepts_any_sign_and_stays_a_lower_bound(self) -> None:
        m = lp([("x", F(0), F(10))], {"x": F(1)}, [("r", {"x": F(1)}, F(3), F(3))])
        assert dual_bound_min_form(m, [F(1)]) == F(3)
        assert dual_bound_min_form(m, [F(-1)]) == F(-3)  # valid but weaker
        assert dual_bound_min_form(m, [F(-1)]) <= F(3)  # type: ignore[operator]

    def test_ranged_row_both_signs_finite(self) -> None:
        m = lp(
            [("a", F(0), F(3)), ("b", F(0), F(3))],
            {"a": F(1), "b": F(2)},
            [("r", {"a": F(1), "b": F(1)}, F(1), F(4))],
        )
        assert dual_bound_min_form(m, [F(1)]) == F(1)  # optimum
        assert dual_bound_min_form(m, [F(-1)]) is not None

    def test_zero_coefficient_on_infinite_bound_is_not_minus_infinity(self) -> None:
        m = lp([("free", None, None), ("x", F(0), F(1))], {"free": F(0), "x": F(1)}, [])
        assert dual_bound_min_form(m, []) == F(0)

    def test_one_sided_variable_needs_reduced_cost_sign(self) -> None:
        pos = lp([("x", F(2), None)], {"x": F(3)}, [])
        neg = lp([("x", F(2), None)], {"x": F(-3)}, [])
        assert dual_bound_min_form(pos, []) == F(6)
        assert dual_bound_min_form(neg, []) is None

    def test_offset_is_added_exactly_once(self) -> None:
        m = lp([("x", F(0), F(1))], {"x": F(1)}, [], offset=F(7, 3))
        assert dual_bound_min_form(m, []) == F(7, 3)


# ------------------------------------------------------------------ random weak-duality oracle


def random_feasible_model(rng: random.Random) -> tuple[CanonicalRangedLP, list[Fraction]]:
    n, mrows = rng.randint(1, 4), rng.randint(0, 3)
    x0 = [F(rng.randint(-3, 3)) for _ in range(n)]
    cols = []
    for j in range(n):
        kind = rng.choice("fLUBX")
        lo = None if kind in "fU" else x0[j] - rng.randint(0, 2) * (kind != "X")
        hi = None if kind in "fL" else x0[j] + rng.randint(0, 2) * (kind != "X")
        cols.append((f"x{j}", lo, hi))
    rows = []
    for i in range(mrows):
        co = {f"x{j}": F(rng.randint(-3, 3)) for j in range(n) if rng.random() < 0.7}
        act = sum((a * x0[int(k[1:])] for k, a in co.items()), F(0))
        kind = rng.choice("GLRE")
        lo = None if kind == "L" else act - (0 if kind == "E" else rng.randint(0, 2))
        hi = None if kind == "G" else act + (0 if kind == "E" else rng.randint(0, 2))
        rows.append((f"r{i}", co, lo, hi))
    cost = {f"x{j}": F(rng.randint(-3, 3)) for j in range(n)}
    return lp(cols, cost, rows, F(rng.randint(-5, 5))), x0


class TestRandomizedSoundness:
    def test_weak_duality_and_farkas_soundness_on_feasible_models(self) -> None:
        rng = random.Random(20240607)  # noqa: S311 - seeded test data, not security
        repaired = finite = 0
        for _ in range(400):
            m, x0 = random_feasible_model(rng)
            primal = sum((m.cost[j] * x0[j] for j in range(len(x0))), F(0)) + m.offset
            for _ in range(5):
                y = [F(rng.randint(-4, 4), rng.randint(1, 3)) for _ in range(m.num_rows)]
                d = dual_bound_min_form(m, y)
                if d is not None:
                    finite += 1
                    assert d <= primal  # weak duality for ANY y
                fv = farkas_value(m, y)
                assert fv is None or fv <= 0  # feasible model: no Farkas certificate exists
                assert not verify_farkas(m, y).verified
                rep = repair_dual(m, y)
                if rep is not None:
                    repaired += 1
                    d2 = dual_bound_min_form(m, rep[1])
                    assert d2 is not None and d2 <= primal  # repaired candidate rechecked exactly
            assert find_interval_conflict(m) is None
        assert finite > 100 and repaired > 20  # the property was actually exercised


# ------------------------------------------------------------------ Farkas matrix


class TestFarkas:
    def test_objective_constant_does_not_enter_the_contradiction(self) -> None:
        for off in (F(0), F(100), F(-100)):
            m = lp([("x", F(0), F(1))], {"x": F(5)}, [("r", {"x": F(1)}, F(2), None)], off)
            assert farkas_value(m, [F(1)]) == F(1)

    def test_touching_constraints_are_feasible_so_no_certificate(self) -> None:
        m = lp([("x", F(0), F(1))], {"x": F(0)}, [("r", {"x": F(1)}, F(1), None)])
        assert farkas_value(m, [F(1)]) == F(0)
        assert not verify_farkas(m, [F(1)]).verified

    def test_wrong_orientation_is_rejected(self) -> None:
        m = lp([("x", F(0), F(1))], {"x": F(0)}, [("r", {"x": F(1)}, F(2), None)])
        assert verify_farkas(m, [F(1)]).verified
        assert not verify_farkas(m, [F(-1)]).verified

    def test_two_row_conflict_on_free_column_is_not_a_single_row_conflict(self) -> None:
        # x free; x >= 2 and x <= 1: certificate y = (1, -1), D0 = 2 - 1 = 1
        m = lp(
            [("x", None, None)],
            {"x": F(0)},
            [("a", {"x": F(1)}, F(2), None), ("b", {"x": F(1)}, None, F(1))],
        )
        assert find_interval_conflict(m) is None
        v = verify_farkas(m, [F(1), F(-1)])
        assert v.verified and v.certificate_value == F(1)
        assert not verify_farkas(m, [F(1), F(1)]).verified

    def test_empty_rows(self) -> None:
        pos = lp([("x", F(0), F(1))], {"x": F(0)}, [("e", {}, F(1), None)])  # 0 >= 1
        neg = lp([("x", F(0), F(1))], {"x": F(0)}, [("e", {}, None, F(-1))])  # 0 <= -1
        ok = lp([("x", F(0), F(1))], {"x": F(0)}, [("e", {}, F(-1), F(1))])
        assert find_interval_conflict(pos) is not None
        assert find_interval_conflict(neg) is not None
        assert find_interval_conflict(ok) is None

    def test_ranged_row_above_activity_range(self) -> None:
        m = lp([("x", F(0), F(1))], {"x": F(0)}, [("r", {"x": F(2)}, F(3), F(5))])  # 2x<=2
        v = find_interval_conflict(m)
        assert v is not None and v.certificate_value == F(1)

    def test_negative_coefficient_activity_range(self) -> None:
        # -x in [-1, 0] over x in [0,1]... feasible; -x >= 1 infeasible (max activity 0)
        feas = lp([("x", F(0), F(1))], {"x": F(0)}, [("r", {"x": F(-1)}, F(-1), F(0))])
        bad = lp([("x", F(0), F(1))], {"x": F(0)}, [("r", {"x": F(-1)}, F(1), None)])
        assert find_interval_conflict(feas) is None
        assert find_interval_conflict(bad) is not None


class TestFarkasThroughOrchestrator:
    def doc(self) -> dict[str, Any]:
        return problem(
            [variable("VAR-01", None, None)],
            var(),
            [con(1, var(), ">=", 2), con(2, var(), "<=", 1)],
        )

    def test_exact_multi_row_ray_gives_infeasible(self) -> None:
        a = FakeAdapter({"termination": SolverTermination.INFEASIBLE, "farkas_ray": (1.0, -1.0)})
        r = run(self.doc(), a).result
        assert r is not None and r.outcome == MathOutcome.INFEASIBLE
        assert r.infeasibility_evidence is not None
        assert r.infeasibility_evidence.certificate_value == "1"

    def test_wrong_ray_with_infeasible_status_is_unknown(self) -> None:
        a = FakeAdapter({"termination": SolverTermination.INFEASIBLE, "farkas_ray": (1.0, 1.0)})
        r = run(self.doc(), a).result
        assert r is not None and r.outcome == MathOutcome.UNKNOWN
        assert r.infeasibility_evidence is None

    def test_status_alone_never_proves_infeasibility(self) -> None:
        a = FakeAdapter({"termination": SolverTermination.INFEASIBLE})
        r = run(self.doc(), a).result
        assert r is not None and r.outcome == MathOutcome.UNKNOWN

    def test_touching_system_with_infeasible_claim_is_not_infeasible(self) -> None:
        doc = problem(
            [variable("VAR-01", None, None)],
            var(),
            [con(1, var(), ">=", 1), con(2, var(), "<=", 1)],
        )
        a = FakeAdapter({"termination": SolverTermination.INFEASIBLE, "farkas_ray": (1.0, -1.0)})
        r = run(doc, a).result
        assert r is not None and r.outcome == MathOutcome.UNKNOWN

    def test_exact_certificate_survives_timeout_and_crash(self) -> None:
        t = FakeAdapter(
            {"status": WorkerStatus.TIMED_OUT, "termination": SolverTermination.TIME_LIMIT}
        )
        r = run(infeasible_doc(), t).result
        assert r is not None and r.outcome == MathOutcome.INFEASIBLE
        crash = FakeAdapter(
            {
                "status": WorkerStatus.FAILED,
                "termination": SolverTermination.CRASHED,
                "failure": WorkerFailure(code=WorkerFailureCode.CRASHED, message="boom"),
            }
        )
        r2 = run(infeasible_doc(), crash).result
        assert r2 is not None and r2.outcome == MathOutcome.INFEASIBLE
        assert r2.solver_termination == SolverTermination.CRASHED


# ------------------------------------------------------------------ unbounded matrix


class TestUnboundedExactness:
    def model(self) -> CanonicalRangedLP:
        # min -x1 ; x1,x2 >= 0 ; x1 - x2 == 0
        return lp(
            [("x1", F(0), None), ("x2", F(0), None)],
            {"x1": F(-1), "x2": F(0)},
            [("r", {"x1": F(1), "x2": F(-1)}, F(0), F(0))],
        )

    def test_valid_recession_direction(self) -> None:
        v = verify_unbounded(self.model(), [F(0), F(0)], [F(1), F(1)])
        assert v.verified and v.slope_min_form == F(-1)

    def test_equality_row_breaks_direction(self) -> None:
        assert not verify_unbounded(self.model(), [F(0), F(0)], [F(1), F(0)]).verified

    def test_infeasible_anchor_is_rejected(self) -> None:
        v = verify_unbounded(self.model(), [F(1), F(0)], [F(1), F(1)])
        assert not v.verified and "anchor" in v.reason

    def test_zero_ray_and_zero_slope_rejected(self) -> None:
        m = lp([("x", F(0), None)], {"x": F(0)}, [])
        assert not verify_unbounded(m, [F(0)], [F(0)]).verified
        assert not verify_unbounded(m, [F(0)], [F(1)]).verified  # slope 0, not improving

    def test_ray_leaving_finite_upper_bound_rejected(self) -> None:
        m = lp([("x", F(0), F(5))], {"x": F(-1)}, [])
        assert not verify_unbounded(m, [F(0)], [F(1)]).verified

    def test_free_variable_both_directions(self) -> None:
        m = lp([("x", None, None)], {"x": F(1)}, [])
        assert verify_unbounded(m, [F(3)], [F(-1)]).verified
        assert not verify_unbounded(m, [F(3)], [F(1)]).verified

    def test_one_sided_row_allows_only_its_open_direction(self) -> None:
        m = lp([("x", F(0), None)], {"x": F(-1)}, [("r", {"x": F(1)}, None, F(4))])
        assert not verify_unbounded(m, [F(0)], [F(1)]).verified  # x <= 4 caps it

    def test_ray_alone_without_exact_anchor_is_unknown_through_orchestrator(self) -> None:
        doc = problem(
            [variable("VAR-01", 0, None)],
            binop("mul", lit(-1), var()),
            [con(1, var(), ">=", 1)],
        )
        a = FakeAdapter({"termination": SolverTermination.UNBOUNDED, "primal_ray": (1.0,)})
        r = run(doc, a).result
        assert r is not None and r.outcome == MathOutcome.UNKNOWN

    def test_exact_trivial_anchor_suffices_without_solver_point(self) -> None:
        doc = problem(
            [variable("VAR-01", 0, None)],
            binop("mul", lit(-1), var()),
            [con(1, var(), ">=", 0)],
        )
        a = FakeAdapter({"termination": SolverTermination.UNBOUNDED, "primal_ray": (1.0,)})
        r = run(doc, a).result
        assert r is not None and r.outcome == MathOutcome.UNBOUNDED
        assert r.unbounded_evidence is not None
        assert r.unbounded_evidence.objective_slope_min_form == "-1"

    def test_maximization_unbounded_slope_is_in_minimization_form(self) -> None:
        doc = problem(
            [variable("VAR-01", 0, None)], var(), [con(1, var(), ">=", 0)], direction="maximize"
        )
        a = FakeAdapter({"termination": SolverTermination.UNBOUNDED, "primal_ray": (1.0,)})
        r = run(doc, a).result
        assert r is not None and r.outcome == MathOutcome.UNBOUNDED
        assert r.unbounded_evidence is not None
        assert r.unbounded_evidence.objective_slope_min_form == "-1"


# ------------------------------------------------------------------ orchestrator semantics


class TestOrchestratorSemantics:
    def test_encoding_a_constant_offset_in_expression(self) -> None:
        # min x ; x + 3 >= 5 ; x in [0,10]  -> x >= 2, optimum 2, y = 1
        doc = problem(
            [variable("VAR-01", 0, 10)],
            var(),
            [con(1, binop("add", var(), lit(3)), ">=", 5)],
        )
        a = FakeAdapter({"primal_solution": (2.0,), "dual_solution": (1.0,)})
        r = run(doc, a).result
        assert r is not None and r.outcome == MathOutcome.SOLVED and r.objective_value == 2.0

    def test_maximization_with_constant(self) -> None:
        doc = analytic("maximize")
        doc["objectives"][0]["expressionAst"] = binop(
            "add", binop("add", var("VAR-01"), var("VAR-02")), lit(10)
        )
        a = FakeAdapter({"primal_solution": (3.0, 1.0), "dual_solution": (-1.0,)})
        r = run(doc, a).result
        assert r is not None and r.outcome == MathOutcome.SOLVED and r.objective_value == 14.0

    def test_primal_violating_by_more_than_tolerance_is_not_solved(self) -> None:
        a = FakeAdapter({"primal_solution": (1.0, -0.001), "dual_solution": (1.0,)})
        r = run(analytic(), a).result
        assert r is not None and r.outcome == MathOutcome.UNKNOWN
        assert (K.PRIMAL_FEASIBILITY, L.NUMERICAL) in {
            (c.claim_kind, c.level) for c in r.claims if c.status == S.REFUTED
        }

    def test_bad_dual_with_feasible_primal_is_not_solved(self) -> None:
        a = FakeAdapter({"primal_solution": (1.0, 0.0), "dual_solution": (0.0,)})
        r = run(analytic(), a).result
        assert r is not None and r.outcome == MathOutcome.UNKNOWN  # gap 1 > tolerance

    def test_suboptimal_feasible_point_with_matching_bound_is_unknown(self) -> None:
        a = FakeAdapter({"primal_solution": (2.0, 0.0), "dual_solution": (1.0,)})  # obj 2 vs D 1
        r = run(analytic(), a).result
        assert r is not None and r.outcome == MathOutcome.UNKNOWN

    def test_numerically_feasible_point_is_solved_only_at_numerical_tier(self) -> None:
        a = FakeAdapter({"primal_solution": (1.0 - 1e-7, 0.0), "dual_solution": (1.0,)})
        r = run(analytic(), a).result
        assert r is not None and r.outcome == MathOutcome.SOLVED
        assert r.optimality_tier == OptimalityTier.GAP_NUMERICAL
        levels = {(c.claim_kind, c.level) for c in r.claims}
        assert (K.PRIMAL_FEASIBILITY, L.NUMERICAL) in levels
        assert (K.PRIMAL_FEASIBILITY, L.EXACT_RATIONAL) not in levels

    def test_wrong_dimension_vectors_are_ignored(self) -> None:
        a = FakeAdapter({"primal_solution": (1.0,), "dual_solution": (1.0, 1.0)})
        r = run(analytic(), a).result
        assert r is not None and r.outcome == MathOutcome.UNKNOWN
        assert "primal_solution_ignored" in r.metadata and "dual_solution_ignored" in r.metadata

    def test_binding_mismatch_never_yields_a_result_claim(self) -> None:
        class Liar(FakeAdapter):
            def solve(self, request: Any) -> Any:
                resp = super().solve(request)
                return resp.model_copy(update={"ir_sha256": "b" * 64})

        a = Liar({"primal_solution": (1.0, 0.0), "dual_solution": (1.0,)})
        out = run(analytic(), a)
        assert out.job.state == JobState.FAILED
        assert out.result is None or out.result.outcome != MathOutcome.SOLVED

    def test_unbounded_or_infeasible_status_without_vectors_is_unknown(self) -> None:
        a = FakeAdapter({"termination": SolverTermination.UNBOUNDED_OR_INFEASIBLE})
        r = run(analytic(), a).result
        assert r is not None and r.outcome == MathOutcome.UNKNOWN

    def test_solver_claimed_optimal_on_infeasible_model_is_never_solved(self) -> None:
        a = FakeAdapter({"primal_solution": (0.5,), "dual_solution": (1.0,)})
        r = run(infeasible_doc(), a).result
        assert r is not None and r.outcome == MathOutcome.INFEASIBLE  # own exact conflict

    def test_non_finite_candidates_cannot_be_constructed(self) -> None:
        with pytest.raises(ValidationError):
            FakeAdapter({"primal_solution": (float("nan"), 0.0)}).solve(
                type(
                    "R", (), {"request_id": "x", "model_sha256": "a" * 64, "ir_sha256": "a" * 64}
                )()
            )


# ------------------------------------------------------------------ repair / anchor limits


class TestRepairAdversarial:
    def test_multiple_free_columns_repaired_together(self) -> None:
        # min x+y ; x,y free ; x+y == 2 ; and x - y == 0   -> y* = (1, 0) gives r = 0
        m = lp(
            [("x", None, None), ("y", None, None)],
            {"x": F(1), "y": F(1)},
            [
                ("a", {"x": F(1), "y": F(1)}, F(2), F(2)),
                ("b", {"x": F(1), "y": F(-1)}, F(0), F(0)),
            ],
        )
        bad = [F(1) + F(1, 10**9), F(1, 10**12)]
        assert dual_bound_min_form(m, bad) is None
        rep = repair_dual(m, bad)
        assert rep is not None
        assert dual_bound_min_form(m, rep[1]) == F(2)

    def test_degenerate_duplicate_rows(self) -> None:
        m = lp(
            [("x", None, None)],
            {"x": F(1)},
            [("a", {"x": F(1)}, F(2), F(2)), ("b", {"x": F(1)}, F(2), F(2))],
        )
        rep = repair_dual(m, [F(1, 2) + F(1, 10**9), F(1, 2)])
        assert rep is not None and dual_bound_min_form(m, rep[1]) == F(2)

    def test_inconsistent_system_is_not_repaired(self) -> None:
        m = lp(
            [("x", None, None), ("y", None, None)],
            {"x": F(1), "y": F(5)},
            [("a", {"x": F(1), "y": F(1)}, F(0), F(0))],  # needs 1 = y_ and 5 = y_ simultaneously
        )
        assert repair_dual(m, [F(1)]) is None

    def test_size_limit_fails_closed_only_because_of_the_cap(self) -> None:
        def ident(n: int) -> CanonicalRangedLP:  # x_j free, row j: x_j == 0 -> exactly solvable
            cols = [(f"x{j}", None, None) for j in range(n)]
            rows = [(f"r{j}", {f"x{j}": F(1)}, F(0), F(0)) for j in range(n)]
            return lp(cols, {f"x{j}": F(1) for j in range(n)}, rows)

        small = ident(50)
        assert repair_dual(small, [F(1, 3)] * 50) is not None  # solvable under the cap
        big = ident(300)  # same structure, 300 forced columns x 300 rows > 40_000 cells
        assert repair_dual(big, [F(1, 3)] * 300) is None

    def test_repair_never_manufactures_a_better_bound_than_the_optimum(self) -> None:
        m = lp(
            [("x", None, None), ("z", F(0), F(3))],
            {"x": F(1), "z": F(1)},
            [("r", {"x": F(1), "z": F(1)}, F(2), F(2))],
        )
        rep = repair_dual(m, [F(7, 5)])
        if rep is not None:
            d = dual_bound_min_form(m, rep[1])
            assert d is not None and d <= F(2)

"""Adversarial / analytic hardening tests for the verifier and orchestrator (fake adapters only)."""

from __future__ import annotations

from fractions import Fraction
from typing import Any

from qorvexa.contracts.solver_runtime import (
    JobState,
    MathOutcome,
    OptimalityTier,
    SolverTermination,
)
from qorvexa.solver_fabric.canonical_lp import ColumnSpec, RowSpec, build_canonical_lp
from qorvexa.solver_fabric.verifier import (
    PrimalCheck,
    VerificationPolicy,
    anchor_candidates,
    dual_bound_min_form,
    evaluate_gap,
    repair_dual,
)
from tests.test_sf_orchestrator import (
    FakeAdapter,
    binop,
    constraint,
    lit,
    problem,
    run,
    var,
    variable,
)

F = Fraction


def free_column_doc() -> dict[str, Any]:
    # min x + z ; x free, z in [0,3] ; x + z == 2.   optimum 2, y = 1, reduced costs 0
    return problem(
        [variable("VAR-01", None, None), variable("VAR-02", 0, 3)],
        binop("add", var("VAR-01"), var("VAR-02")),
        [constraint(binop("add", var("VAR-01"), var("VAR-02")), "==", 2)],
    )


class TestFreeColumnDualRepair:
    def test_tiny_reduced_cost_on_free_column_is_repaired_and_exactly_rechecked(self) -> None:
        y = 1.0 + 1e-12  # exact rational leaves r_x = -1e-12 on a free column -> D(y) = -inf
        a = FakeAdapter({"primal_solution": (2.0, 0.0), "dual_solution": (y,)})
        out = run(free_column_doc(), a)
        r = out.result
        assert r is not None and r.outcome == MathOutcome.SOLVED
        assert r.optimality_tier == OptimalityTier.GAP_NUMERICAL
        assert r.metadata["dual_source"].startswith("repaired_from_")
        assert r.gap_evidence is not None and r.gap_evidence.absolute_gap == 0.0

    def test_without_repair_the_raw_bound_is_minus_infinity(self) -> None:
        m = build_canonical_lp(
            [ColumnSpec("x", None, None), ColumnSpec("z", F(0), F(3))],
            objective_cost={"x": F(1), "z": F(1)},
            objective_constant=F(0),
            objective_sense="minimize",
            rows=[RowSpec("r", {"x": F(1), "z": F(1)}, F(2), F(2))],
        )
        bad = [F(1) + F(1, 10**12)]
        assert dual_bound_min_form(m, bad) is None
        rep = repair_dual(m, bad)
        assert rep is not None and rep[1] == [F(1)]
        assert dual_bound_min_form(m, rep[1]) == F(2)

    def test_wrong_sign_one_sided_row_multiplier_is_frozen_at_zero(self) -> None:
        m = build_canonical_lp(
            [ColumnSpec("x", F(0), F(5))],
            objective_cost={"x": F(1)},
            objective_constant=F(0),
            objective_sense="minimize",
            rows=[RowSpec("r", {"x": F(1)}, None, F(4))],  # x <= 4 needs y <= 0
        )
        assert dual_bound_min_form(m, [F(1, 10**15)]) is None
        rep = repair_dual(m, [F(1, 10**15)])
        assert rep is not None and rep[1] == [F(0)]

    def test_repair_fails_closed_when_inconsistent(self) -> None:
        # free column with cost 1 and no row touching it: no multiplier can cancel its cost
        m = build_canonical_lp(
            [ColumnSpec("x", None, None)],
            objective_cost={"x": F(1)},
            objective_constant=F(0),
            objective_sense="minimize",
            rows=[],
        )
        assert repair_dual(m, []) is None

    def test_gross_dual_error_is_not_repaired_into_a_false_solve(self) -> None:
        a = FakeAdapter({"primal_solution": (1.0, 1.0), "dual_solution": (5.0,)})
        r = run(free_column_doc(), a).result
        # repair yields y = 1 whose gap to a *feasible* primal (obj 2) is 0, which is mathematically
        # valid; the raw y = 5 is never trusted. Outcome must be justified by the exact recheck.
        assert r is not None and r.outcome in (MathOutcome.SOLVED, MathOutcome.UNKNOWN)
        if r.outcome == MathOutcome.SOLVED:
            assert r.gap_evidence is not None and r.gap_evidence.absolute_gap <= 1e-6


class TestUnboundedAnchorRecovery:
    def test_slightly_infeasible_anchor_is_reconstructed_by_denominators(self) -> None:
        a = FakeAdapter(
            {
                "termination": SolverTermination.UNBOUNDED,
                "primal_solution": (0.9999999999,),  # violates x >= 1 by 1e-10 exactly
                "primal_ray": (1.0,),
            }
        )
        doc = problem(
            [variable("VAR-01", 0, None)],
            binop("mul", lit(-1), var()),
            [constraint(var(), ">=", 1)],
        )
        r = run(doc, a).result
        assert r is not None and r.outcome == MathOutcome.UNBOUNDED
        assert r.metadata["anchor_source"].startswith("solver_")
        assert r.metadata["anchor_source"] != "solver_exact"

    def test_equality_anchor_recovered_from_rounded_floats(self) -> None:
        doc = problem(
            [variable("VAR-01", 0, 1), variable("VAR-02", 0, 1), variable("VAR-03", 0, None)],
            binop("mul", lit(-1), var("VAR-03")),
            [constraint(binop("add", var("VAR-01"), var("VAR-02")), "==", 1)],
        )
        a = FakeAdapter(
            {
                "termination": SolverTermination.UNBOUNDED,
                "primal_solution": (0.3333333333333333, 0.6666666666666666, 0.0),
                "primal_ray": (0.0, 0.0, 1.0),
            }
        )
        r = run(doc, a).result
        assert r is not None and r.outcome == MathOutcome.UNBOUNDED
        assert r.unbounded_evidence is not None

    def test_no_exact_anchor_means_unknown_never_unbounded(self) -> None:
        a = FakeAdapter(
            {
                "termination": SolverTermination.UNBOUNDED,
                "primal_solution": (0.2,),  # infeasible for x >= 1 under every reconstruction
                "primal_ray": (1.0,),
            }
        )
        doc = problem(
            [variable("VAR-01", 0, None)],
            binop("mul", lit(-1), var()),
            [constraint(var(), ">=", 5)],
        )
        r = run(doc, a).result
        assert r is not None and r.outcome == MathOutcome.UNKNOWN
        assert r.unbounded_evidence is None

    def test_non_recession_ray_is_rejected_even_with_good_anchor(self) -> None:
        a = FakeAdapter(
            {
                "termination": SolverTermination.UNBOUNDED,
                "primal_solution": (1.0,),
                "primal_ray": (-1.0,),  # leaves x >= 0 box and worsens objective
            }
        )
        doc = problem(
            [variable("VAR-01", 0, None)],
            binop("mul", lit(-1), var()),
            [constraint(var(), ">=", 1)],
        )
        r = run(doc, a).result
        assert r is not None and r.outcome == MathOutcome.UNKNOWN

    def test_candidates_include_box_clamped_zero_without_solver_vector(self) -> None:
        m = build_canonical_lp(
            [ColumnSpec("x", F(2), None)],
            objective_cost={"x": F(-1)},
            objective_constant=F(0),
            objective_sense="minimize",
            rows=[],
        )
        names = [n for n, _ in anchor_candidates(m, None)]
        assert "box_clamped_zero" in names


class TestGapPolicy:
    def _model(self, offset: Fraction) -> Any:
        return build_canonical_lp(
            [ColumnSpec("x", F(0), F(1))],
            objective_cost={"x": F(1)},
            objective_constant=offset,
            objective_sense="minimize",
            rows=[],
        )

    def test_negative_gap_with_exact_primal_is_rejected(self) -> None:
        m = self._model(F(0))  # D(()) = 0
        bogus = PrimalCheck(F(0), F(0), F(-1))  # primal below the dual bound: impossible
        v = evaluate_gap(m, bogus, [], VerificationPolicy())
        assert not v.accepted and "weak duality" in v.reason

    def test_large_objective_uses_relative_tolerance(self) -> None:
        m = self._model(F(10**9))
        near = PrimalCheck(F(0), F(0), F(10**9) + F(1, 2))
        assert evaluate_gap(m, near, [], VerificationPolicy()).accepted

    def test_small_objective_does_not_get_a_free_pass(self) -> None:
        m = self._model(F(0))
        off = PrimalCheck(F(0), F(0), F(1, 1000))  # abs gap 1e-3, scale floored at 1
        assert not evaluate_gap(m, off, [], VerificationPolicy()).accepted

    def test_residual_feasibility_is_not_optimality(self) -> None:
        m = self._model(F(0))
        # numerically feasible (violation > 0) point whose objective beats the dual bound by a lot
        infeasible_ish = PrimalCheck(F(1, 10**7), F(0), F(-1, 2))
        assert not evaluate_gap(m, infeasible_ish, [], VerificationPolicy()).accepted

    def test_constant_and_maximization_are_handled(self) -> None:
        m = build_canonical_lp(
            [ColumnSpec("x", F(0), F(1))],
            objective_cost={"x": F(1)},
            objective_constant=F(7),
            objective_sense="maximize",
            rows=[],
        )
        # compiler negates: min -x - 7 ; optimum at x=1 is -8 (original 8); D(()) = -1 - 7 = -8
        v = evaluate_gap(m, PrimalCheck(F(0), F(0), F(-8)), [], VerificationPolicy())
        assert v.accepted and v.primal_original == 8.0 and v.dual_original == 8.0


class TestContradictoryVariableBounds:
    def test_fail_closed_before_any_solver_call(self) -> None:
        a = FakeAdapter({})
        doc = problem([variable("VAR-01", 5, 1)], var(), [constraint(var(), ">=", 0)])
        out = run(doc, a)
        assert out.job.state == JobState.FAILED and not a.requests
        assert out.rejection is not None and out.rejection.code == "BOUND_CONTRADICTION_EXACT"

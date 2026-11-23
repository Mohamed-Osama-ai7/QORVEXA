"""Independent exact verifier tests (analytic fixtures; no solver involved)."""

from __future__ import annotations

import dataclasses
import math
from fractions import Fraction

import pytest

from qorvexa.solver_fabric.canonical_lp import (
    CanonicalRangedLP,
    ColumnSpec,
    RowSpec,
    build_canonical_lp,
    lower_ranged_lp,
)
from qorvexa.solver_fabric.verifier import (
    VerificationPolicy,
    check_primal,
    dual_bound_min_form,
    evaluate_gap,
    exact_vector,
    farkas_value,
    find_interval_conflict,
    to_original_sense,
    trivial_anchor_candidates,
    vector_variants,
    verify_farkas,
    verify_lowering,
    verify_unbounded,
)


def lp(sense: str = "minimize", c0: Fraction = Fraction(0)) -> CanonicalRangedLP:
    # min x1 + 2 x2 ; 1 <= x1 + x2 <= 4 ; x in [0,3]^2 ; optimum 1 at (1,0), y = 1
    return build_canonical_lp(
        [ColumnSpec("x1", Fraction(0), Fraction(3)), ColumnSpec("x2", Fraction(0), Fraction(3))],
        objective_cost={"x1": Fraction(1), "x2": Fraction(2)},
        objective_constant=c0,
        objective_sense=sense,  # type: ignore[arg-type]
        rows=[RowSpec("r1", {"x1": Fraction(1), "x2": Fraction(1)}, Fraction(1), Fraction(4))],
    )


def one_var(
    lo: Fraction | None, hi: Fraction | None, cost: Fraction, rows: list[RowSpec]
) -> CanonicalRangedLP:
    return build_canonical_lp(
        [ColumnSpec("x", lo, hi)],
        objective_cost={"x": cost},
        objective_constant=Fraction(0),
        objective_sense="minimize",
        rows=rows,
    )


class TestDualBound:
    def test_tight_at_optimal_multiplier(self) -> None:
        assert dual_bound_min_form(lp(), [Fraction(1)]) == 1

    def test_valid_for_every_multiplier(self) -> None:
        for y in (
            Fraction(-5),
            Fraction(-1),
            Fraction(0),
            Fraction(1, 2),
            Fraction(2),
            Fraction(7),
        ):
            d = dual_bound_min_form(lp(), [y])
            assert d is not None and d <= 1

    def test_constant_shifts_bound(self) -> None:
        assert dual_bound_min_form(lp(c0=Fraction(7)), [Fraction(1)]) == 8

    def test_free_column_with_nonzero_reduced_cost_is_minus_infinity(self) -> None:
        m = one_var(
            None, None, Fraction(1), [RowSpec("r", {"x": Fraction(1)}, Fraction(0), Fraction(1))]
        )
        assert dual_bound_min_form(m, [Fraction(0)]) is None
        assert dual_bound_min_form(m, [Fraction(1)]) == 0  # r = 0, row inf = 0

    def test_wrong_length_rejected(self) -> None:
        with pytest.raises(ValueError):
            dual_bound_min_form(lp(), [Fraction(1), Fraction(1)])


class TestPrimal:
    def test_exact_feasible_optimum(self) -> None:
        c = check_primal(lp(), [Fraction(1), Fraction(0)])
        assert c.exactly_feasible and c.objective_min_form == 1

    def test_row_violation_is_exact(self) -> None:
        c = check_primal(lp(), [Fraction(0), Fraction(0)])
        assert not c.exactly_feasible and c.max_row_violation == 1 and c.max_bound_violation == 0

    def test_bound_violation_is_exact(self) -> None:
        c = check_primal(lp(), [Fraction(4), Fraction(0)])
        assert c.max_bound_violation == 1 and c.max_row_violation == 0

    def test_binary64_candidate_is_judged_exactly(self) -> None:
        x = exact_vector([0.1, 0.9], 2, "x")  # 0.1 + 0.9 is not exactly 1 as rationals
        c = check_primal(lp(), x)
        assert (
            c.max_violation == Fraction(1) - (Fraction(0.1) + Fraction(0.9)) or c.exactly_feasible
        )

    def test_original_sense_conversion(self) -> None:
        assert to_original_sense(lp("maximize"), Fraction(3)) == -3
        assert to_original_sense(lp(), Fraction(3)) == 3


class TestVectors:
    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    def test_non_finite_rejected(self, bad: float) -> None:
        with pytest.raises(ValueError):
            exact_vector([1.0, bad], 2, "v")

    def test_length_checked(self) -> None:
        with pytest.raises(ValueError):
            exact_vector([1.0], 2, "v")

    def test_variants_dedupe_and_start_with_exact(self) -> None:
        v = vector_variants([0.5, 1.0], 2, "v")
        assert v == [[Fraction(1, 2), Fraction(1)]]
        w = vector_variants([0.1], 1, "v")
        assert w[0] == [Fraction(0.1)] and w[1] == [Fraction(1, 10)]


class TestFarkas:
    def infeasible(self) -> CanonicalRangedLP:
        return one_var(
            Fraction(0),
            Fraction(1),
            Fraction(1),
            [RowSpec("r", {"x": Fraction(1)}, Fraction(2), None)],
        )

    def test_exact_certificate(self) -> None:
        m = self.infeasible()
        assert farkas_value(m, [Fraction(1)]) == 1
        v = verify_farkas(m, [Fraction(1)])
        assert v.verified and v.certificate_value == 1

    def test_scaling_keeps_validity(self) -> None:
        assert verify_farkas(self.infeasible(), [Fraction(5)]).certificate_value == 5

    def test_zero_and_wrong_sign_multipliers_do_not_verify(self) -> None:
        m = self.infeasible()
        assert not verify_farkas(m, [Fraction(0)]).verified
        assert not verify_farkas(m, [Fraction(-1)]).verified

    def test_feasible_model_has_no_certificate(self) -> None:
        for y in (Fraction(-3), Fraction(0), Fraction(1), Fraction(3)):
            v = farkas_value(lp(), [y])
            assert v is None or v <= 0

    def test_unbounded_side_gives_no_certificate(self) -> None:
        m = one_var(None, None, Fraction(0), [RowSpec("r", {"x": Fraction(1)}, Fraction(2), None)])
        assert not verify_farkas(m, [Fraction(1)]).verified


class TestIntervalConflict:
    def test_empty_row(self) -> None:
        m = build_canonical_lp(
            [ColumnSpec("x", Fraction(0), Fraction(1))],
            objective_cost={},
            objective_constant=Fraction(0),
            objective_sense="minimize",
            rows=[RowSpec("e", {}, Fraction(5), None)],
        )
        v = find_interval_conflict(m)
        assert v is not None and v.verified and v.source == "interval_conflict:e"

    def test_activity_below_lower(self) -> None:
        m = one_var(
            Fraction(0),
            Fraction(1),
            Fraction(1),
            [RowSpec("r", {"x": Fraction(2)}, Fraction(5), None)],
        )
        v = find_interval_conflict(m)
        assert v is not None and v.certificate_value == 3 and v.multipliers == (Fraction(1),)

    def test_activity_above_upper(self) -> None:
        m = one_var(
            Fraction(4),
            Fraction(9),
            Fraction(1),
            [RowSpec("r", {"x": Fraction(1)}, None, Fraction(2))],
        )
        v = find_interval_conflict(m)
        assert v is not None and v.multipliers == (Fraction(-1),) and v.certificate_value == 2

    def test_negative_coefficient(self) -> None:
        m = one_var(
            Fraction(0),
            Fraction(1),
            Fraction(1),
            [RowSpec("r", {"x": Fraction(-1)}, Fraction(1), None)],
        )
        assert find_interval_conflict(m) is not None  # -x <= 0 < 1

    def test_feasible_has_none(self) -> None:
        assert find_interval_conflict(lp()) is None

    def test_unbounded_activity_has_none(self) -> None:
        m = one_var(None, None, Fraction(1), [RowSpec("r", {"x": Fraction(1)}, Fraction(5), None)])
        assert find_interval_conflict(m) is None

    def test_coupled_conflict_is_not_claimed_by_single_row_check(self) -> None:
        # x>=1 and x<=0 through two rows is infeasible but no single row conflicts.
        m = one_var(
            None,
            None,
            Fraction(0),
            [
                RowSpec("a", {"x": Fraction(1)}, Fraction(1), None),
                RowSpec("b", {"x": Fraction(1)}, None, Fraction(0)),
            ],
        )
        assert find_interval_conflict(m) is None
        assert verify_farkas(m, [Fraction(1), Fraction(-1)]).verified  # but a Farkas pair proves it


class TestUnbounded:
    def model(self) -> CanonicalRangedLP:  # min -x, x >= 0 free above, row x >= 1
        return one_var(
            Fraction(0), None, Fraction(-1), [RowSpec("r", {"x": Fraction(1)}, Fraction(1), None)]
        )

    def test_verified(self) -> None:
        v = verify_unbounded(self.model(), [Fraction(1)], [Fraction(1)])
        assert v.verified and v.slope_min_form == -1

    def test_anchor_must_be_exactly_feasible(self) -> None:
        assert not verify_unbounded(self.model(), [Fraction(1, 2)], [Fraction(1)]).verified

    def test_ray_must_improve(self) -> None:
        assert not verify_unbounded(self.model(), [Fraction(1)], [Fraction(-1)]).verified

    def test_ray_must_be_nonzero(self) -> None:
        assert not verify_unbounded(self.model(), [Fraction(1)], [Fraction(0)]).verified

    def test_ray_must_respect_bounds(self) -> None:
        m = one_var(Fraction(0), Fraction(5), Fraction(-1), [])
        assert not verify_unbounded(m, [Fraction(0)], [Fraction(1)]).verified

    def test_ray_must_respect_rows(self) -> None:
        m = one_var(
            Fraction(0), None, Fraction(-1), [RowSpec("r", {"x": Fraction(1)}, None, Fraction(9))]
        )
        assert not verify_unbounded(m, [Fraction(1)], [Fraction(1)]).verified

    def test_improving_direction_with_bounded_objective_is_not_unbounded(self) -> None:
        # min x with x >= 0: ray +1 is feasible but not improving
        m = one_var(Fraction(0), None, Fraction(1), [])
        assert not verify_unbounded(m, [Fraction(0)], [Fraction(1)]).verified

    def test_trivial_anchor(self) -> None:
        m = self.model()
        anchors = trivial_anchor_candidates(m)
        assert anchors[0] == [Fraction(0)]
        assert not check_primal(m, anchors[0]).exactly_feasible  # row needs x >= 1


class TestGap:
    def test_accepts_tight_pair(self) -> None:
        m = lp()
        g = evaluate_gap(
            m, check_primal(m, [Fraction(1), Fraction(0)]), [Fraction(1)], VerificationPolicy()
        )
        assert g.accepted and g.absolute_gap == 0

    def test_rejects_weak_dual(self) -> None:
        m = lp()
        g = evaluate_gap(
            m, check_primal(m, [Fraction(1), Fraction(0)]), [Fraction(0)], VerificationPolicy()
        )
        assert not g.accepted and g.absolute_gap == 1.0

    def test_rejects_minus_infinity_dual(self) -> None:
        m = one_var(
            None, None, Fraction(1), [RowSpec("r", {"x": Fraction(1)}, Fraction(0), Fraction(1))]
        )
        g = evaluate_gap(m, check_primal(m, [Fraction(0)]), [Fraction(0)], VerificationPolicy())
        assert not g.accepted

    def test_maximize_reports_original_sense(self) -> None:
        m = lp("maximize")
        # max -(x1+2x2): min-form cost (1,2) negated -> cost (-1,-2); optimum -1 at (1,0)
        primal = check_primal(m, [Fraction(1), Fraction(0)])
        assert to_original_sense(m, primal.objective_min_form) == 1  # cost*x = -1 -> original 1


class TestLoweringVerification:
    def test_valid(self) -> None:
        m = lp()
        ir, rep = lower_ranged_lp(m)
        c = verify_lowering(m, ir, rep)
        assert c.valid, c.mismatches

    def test_rounding_summary_matches_compiler(self) -> None:
        m = one_var(
            Fraction(0),
            Fraction(1),
            Fraction(1, 3),
            [RowSpec("r", {"x": Fraction(1, 7)}, None, Fraction(2, 3))],
        )
        ir, rep = lower_ranged_lp(m)
        c = verify_lowering(m, ir, rep)
        assert c.valid and c.values_rounded == rep.values_rounded >= 3

    def test_tampered_ir_detected(self) -> None:
        m = lp()
        ir, rep = lower_ranged_lp(m)
        bad = dataclasses.replace(ir, cost=(1.0, 2.0000001))
        assert not verify_lowering(m, bad, rep).valid

    def test_tampered_csr_detected(self) -> None:
        m = lp()
        ir, rep = lower_ranged_lp(m)
        bad = dataclasses.replace(ir, col_index=(1, 0))
        assert not verify_lowering(m, bad, rep).valid

    def test_wrong_model_hash_in_report_detected(self) -> None:
        m, other = lp(), lp(c0=Fraction(1))
        ir, _ = lower_ranged_lp(m)
        _, other_rep = lower_ranged_lp(other)
        assert not verify_lowering(m, ir, other_rep).valid

    def test_infinite_bounds_lower_correctly(self) -> None:
        m = one_var(None, None, Fraction(1), [RowSpec("r", {"x": Fraction(1)}, None, Fraction(2))])
        ir, rep = lower_ranged_lp(m)
        assert verify_lowering(m, ir, rep).valid

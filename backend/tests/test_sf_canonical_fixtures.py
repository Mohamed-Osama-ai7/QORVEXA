"""Analytical fixtures for the canonical ranged LP, lowering and dual bound (S3)."""

from __future__ import annotations

import math
from fractions import Fraction

import pytest

from qorvexa.contracts.solver_runtime import RoundingSummary
from qorvexa.solver_fabric.canonical_lp import (
    COEFFICIENT_OVERFLOW,
    COEFFICIENT_UNDERFLOW,
    CanonicalLPError,
    ColumnSpec,
    RowSpec,
    build_canonical_lp,
    lower_ranged_lp,
)
from qorvexa.solver_fabric.exact import Interval, evaluate_infimum_support


def lp(sense: str = "minimize", c0: Fraction = Fraction(0)):  # type: ignore[no-untyped-def]
    # min x1 + 2 x2 ; 1 <= x1 + x2 <= 4 ; x in [0,3]^2  (optimum 1 at x=(1,0))
    return build_canonical_lp(
        [ColumnSpec("x1", Fraction(0), Fraction(3)), ColumnSpec("x2", Fraction(0), Fraction(3))],
        objective_cost={"x1": Fraction(1), "x2": Fraction(2)},
        objective_constant=c0,
        objective_sense=sense,  # type: ignore[arg-type]
        rows=[RowSpec("r1", {"x1": Fraction(1), "x2": Fraction(1)}, Fraction(1), Fraction(4))],
    )


def dual_bound(c0: Fraction, y: Fraction) -> Fraction:
    """D(y) = c0 + sum_j h(X_j, r_j) + sum_i h(R_i, y_i), r = c - A^T y (inf form)."""
    cost = [Fraction(1), Fraction(2)]
    r = [cost[0] - y, cost[1] - y]
    total = c0
    for rj in r:
        h = evaluate_infimum_support(Interval.from_bounds(0, 3), rj)
        assert h.is_finite and h.value is not None
        total += h.value
    hr = evaluate_infimum_support(Interval.from_bounds(1, 4), y)
    assert hr.is_finite and hr.value is not None
    return total + hr.value


class TestAnalyticalFixture:
    def test_structure_and_min_form(self) -> None:
        m = lp()
        assert m.cost == (Fraction(1), Fraction(2)) and m.rows == (
            ((0, Fraction(1)), (1, Fraction(1))),
        )
        assert (m.row_lower, m.row_upper) == ((Fraction(1),), (Fraction(4),))

    def test_maximize_negates_cost_and_offset(self) -> None:
        m = lp("maximize", Fraction(5))
        assert (
            m.cost == (Fraction(-1), Fraction(-2))
            and m.offset == -5
            and m.original_sense == "maximize"
        )

    def test_dual_bound_is_valid_and_tight_at_y1(self) -> None:
        for y in (
            Fraction(-3),
            Fraction(-1),
            Fraction(0),
            Fraction(1, 2),
            Fraction(1),
            Fraction(3, 2),
            Fraction(2),
            Fraction(5),
        ):
            assert dual_bound(Fraction(0), y) <= 1
        assert dual_bound(Fraction(0), Fraction(1)) == 1
        assert dual_bound(Fraction(0), Fraction(2)) == -1  # wrong-sign direction loses bound
        assert dual_bound(Fraction(7), Fraction(1)) == 8  # c0 shifts D by exactly c0

    def test_infinite_bounds_and_zero_multiplier_are_finite_free(self) -> None:
        free = Interval.from_bounds(None, None)
        assert evaluate_infimum_support(free, 0).value == 0
        assert not evaluate_infimum_support(free, 1).is_finite


class TestLoweringFixtures:
    def test_infinite_bounds_lower_to_inf_and_ranges_preserved(self) -> None:
        m = build_canonical_lp(
            [ColumnSpec("x", None, None)],
            objective_cost={"x": Fraction(1)},
            objective_constant=Fraction(0),
            objective_sense="minimize",
            rows=[
                RowSpec("r", {"x": Fraction(1)}, None, Fraction(2)),
                RowSpec("e", {"x": Fraction(1)}, Fraction(3), Fraction(3)),
            ],
        )
        ir, rep = lower_ranged_lp(m)
        assert ir.col_lower == (-math.inf,) and ir.col_upper == (math.inf,)
        assert ir.row_lower == (-math.inf, 3.0) and ir.row_upper == (2.0, 3.0)
        assert rep.values_rounded == 0

    def test_zero_coefficients_dropped(self) -> None:
        m = build_canonical_lp(
            [ColumnSpec("x", Fraction(0), Fraction(1)), ColumnSpec("y", Fraction(0), Fraction(1))],
            objective_cost={},
            objective_constant=Fraction(0),
            objective_sense="minimize",
            rows=[RowSpec("r", {"x": Fraction(0), "y": Fraction(2)}, None, Fraction(1))],
        )
        ir, _ = lower_ranged_lp(m)
        assert ir.col_index == (1,) and ir.values == (2.0,)

    def test_rounding_reported_and_summary_dto_valid(self) -> None:
        m = build_canonical_lp(
            [ColumnSpec("x", Fraction(0), Fraction(1))],
            objective_cost={"x": Fraction(1, 3)},
            objective_constant=Fraction(0),
            objective_sense="minimize",
            rows=[],
        )
        _, rep = lower_ranged_lp(m)
        assert rep.values_rounded >= 1 and rep.max_abs_rounding_error > 0
        assert isinstance(rep.rounding_summary(), RoundingSummary)

    def test_overflow_and_underflow_typed_with_location(self) -> None:
        for coef, code in (
            (Fraction(10**400), COEFFICIENT_OVERFLOW),
            (Fraction(1, 10**400), COEFFICIENT_UNDERFLOW),
        ):
            m = build_canonical_lp(
                [ColumnSpec("x", Fraction(0), Fraction(1))],
                objective_cost={"x": coef},
                objective_constant=Fraction(0),
                objective_sense="minimize",
                rows=[],
            )
            with pytest.raises(CanonicalLPError) as exc:
                lower_ranged_lp(m)
            assert exc.value.code == code and "cost[x]" in exc.value.location

    def test_hashes_deterministic_and_sensitive(self) -> None:
        a, b = lower_ranged_lp(lp())[1], lower_ranged_lp(lp())[1]
        assert a.model_sha256 == b.model_sha256 and a.ir_sha256 == b.ir_sha256
        c = lower_ranged_lp(lp(c0=Fraction(1)))[1]
        assert c.model_sha256 != a.model_sha256 and c.ir_sha256 != a.ir_sha256

    def test_report_makes_no_verification_claim(self) -> None:
        rep = lower_ranged_lp(lp())[1]
        assert not hasattr(rep, "lowering_evidence") and not hasattr(rep, "verified")

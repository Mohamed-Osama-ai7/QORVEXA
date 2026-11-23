"""Tests for canonical exact ranged-LP model builder and binary64 lowering (S3)."""

import math
from fractions import Fraction

import pytest

from qorvexa.solver_fabric.canonical_lp import (
    BLANK_ID,
    COEFFICIENT_OVERFLOW,
    COEFFICIENT_UNDERFLOW,
    DUPLICATE_ROW,
    DUPLICATE_VARIABLE,
    MODEL_TOO_LARGE,
    ROW_BOUND_CONTRADICTION,
    VALUE_TOO_LARGE,
    VARIABLE_BOUND_CONTRADICTION,
    CanonicalLPError,
    CanonicalRangedLP,
    ColumnSpec,
    ModelLimits,
    RowSpec,
    build_canonical_lp,
    lower_ranged_lp,
)


class TestCanonicalLPBuilder:
    """Tests exact canonical ranged LP validation, construction, and invariants."""

    def test_build_simple_lp(self) -> None:
        cols = [
            ColumnSpec("x0", Fraction(0), Fraction(10)),
            ColumnSpec("x1", Fraction(0), None),
        ]
        rows = [
            RowSpec("r0", {"x0": Fraction(1), "x1": Fraction(2)}, Fraction(3), None),
            RowSpec("r1", {"x0": Fraction(1), "x1": Fraction(-1)}, None, Fraction(5)),
        ]
        lp = build_canonical_lp(
            cols,
            objective_cost={"x0": Fraction(1), "x1": Fraction(1)},
            objective_constant=Fraction(0),
            objective_sense="minimize",
            rows=rows,
        )
        assert isinstance(lp, CanonicalRangedLP)
        assert lp.num_variables == 2
        assert lp.num_rows == 2
        assert lp.num_nonzeros == 4
        assert lp.original_sense == "minimize"
        assert lp.cost == (Fraction(1), Fraction(1))
        assert lp.offset == Fraction(0)
        assert len(lp.model_sha256) == 64

    def test_maximization_sense_negates_cost_and_offset(self) -> None:
        cols = [ColumnSpec("x", Fraction(0), None)]
        lp = build_canonical_lp(
            cols,
            objective_cost={"x": Fraction(5)},
            objective_constant=Fraction(10),
            objective_sense="maximize",
            rows=[],
        )
        assert lp.original_sense == "maximize"
        assert lp.cost == (Fraction(-5),)
        assert lp.offset == Fraction(-10)

    def test_zero_coefficients_dropped(self) -> None:
        cols = [ColumnSpec("x", Fraction(0), None), ColumnSpec("y", Fraction(0), None)]
        rows = [
            RowSpec("r0", {"x": Fraction(0), "y": Fraction(3)}, Fraction(0), Fraction(10)),
        ]
        lp = build_canonical_lp(
            cols,
            objective_cost={"x": Fraction(0), "y": Fraction(2)},
            objective_constant=Fraction(0),
            objective_sense="minimize",
            rows=rows,
        )
        assert lp.cost == (Fraction(0), Fraction(2))
        assert lp.rows[0] == ((1, Fraction(3)),)
        assert lp.num_nonzeros == 1

    def test_duplicate_column_id_rejected(self) -> None:
        cols = [ColumnSpec("x", Fraction(0), None), ColumnSpec("x", Fraction(0), None)]
        with pytest.raises(CanonicalLPError) as exc_info:
            build_canonical_lp(
                cols,
                objective_cost={"x": Fraction(1)},
                objective_constant=Fraction(0),
                objective_sense="minimize",
                rows=[],
            )
        assert exc_info.value.code == DUPLICATE_VARIABLE

    def test_blank_column_id_rejected(self) -> None:
        cols = [ColumnSpec("  ", Fraction(0), None)]
        with pytest.raises(CanonicalLPError) as exc_info:
            build_canonical_lp(
                cols,
                objective_cost={},
                objective_constant=Fraction(0),
                objective_sense="minimize",
                rows=[],
            )
        assert exc_info.value.code == BLANK_ID

    def test_duplicate_row_id_rejected(self) -> None:
        cols = [ColumnSpec("x", Fraction(0), None)]
        rows = [
            RowSpec("r0", {"x": Fraction(1)}, Fraction(0), None),
            RowSpec("r0", {"x": Fraction(2)}, Fraction(0), None),
        ]
        with pytest.raises(CanonicalLPError) as exc_info:
            build_canonical_lp(
                cols,
                objective_cost={"x": Fraction(1)},
                objective_constant=Fraction(0),
                objective_sense="minimize",
                rows=rows,
            )
        assert exc_info.value.code == DUPLICATE_ROW

    def test_column_bound_contradiction_rejected(self) -> None:
        cols = [ColumnSpec("x", Fraction(10), Fraction(5))]
        with pytest.raises(CanonicalLPError) as exc_info:
            build_canonical_lp(
                cols,
                objective_cost={"x": Fraction(1)},
                objective_constant=Fraction(0),
                objective_sense="minimize",
                rows=[],
            )
        assert exc_info.value.code == VARIABLE_BOUND_CONTRADICTION

    def test_row_bound_contradiction_rejected(self) -> None:
        cols = [ColumnSpec("x", Fraction(0), None)]
        rows = [RowSpec("r0", {"x": Fraction(1)}, Fraction(10), Fraction(5))]
        with pytest.raises(CanonicalLPError) as exc_info:
            build_canonical_lp(
                cols,
                objective_cost={"x": Fraction(1)},
                objective_constant=Fraction(0),
                objective_sense="minimize",
                rows=rows,
            )
        assert exc_info.value.code == ROW_BOUND_CONTRADICTION

    def test_empty_infeasible_row_detected(self) -> None:
        cols = [ColumnSpec("x", Fraction(0), None)]
        # Row with all zero coefficients: 0*x = 0, but lower bound is 5.
        # This is impossible (empty infeasible row).
        rows = [RowSpec("r_infeas", {"x": Fraction(0)}, Fraction(5), None)]
        lp = build_canonical_lp(
            cols,
            objective_cost={"x": Fraction(1)},
            objective_constant=Fraction(0),
            objective_sense="minimize",
            rows=rows,
        )
        assert lp.empty_infeasible_rows == ("r_infeas",)

    def test_resource_limits_columns(self) -> None:
        cols = [ColumnSpec(f"x{i}", Fraction(0), None) for i in range(5)]
        limits = ModelLimits(max_columns=3)
        with pytest.raises(CanonicalLPError) as exc_info:
            build_canonical_lp(
                cols,
                objective_cost={},
                objective_constant=Fraction(0),
                objective_sense="minimize",
                rows=[],
                limits=limits,
            )
        assert exc_info.value.code == MODEL_TOO_LARGE

    def test_resource_limits_rational_bits(self) -> None:
        big_frac = Fraction(10**200, 1)
        cols = [ColumnSpec("x", Fraction(0), big_frac)]
        limits = ModelLimits(max_rational_bits=100)
        with pytest.raises(CanonicalLPError) as exc_info:
            build_canonical_lp(
                cols,
                objective_cost={},
                objective_constant=Fraction(0),
                objective_sense="minimize",
                rows=[],
                limits=limits,
            )
        assert exc_info.value.code == VALUE_TOO_LARGE


class TestCanonicalLPLowering:
    """Tests lowering from exact rational LP to binary64 CSR IR."""

    def test_lower_exact_lp(self) -> None:
        cols = [
            ColumnSpec("x0", Fraction(0), Fraction(10)),
            ColumnSpec("x1", Fraction(1, 2), None),
        ]
        rows = [
            RowSpec("r0", {"x0": Fraction(1), "x1": Fraction(2)}, Fraction(3), None),
        ]
        lp = build_canonical_lp(
            cols,
            objective_cost={"x0": Fraction(1), "x1": Fraction(1, 4)},
            objective_constant=Fraction(0),
            objective_sense="minimize",
            rows=rows,
        )
        ir, report = lower_ranged_lp(lp)
        assert ir.num_variables == 2
        assert ir.cost == (1.0, 0.25)
        assert ir.col_lower == (0.0, 0.5)
        assert ir.col_upper[0] == 10.0
        assert math.isinf(ir.col_upper[1])
        assert ir.row_lower == (3.0,)
        assert math.isinf(ir.row_upper[0])
        assert ir.row_start == (0, 2)
        assert ir.col_index == (0, 1)
        assert ir.values == (1.0, 2.0)
        assert len(ir.ir_sha256) == 64

        # Verify RoundingSummary bridge
        summary = report.rounding_summary()
        assert summary.coefficients_total == report.values_total
        assert summary.coefficients_rounded == 0
        assert summary.max_abs_rounding_error == "0"

    def test_rounding_error_reported_for_inexact_fraction(self) -> None:
        cols = [ColumnSpec("x", Fraction(0), None)]
        rows = [RowSpec("r0", {"x": Fraction(1, 3)}, Fraction(0), None)]
        lp = build_canonical_lp(
            cols,
            objective_cost={"x": Fraction(1)},
            objective_constant=Fraction(0),
            objective_sense="minimize",
            rows=rows,
        )
        _ir, report = lower_ranged_lp(lp)
        assert report.values_rounded == 1
        assert report.max_abs_rounding_error > 0
        summary = report.rounding_summary()
        assert summary.coefficients_rounded == 1

    def test_lowering_overflow_rejected(self) -> None:
        huge = Fraction(10**400, 1)
        cols = [ColumnSpec("x", Fraction(0), None)]
        rows = [RowSpec("r0", {"x": huge}, Fraction(0), None)]
        lp = build_canonical_lp(
            cols,
            objective_cost={"x": Fraction(1)},
            objective_constant=Fraction(0),
            objective_sense="minimize",
            rows=rows,
        )
        with pytest.raises(CanonicalLPError) as exc_info:
            lower_ranged_lp(lp)
        assert exc_info.value.code == COEFFICIENT_OVERFLOW

    def test_lowering_underflow_rejected(self) -> None:
        tiny = Fraction(1, 10**400)
        cols = [ColumnSpec("x", Fraction(0), None)]
        rows = [RowSpec("r0", {"x": tiny}, Fraction(0), None)]
        lp = build_canonical_lp(
            cols,
            objective_cost={"x": Fraction(1)},
            objective_constant=Fraction(0),
            objective_sense="minimize",
            rows=rows,
        )
        with pytest.raises(CanonicalLPError) as exc_info:
            lower_ranged_lp(lp)
        assert exc_info.value.code == COEFFICIENT_UNDERFLOW

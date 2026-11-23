"""Encoding A normalization tests (exact constraint-to-row semantics)."""

from __future__ import annotations

import json
from fractions import Fraction
from typing import Any

import pytest

from qorvexa.solver_fabric.affine import AffineForm
from qorvexa.solver_fabric.canonical_lp import CanonicalRangedLP
from qorvexa.solver_fabric.encoding_a import (
    BOUND_AMBIGUOUS,
    BOUND_MISSING,
    COMPARISON_NODE_UNSUPPORTED,
    RANGE_INVERTED,
    RELATION_NOT_SUPPORTED,
    STRICT_INEQUALITY_UNSUPPORTED,
    NormalizationError,
    build_model_from_derived,
    normalize_constraint,
)
from qorvexa.solver_fabric.lp_intake import DerivedConstraint, derive_problem_forms
from qorvexa.solver_fabric.raw_ingress import parse_problem_spec_json


def var(i: str = "VAR-01") -> dict[str, Any]:
    return {"type": "variable_ref", "variableId": i}


def lit(v: Any) -> dict[str, Any]:
    return {"type": "literal", "dataType": "real", "value": v}


def binop(op: str, left: Any, right: Any) -> dict[str, Any]:
    return {"type": "binary_op", "operator": op, "left": left, "right": right}


def variable(i: str, lo: Any = 0, hi: Any = 10) -> dict[str, Any]:
    return {
        "id": i,
        "name": i,
        "symbol": i,
        "domain": "continuous",
        "lowerBound": lo,
        "upperBound": hi,
    }


def doc(
    ast: Any, relation: str, bound: Any, upper: Any = None, direction: str = "minimize"
) -> dict[str, Any]:
    c: dict[str, Any] = {
        "id": "CON-01",
        "name": "c",
        "category": "capacity",
        "relation": relation,
        "expressionSummary": "never executed: __import__('os').system('x')",
        "expressionAst": ast,
        "boundValue": bound,
    }
    if upper is not None:
        c["upperBoundValue"] = upper
    return {
        "title": "t",
        "problemDescription": "d",
        "problemType": "linear_programming",
        "timeHorizon": {"isSpecified": False},
        "solverHandoffNotes": {"recommendedSolverFamily": "lp"},
        "decisionVariables": [variable("VAR-01"), variable("VAR-02")],
        "objectives": [
            {
                "id": "OBJ-01",
                "direction": direction,
                "name": "o",
                "expressionSummary": "s",
                "expressionAst": binop("add", binop("add", var("VAR-01"), lit(5)), var("VAR-02")),
            }
        ],
        "constraints": [c],
    }


def model_of(d: dict[str, Any]) -> CanonicalRangedLP:
    return build_model_from_derived(derive_problem_forms(parse_problem_spec_json(json.dumps(d))))


E = binop("add", binop("mul", lit(2), var("VAR-01")), lit(3))  # 2 x + 3


class TestRelations:
    def test_le(self) -> None:
        m = model_of(doc(E, "<=", 10))
        assert (m.row_lower[0], m.row_upper[0]) == (None, Fraction(7))
        assert m.rows[0] == ((0, Fraction(2)),)

    def test_ge(self) -> None:
        m = model_of(doc(E, ">=", 10))
        assert (m.row_lower[0], m.row_upper[0]) == (Fraction(7), None)

    def test_eq(self) -> None:
        m = model_of(doc(E, "==", 10))
        assert (m.row_lower[0], m.row_upper[0]) == (Fraction(7), Fraction(7))

    def test_range(self) -> None:
        m = model_of(doc(E, "range", 1, 9))
        assert (m.row_lower[0], m.row_upper[0]) == (Fraction(-2), Fraction(6))

    def test_degenerate_range_allowed(self) -> None:
        m = model_of(doc(E, "range", 4, 4))
        assert (m.row_lower[0], m.row_upper[0]) == (Fraction(1), Fraction(1))

    def test_exact_rational_arithmetic(self) -> None:
        d = doc(binop("add", var(), lit(0.2)), "<=", 0.3)
        assert model_of(d).row_upper[0] == Fraction(0.3) - Fraction(0.2)

    def test_constant_only_row_is_kept_and_flagged(self) -> None:
        m = model_of(doc(lit(3), ">=", 5))
        assert m.row_lower[0] == Fraction(2) and m.rows[0] == ()
        assert m.empty_infeasible_rows == ("CON-01",)

    def test_expression_summary_is_never_used(self) -> None:
        # The hostile summary in doc() has no effect on the derived row.
        assert model_of(doc(E, "<=", 10)).row_upper[0] == Fraction(7)


class TestObjective:
    def test_minimize_constant_preserved(self) -> None:
        m = model_of(doc(E, "<=", 10))
        assert m.offset == 5 and m.cost == (Fraction(1), Fraction(1))
        assert m.original_sense == "minimize"

    def test_maximize_semantics_preserved(self) -> None:
        m = model_of(doc(E, "<=", 10, direction="maximize"))
        assert m.original_sense == "maximize"
        assert m.offset == -5 and m.cost == (Fraction(-1), Fraction(-1))


def dc(
    relation: str, bound: Any, upper: Any = None, shape: str = "EXPRESSION"
) -> DerivedConstraint:
    form = AffineForm(Fraction(1), {"x": Fraction(1)}, frozenset({"x"}))
    return DerivedConstraint(
        constraint_id="c",
        relation=relation,
        shape=shape,  # type: ignore[arg-type]
        expression=form if shape == "EXPRESSION" else None,
        left=form if shape != "EXPRESSION" else None,
        right=form if shape != "EXPRESSION" else None,
        comparison_operator="<=" if shape != "EXPRESSION" else None,
        bound_value=None if bound is None else Fraction(bound),
        upper_bound_value=None if upper is None else Fraction(upper),
    )


class TestRejections:
    @pytest.mark.parametrize("rel", ["<=", ">=", "=="])
    def test_missing_bound(self, rel: str) -> None:
        with pytest.raises(NormalizationError) as exc:
            normalize_constraint(dc(rel, None))
        assert exc.value.code == BOUND_MISSING

    def test_range_missing_either_bound(self) -> None:
        for args in ((None, 3), (1, None)):
            with pytest.raises(NormalizationError) as exc:
                normalize_constraint(dc("range", *args))
            assert exc.value.code == BOUND_MISSING

    @pytest.mark.parametrize("rel", ["<=", ">=", "=="])
    def test_ambiguous_upper_bound(self, rel: str) -> None:
        with pytest.raises(NormalizationError) as exc:
            normalize_constraint(dc(rel, 1, 2))
        assert exc.value.code == BOUND_AMBIGUOUS

    def test_inverted_range(self) -> None:
        with pytest.raises(NormalizationError) as exc:
            normalize_constraint(dc("range", 5, 1))
        assert exc.value.code == RANGE_INVERTED

    @pytest.mark.parametrize("rel", ["<", ">", "!="])
    def test_strict_and_not_equal(self, rel: str) -> None:
        with pytest.raises(NormalizationError) as exc:
            normalize_constraint(dc(rel, 1))
        assert exc.value.code == STRICT_INEQUALITY_UNSUPPORTED

    def test_unknown_relation(self) -> None:
        with pytest.raises(NormalizationError) as exc:
            normalize_constraint(dc("approx", 1))
        assert exc.value.code == RELATION_NOT_SUPPORTED

    def test_comparison_root_rejected_directly(self) -> None:
        with pytest.raises(NormalizationError) as exc:
            normalize_constraint(dc("<=", 1, shape="COMPARISON_ROOT"))
        assert exc.value.code == COMPARISON_NODE_UNSUPPORTED

    def test_comparison_node_rejected_end_to_end(self) -> None:
        cmp = {"type": "comparison", "operator": "<=", "left": var(), "right": lit(3)}
        with pytest.raises(NormalizationError) as exc:
            model_of(doc(cmp, "<=", 1))
        assert exc.value.code == COMPARISON_NODE_UNSUPPORTED

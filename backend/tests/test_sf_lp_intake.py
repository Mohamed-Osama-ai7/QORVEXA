"""Tests for strict continuous-LP validation and exact affine derivation (S3)."""

from __future__ import annotations

import json
from fractions import Fraction
from typing import Any

import pytest

from qorvexa.contracts.solver_fabric import GeneralizedProblemSpec
from qorvexa.solver_fabric.affine import (
    AffineEvaluationError,
    ContinuousVariableOnlyError,
    NonlinearMultiplicationError,
    UnsupportedLiteralError,
    UnsupportedParameterRefError,
)
from qorvexa.solver_fabric.exact import BOUND_CONTRADICTION_EXACT
from qorvexa.solver_fabric.lp_intake import (
    BLANK_ID,
    CONSTRAINT_BOUND_FIELDS,
    CONSTRAINT_MISSING_AST,
    DISCRETE_VALUES_NOT_ALLOWED,
    DUPLICATE_ID,
    HARDNESS_UNSUPPORTED,
    MULTI_OBJECTIVE_UNSUPPORTED,
    OBJECTIVE_COUNT,
    OBJECTIVE_MISSING_AST,
    PROBLEM_TYPE_UNSUPPORTED,
    RELATION_UNSUPPORTED,
    TOO_MANY_CONSTRAINTS,
    TOO_MANY_VARIABLES,
    VARIABLE_DOMAIN_UNSUPPORTED,
    IntakeBoundContradictionError,
    IntakeError,
    IntakeLimits,
    derive_problem_forms,
    validate_lp_problem,
)
from qorvexa.solver_fabric.raw_ingress import parse_problem_spec_json


def var(i: str = "VAR-01") -> dict[str, Any]:
    return {"type": "variable_ref", "variableId": i}


def lit(v: Any, dt: str = "real") -> dict[str, Any]:
    return {"type": "literal", "dataType": dt, "value": v}


def binop(op: str, left: Any, right: Any) -> dict[str, Any]:
    return {"type": "binary_op", "operator": op, "left": left, "right": right}


def variable(i: str, lo: Any = 0, hi: Any = 10, domain: str = "continuous") -> dict[str, Any]:
    return {
        "id": i,
        "name": i,
        "symbol": i,
        "domain": domain,
        "lowerBound": lo,
        "upperBound": hi,
    }


def constraint(
    i: str = "CON-01", ast: Any = None, relation: str = "<=", bound: Any = 5, **extra: Any
) -> dict[str, Any]:
    c: dict[str, Any] = {
        "id": i,
        "name": i,
        "category": "capacity",
        "relation": relation,
        "expressionSummary": "s",
        "expressionAst": var() if ast is None else ast,
        "boundValue": bound,
    }
    c.update(extra)
    return c


def doc(**over: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "title": "t",
        "problemDescription": "d",
        "problemType": "linear_programming",
        "timeHorizon": {"isSpecified": False},
        "solverHandoffNotes": {"recommendedSolverFamily": "lp"},
        "decisionVariables": [variable("VAR-01"), variable("VAR-02")],
        "objectives": [
            {
                "id": "OBJ-01",
                "direction": "minimize",
                "name": "o",
                "expressionSummary": "s",
                "expressionAst": binop("add", var("VAR-01"), binop("mul", lit(2), var("VAR-02"))),
            }
        ],
        "constraints": [constraint()],
    }
    d.update(over)
    return d


def spec_of(d: dict[str, Any]) -> GeneralizedProblemSpec:
    return parse_problem_spec_json(json.dumps(d))


def code_of(d: dict[str, Any]) -> str:
    with pytest.raises(IntakeError) as exc:
        derive_problem_forms(spec_of(d))
    return exc.value.code


class TestObjectiveRules:
    def test_exactly_one_objective_accepted(self) -> None:
        p = derive_problem_forms(spec_of(doc()))
        assert p.objective.direction == "minimize"
        assert dict(p.objective.form.coefficients) == {"VAR-01": Fraction(1), "VAR-02": Fraction(2)}

    def test_zero_objectives_rejected_not_treated_as_feasibility(self) -> None:
        assert code_of(doc(objectives=[])) == OBJECTIVE_COUNT

    def test_two_objectives_rejected(self) -> None:
        d = doc()
        d["objectives"].append({**d["objectives"][0], "id": "OBJ-02"})
        assert code_of(d) == OBJECTIVE_COUNT

    def test_objective_without_ast_rejected(self) -> None:
        d = doc()
        del d["objectives"][0]["expressionAst"]
        assert code_of(d) == OBJECTIVE_MISSING_AST

    def test_maximize_direction_preserved(self) -> None:
        d = doc()
        d["objectives"][0]["direction"] = "maximize"
        assert derive_problem_forms(spec_of(d)).objective.direction == "maximize"

    def test_objective_constant_term(self) -> None:
        d = doc()
        d["objectives"][0]["expressionAst"] = binop("add", var("VAR-01"), lit(7, "integer"))
        form = derive_problem_forms(spec_of(d)).objective.form
        assert form.constant == 7 and dict(form.coefficients) == {"VAR-01": Fraction(1)}


class TestProblemLevelRules:
    def test_problem_type_must_be_explicit_lp(self) -> None:
        d = doc()
        del d["problemType"]  # M1 default is 'unknown'
        assert code_of(d) == PROBLEM_TYPE_UNSUPPORTED
        for t in ("mixed_integer_linear_programming", "nonlinear_programming", "unknown"):
            assert code_of(doc(problemType=t)) == PROBLEM_TYPE_UNSUPPORTED

    def test_multi_objective_strategy_must_be_single_or_absent(self) -> None:
        assert code_of(doc(multiObjectiveStrategy="weighted_sum")) == MULTI_OBJECTIVE_UNSUPPORTED
        derive_problem_forms(spec_of(doc(multiObjectiveStrategy="single_objective")))

    def test_size_limits(self) -> None:
        spec = spec_of(doc())
        with pytest.raises(IntakeError) as exc:
            derive_problem_forms(spec, IntakeLimits(max_variables=1))
        assert exc.value.code == TOO_MANY_VARIABLES
        with pytest.raises(IntakeError) as exc2:
            derive_problem_forms(spec, IntakeLimits(max_constraints=0))
        assert exc2.value.code == TOO_MANY_CONSTRAINTS


class TestVariableRules:
    @pytest.mark.parametrize(
        "domain",
        ["integer", "binary", "semi_continuous", "semi_integer", "categorical", "discrete_set"],
    )
    def test_non_continuous_domains_rejected(self, domain: str) -> None:
        d = doc(decisionVariables=[variable("VAR-01", domain=domain), variable("VAR-02")])
        assert code_of(d) == VARIABLE_DOMAIN_UNSUPPORTED

    def test_discrete_values_rejected(self) -> None:
        v = variable("VAR-01")
        v["discreteValues"] = [1.0, 2.0]
        assert (
            code_of(doc(decisionVariables=[v, variable("VAR-02")])) == DISCRETE_VALUES_NOT_ALLOWED
        )

    def test_duplicate_and_blank_ids(self) -> None:
        d = doc(decisionVariables=[variable("VAR-01"), variable("VAR-01")])
        assert code_of(d) == DUPLICATE_ID
        d = doc(decisionVariables=[variable("  "), variable("VAR-02")])
        assert code_of(d) == BLANK_ID
        d = doc()
        d["constraints"] = [constraint("C"), constraint("C")]
        assert code_of(d) == DUPLICATE_ID

    def test_exact_bound_contradiction_is_typed(self) -> None:
        d = doc(decisionVariables=[variable("VAR-01", lo=5, hi=2), variable("VAR-02")])
        with pytest.raises(IntakeBoundContradictionError) as exc:
            derive_problem_forms(spec_of(d))
        e = exc.value
        assert e.code == BOUND_CONTRADICTION_EXACT
        assert (e.variable_id, e.lower, e.upper) == ("VAR-01", Fraction(5), Fraction(2))
        assert isinstance(e, IntakeError)

    def test_equal_and_open_bounds_are_fine(self) -> None:
        d = doc(
            decisionVariables=[variable("VAR-01", lo=3, hi=3), variable("VAR-02", lo=None, hi=None)]
        )
        p = derive_problem_forms(spec_of(d))
        assert (p.variables[0].lower, p.variables[0].upper) == (Fraction(3), Fraction(3))
        assert (p.variables[1].lower, p.variables[1].upper) == (None, None)

    def test_bounds_are_exact_binary64_values(self) -> None:
        d = doc(decisionVariables=[variable("VAR-01", lo=0.1, hi=0.3), variable("VAR-02")])
        p = derive_problem_forms(spec_of(d))
        assert p.variables[0].lower == Fraction(0.1) and p.variables[0].upper == Fraction(0.3)

    def test_hyphenated_uppercase_ids_supported_end_to_end(self) -> None:
        p = derive_problem_forms(spec_of(doc()))
        assert [v.variable_id for v in p.variables] == ["VAR-01", "VAR-02"]
        assert p.constraints[0].constraint_id == "CON-01"


class TestConstraintRules:
    @pytest.mark.parametrize("relation", ["<=", ">=", "=="])
    def test_supported_single_bound_relations(self, relation: str) -> None:
        p = derive_problem_forms(spec_of(doc(constraints=[constraint(relation=relation)])))
        assert p.constraints[0].relation == relation

    def test_range_requires_both_bounds(self) -> None:
        ok = constraint(relation="range", bound=1, upperBoundValue=4)
        p = derive_problem_forms(spec_of(doc(constraints=[ok])))
        assert (p.constraints[0].bound_value, p.constraints[0].upper_bound_value) == (
            Fraction(1),
            Fraction(4),
        )
        assert (
            code_of(doc(constraints=[constraint(relation="range", bound=1)]))
            == CONSTRAINT_BOUND_FIELDS
        )

    def test_upper_bound_only_valid_with_range(self) -> None:
        bad = constraint(relation="<=", upperBoundValue=9)
        assert code_of(doc(constraints=[bad])) == CONSTRAINT_BOUND_FIELDS

    @pytest.mark.parametrize("relation", ["<", ">", "!=", "in_set", "sos1", "sos2", "logical"])
    def test_unsupported_relations(self, relation: str) -> None:
        assert code_of(doc(constraints=[constraint(relation=relation)])) == RELATION_UNSUPPORTED

    @pytest.mark.parametrize("hardness", ["soft", "unknown"])
    def test_only_hard_constraints(self, hardness: str) -> None:
        assert code_of(doc(constraints=[constraint(hardness=hardness)])) == HARDNESS_UNSUPPORTED

    def test_constraint_without_ast_rejected(self) -> None:
        c = constraint()
        del c["expressionAst"]
        assert code_of(doc(constraints=[c])) == CONSTRAINT_MISSING_AST

    def test_expression_summary_is_never_used_for_derivation(self) -> None:
        c = constraint(ast=var("VAR-01"), expressionSummary="VAR-02 * 1000 <= 1")
        p = derive_problem_forms(spec_of(doc(constraints=[c])))
        assert dict(p.constraints[0].expression.coefficients) == {"VAR-01": Fraction(1)}  # type: ignore[union-attr]


class TestExactDerivation:
    def test_expression_shape(self) -> None:
        ast = binop("sub", binop("mul", lit(3, "integer"), var("VAR-01")), var("VAR-02"))
        p = derive_problem_forms(spec_of(doc(constraints=[constraint(ast=ast)])))
        c = p.constraints[0]
        assert c.shape == "EXPRESSION" and c.left is None and c.right is None
        assert c.expression is not None
        assert dict(c.expression.coefficients) == {"VAR-01": Fraction(3), "VAR-02": Fraction(-1)}

    def test_comparison_root_shape_derives_both_sides(self) -> None:
        ast = {
            "type": "comparison",
            "operator": "<=",
            "left": binop("add", var("VAR-01"), var("VAR-02")),
            "right": lit(10, "integer"),
        }
        p = derive_problem_forms(spec_of(doc(constraints=[constraint(ast=ast, bound=None)])))
        c = p.constraints[0]
        assert c.shape == "COMPARISON_ROOT" and c.comparison_operator == "<="
        assert c.left is not None and c.right is not None and c.expression is None
        assert dict(c.left.coefficients) == {"VAR-01": Fraction(1), "VAR-02": Fraction(1)}
        assert c.right.constant == 10 and c.bound_value is None

    def test_no_row_semantics_are_assigned(self) -> None:
        p = derive_problem_forms(spec_of(doc()))
        c = p.constraints[0]
        for attr in ("row_lower", "row_upper", "rows", "coefficients", "lower", "upper"):
            assert not hasattr(c, attr)

    def test_float_literal_is_exact_binary64(self) -> None:
        d = doc()
        d["objectives"][0]["expressionAst"] = binop("mul", lit(0.1), var("VAR-01"))
        form = derive_problem_forms(spec_of(d)).objective.form
        assert form.coefficients["VAR-01"] == Fraction(0.1) != Fraction(1, 10)

    def test_big_integer_coefficient_exact(self) -> None:
        big = 10**40 + 1
        d = doc()
        d["objectives"][0]["expressionAst"] = binop("mul", lit(big, "integer"), var("VAR-01"))
        form = derive_problem_forms(spec_of(d)).objective.form
        assert form.coefficients["VAR-01"] == big

    def test_derived_forms_are_immutable(self) -> None:
        form = derive_problem_forms(spec_of(doc())).objective.form
        with pytest.raises(TypeError):
            form.coefficients["VAR-01"] = Fraction(9)  # type: ignore[index]


class TestExpressionRejections:
    def test_undeclared_variable(self) -> None:
        c = constraint(ast=var("VAR-99"))
        with pytest.raises(ContinuousVariableOnlyError) as exc:
            derive_problem_forms(spec_of(doc(constraints=[c])))
        assert exc.value.path.startswith("$.constraints[0].expressionAst")

    def test_variable_declared_non_continuous_is_rejected_before_expressions(self) -> None:
        d = doc(decisionVariables=[variable("VAR-01", domain="integer"), variable("VAR-02")])
        assert code_of(d) == VARIABLE_DOMAIN_UNSUPPORTED

    def test_parameter_reference(self) -> None:
        c = constraint(ast={"type": "parameter_ref", "parameterId": "P-1", "defaultValue": 1.0})
        with pytest.raises(UnsupportedParameterRefError):
            derive_problem_forms(spec_of(doc(constraints=[c])))

    def test_nonlinear_product(self) -> None:
        c = constraint(ast=binop("mul", var("VAR-01"), var("VAR-02")))
        with pytest.raises(NonlinearMultiplicationError):
            derive_problem_forms(spec_of(doc(constraints=[c])))

    def test_boolean_and_string_literals(self) -> None:
        for lit_node in (lit(True, "boolean"), lit("5", "string"), lit(True, "real")):
            c = constraint(ast=binop("add", var("VAR-01"), lit_node))
            with pytest.raises(UnsupportedLiteralError):
                derive_problem_forms(spec_of(doc(constraints=[c])))

    def test_unary_nonlinear_and_unsupported_nodes(self) -> None:
        nodes = [
            {"type": "unary_op", "operator": "sqrt", "argument": var()},
            {"type": "lookup_table", "curveId": "C", "input": var()},
            {"type": "uncompiled", "expressionSummary": "x + y"},
        ]
        for node in nodes:
            with pytest.raises(AffineEvaluationError):
                derive_problem_forms(spec_of(doc(constraints=[constraint(ast=node)])))

    def test_empty_sum(self) -> None:
        c = constraint(ast={"type": "nary_op", "operator": "sum", "arguments": []})
        with pytest.raises(AffineEvaluationError, match="Empty sum"):
            derive_problem_forms(spec_of(doc(constraints=[c])))

    def test_nested_comparison_inside_expression(self) -> None:
        inner = {"type": "comparison", "operator": "<=", "left": var(), "right": lit(1)}
        c = constraint(ast=binop("add", var(), inner))
        with pytest.raises(AffineEvaluationError):
            derive_problem_forms(spec_of(doc(constraints=[c])))

    def test_time_indexed_reference(self) -> None:
        node = {"type": "variable_ref", "variableId": "VAR-01", "timeIndex": 0}
        with pytest.raises(AffineEvaluationError, match="Time-indexed"):
            derive_problem_forms(spec_of(doc(constraints=[constraint(ast=node)])))


class TestValidationEntryPoint:
    def test_validate_returns_exact_bounds_and_parts(self) -> None:
        variables, objective, constraints = validate_lp_problem(spec_of(doc()))
        assert [v.variable_id for v in variables] == ["VAR-01", "VAR-02"]
        assert objective.id == "OBJ-01" and len(constraints) == 1

    def test_python_constructed_spec_gets_strict_validation_but_not_raw_guarantees(self) -> None:
        # A spec built straight from a dict skips the raw scanner: typos are silently dropped by M1.
        d = doc()
        d["decisionVariables"][0]["upperBoud"] = 1  # typo ignored by M1
        spec = GeneralizedProblemSpec.model_validate(d)
        p = derive_problem_forms(spec)
        assert p.variables[0].upper == 10  # the typo had no effect; ingress would have rejected it

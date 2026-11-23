"""Tests for independent recursive affine expression evaluator directly over ExprNode AST (S2)."""

import random
from fractions import Fraction

import pytest

from qorvexa.contracts.solver_fabric import (
    BinaryOpNode,
    ComparisonNode,
    ExprNode,
    LiteralNode,
    LookupTableNode,
    NaryOpNode,
    ParameterRefNode,
    StatisticalDistNode,
    UnaryOpNode,
    UncompiledNode,
    VariableRefNode,
)
from qorvexa.solver_fabric.affine import (
    AffineForm,
    AffineLimits,
    ContinuousVariableOnlyError,
    CyclicExpressionError,
    DivisionByZeroError,
    ExpressionDepthExceededError,
    NonlinearMultiplicationError,
    NonlinearOperatorError,
    ResourceLimitExceededError,
    UnsupportedLiteralError,
    UnsupportedNodeError,
    UnsupportedOperatorError,
    UnsupportedParameterRefError,
    VariableDivisorError,
    evaluate_affine_expression,
)


def _lit(val: float | int | bool | str, data_type: str = "real") -> LiteralNode:
    return LiteralNode(value=val, dataType=data_type)


def _frac_lit(c: Fraction) -> ExprNode:
    if c.denominator == 1:
        return _lit(int(c))
    return BinaryOpNode(operator="div", left=_lit(c.numerator), right=_lit(c.denominator))


def _var(var_id: str, time_index: int | None = None) -> VariableRefNode:
    return VariableRefNode(variableId=var_id, timeIndex=time_index)


def _add(left: ExprNode, right: ExprNode) -> BinaryOpNode:
    return BinaryOpNode(operator="add", left=left, right=right)


def _sub(left: ExprNode, right: ExprNode) -> BinaryOpNode:
    return BinaryOpNode(operator="sub", left=left, right=right)


def _mul(left: ExprNode, right: ExprNode) -> BinaryOpNode:
    return BinaryOpNode(operator="mul", left=left, right=right)


def _div(left: ExprNode, right: ExprNode) -> BinaryOpNode:
    return BinaryOpNode(operator="div", left=left, right=right)


def _neg(arg: ExprNode) -> UnaryOpNode:
    return UnaryOpNode(operator="neg", argument=arg)


def _sum(*args: ExprNode) -> NaryOpNode:
    return NaryOpNode(operator="sum", arguments=list(args))


class TestAffineEvaluationFixtures:
    """Implements all specified E1-E11 fixtures and fundamental affine rules."""

    def test_e1_simple_linear_expression(self) -> None:
        """E1: 2*x + 3*y - 5."""
        # 2*x + 3*y - 5 = ((2 * x) + (3 * y)) - 5
        tree = _sub(
            _add(_mul(_lit(2), _var("x")), _mul(_lit(3), _var("y"))),
            _lit(5),
        )
        form = evaluate_affine_expression(tree)
        assert form.constant == Fraction(-5)
        assert form.coefficients == {"x": Fraction(2), "y": Fraction(3)}
        assert form.structural_variable_refs == frozenset({"x", "y"})
        assert form.is_constant is False
        assert form.is_structurally_constant is False

    def test_e2_rational_coefficients(self) -> None:
        """E2: (1/3)*x - (5/7)*y + 2/5."""
        # ((1/3 * x) - (5/7 * y)) + 2/5
        tree = _add(
            _sub(
                _mul(_div(_lit(1), _lit(3)), _var("x")),
                _mul(_div(_lit(5), _lit(7)), _var("y")),
            ),
            _div(_lit(2), _lit(5)),
        )
        form = evaluate_affine_expression(tree)
        assert form.constant == Fraction(2, 5)
        assert form.coefficients == {"x": Fraction(1, 3), "y": Fraction(-5, 7)}
        assert form.structural_variable_refs == frozenset({"x", "y"})

    def test_e3_exact_cancellation(self) -> None:
        """E3: x - x -> constant 0, empty coefficients, retains structural variable ref."""
        tree = _sub(_var("x"), _var("x"))
        form = evaluate_affine_expression(tree)
        assert form.constant == Fraction(0)
        assert form.coefficients == {}  # Zero coefficient filtered out
        assert form.is_constant is True
        # Crucial: structural tracking persists
        assert form.structural_variable_refs == frozenset({"x"})
        assert form.is_structurally_constant is False

    def test_e4_structural_rejection_of_cancelled_variable_multiplication(self) -> None:
        """E4: (x - x) * y -> must be rejected as non-linear variable * variable!"""
        cancelled_x = _sub(_var("x"), _var("x"))
        tree = _mul(cancelled_x, _var("y"))

        with pytest.raises(
            NonlinearMultiplicationError,
            match="Cannot multiply two subexpressions that structurally contain variables",
        ) as exc_info:
            evaluate_affine_expression(tree)
        assert exc_info.value.path == "$"

    def test_e5_multiplication_with_constants_on_both_sides(self) -> None:
        """E5: 3 * (2*x + 1) and (2*x + 1) * 3."""
        subexpr = _add(_mul(_lit(2), _var("x")), _lit(1))

        # Left constant: 3 * (2*x + 1) = 6*x + 3
        tree_left = _mul(_lit(3), subexpr)
        form_left = evaluate_affine_expression(tree_left)
        assert form_left.constant == Fraction(3)
        assert form_left.coefficients == {"x": Fraction(6)}
        assert form_left.structural_variable_refs == frozenset({"x"})

        # Right constant: (2*x + 1) * 3 = 6*x + 3
        tree_right = _mul(subexpr, _lit(3))
        form_right = evaluate_affine_expression(tree_right)
        assert form_right.constant == Fraction(3)
        assert form_right.coefficients == {"x": Fraction(6)}
        assert form_right.structural_variable_refs == frozenset({"x"})

    def test_e6_constant_division(self) -> None:
        """E6: (4*x + 6) / 2 = 2*x + 3."""
        subexpr = _add(_mul(_lit(4), _var("x")), _lit(6))
        tree = _div(subexpr, _lit(2))
        form = evaluate_affine_expression(tree)
        assert form.constant == Fraction(3)
        assert form.coefficients == {"x": Fraction(2)}

    def test_e7_division_by_zero_constant(self) -> None:
        """E7: (4*x + 6) / 0 -> DivisionByZeroError."""
        subexpr = _add(_mul(_lit(4), _var("x")), _lit(6))
        tree = _div(subexpr, _lit(0))
        with pytest.raises(DivisionByZeroError, match="Division by zero constant"):
            evaluate_affine_expression(tree)

    def test_e8_variable_divisor(self) -> None:
        """E8: 10 / x -> VariableDivisorError."""
        tree = _div(_lit(10), _var("x"))
        with pytest.raises(
            VariableDivisorError,
            match="Cannot divide by a subexpression that structurally contains variables",
        ):
            evaluate_affine_expression(tree)

    def test_e9_variable_multiplied_by_variable(self) -> None:
        """E9: x * y -> NonlinearMultiplicationError."""
        tree = _mul(_var("x"), _var("y"))
        with pytest.raises(
            NonlinearMultiplicationError, match="Cannot multiply two subexpressions"
        ):
            evaluate_affine_expression(tree)

    def test_e10_nary_sum(self) -> None:
        """E10: sum(x, 2*y, -x) = 2*y."""
        tree = _sum(_var("x"), _mul(_lit(2), _var("y")), _neg(_var("x")))
        form = evaluate_affine_expression(tree)
        assert form.constant == Fraction(0)
        assert form.coefficients == {"y": Fraction(2)}
        assert form.structural_variable_refs == frozenset({"x", "y"})

    def test_e11_parameter_ref_rejected_for_slice_1(self) -> None:
        """E11: ParameterRefNode -> UnsupportedParameterRefError with typed path."""
        param_node = ParameterRefNode(parameterId="DEMAND_THRESHOLD", defaultValue=100.0)
        tree = _add(_var("x"), param_node)
        with pytest.raises(
            UnsupportedParameterRefError,
            match="Parameter reference 'DEMAND_THRESHOLD' is not supported",
        ) as exc_info:
            evaluate_affine_expression(tree)
        assert exc_info.value.path == "$.right"


class TestAffineEvaluationEdgeCases:
    """Verifies edge cases, operator constraints, depth guards, and type validation."""

    def test_duplicate_variable_accumulation(self) -> None:
        """x + x + x = 3*x."""
        tree = _add(_add(_var("x"), _var("x")), _var("x"))
        form = evaluate_affine_expression(tree)
        assert form.coefficients == {"x": Fraction(3)}
        assert form.constant == Fraction(0)

    def test_time_indexed_variable_refs(self) -> None:
        tree = _add(_var("flow", time_index=0), _var("flow", time_index=1))
        with pytest.raises(UnsupportedNodeError, match="Time-indexed variable reference"):
            evaluate_affine_expression(tree)

    def test_disallowed_boolean_literals(self) -> None:
        tree = _lit(True, data_type="boolean")
        with pytest.raises(
            UnsupportedLiteralError, match="Literal data_type 'boolean' is not supported"
        ):
            evaluate_affine_expression(tree)

        tree2 = _lit(True, data_type="real")
        with pytest.raises(UnsupportedLiteralError, match="type 'bool' is not a numeric"):
            evaluate_affine_expression(tree2)

    def test_disallowed_nan_and_inf_literals(self) -> None:
        with pytest.raises(UnsupportedLiteralError, match="Invalid numeric literal"):
            evaluate_affine_expression(_lit(float("nan")))
        with pytest.raises(UnsupportedLiteralError, match="Invalid numeric literal"):
            evaluate_affine_expression(_lit(float("inf")))

    def test_disallowed_ast_nodes(self) -> None:
        with pytest.raises(UnsupportedNodeError, match=r"LookupTableNode|lookup_table"):
            evaluate_affine_expression(LookupTableNode(curveId="PUMP_CURVE", input=_var("x")))
        with pytest.raises(
            UnsupportedNodeError, match=r"StatisticalDistNode|statistical_distribution"
        ):
            evaluate_affine_expression(
                StatisticalDistNode(distribution="normal", parameters={"mean": 0.0, "std": 1.0})
            )
        with pytest.raises(UnsupportedNodeError, match=r"UncompiledNode|uncompiled"):
            evaluate_affine_expression(UncompiledNode(expressionSummary="2*x + y <= 10"))
        with pytest.raises(UnsupportedNodeError, match=r"ComparisonNode|comparison"):
            evaluate_affine_expression(
                ComparisonNode(operator="<=", left=_var("x"), right=_lit(10))
            )

    def test_disallowed_nonlinear_operators(self) -> None:
        with pytest.raises(NonlinearOperatorError, match="Unary operator 'sqrt' is non-linear"):
            evaluate_affine_expression(UnaryOpNode(operator="sqrt", argument=_var("x")))
        with pytest.raises(NonlinearOperatorError, match="Unary operator 'abs' is non-linear"):
            evaluate_affine_expression(UnaryOpNode(operator="abs", argument=_var("x")))
        with pytest.raises(
            UnsupportedOperatorError, match="Binary operator 'pow' is not supported"
        ):
            evaluate_affine_expression(BinaryOpNode(operator="pow", left=_var("x"), right=_lit(2)))
        with pytest.raises(
            UnsupportedOperatorError, match="Binary operator 'min' is not supported"
        ):
            evaluate_affine_expression(BinaryOpNode(operator="min", left=_var("x"), right=_lit(0)))

    def test_continuous_variable_domain_enforcement(self) -> None:
        domains = {"x": "continuous", "cnt": "integer", "bin_flag": "binary"}

        # Continuous passes
        form = evaluate_affine_expression(_var("x"), variable_domains=domains)
        assert form.coefficients == {"x": Fraction(1)}

        # Integer raises ContinuousVariableOnlyError
        with pytest.raises(ContinuousVariableOnlyError, match="domain 'integer'"):
            evaluate_affine_expression(_var("cnt"), variable_domains=domains)

        # Binary raises ContinuousVariableOnlyError
        with pytest.raises(ContinuousVariableOnlyError, match="domain 'binary'"):
            evaluate_affine_expression(_var("bin_flag"), variable_domains=domains)

    def test_expression_depth_exceeded(self) -> None:
        # Build nested tree: x + (x + (x + ...))
        curr: ExprNode = _var("x")
        for _ in range(25):
            curr = _add(_var("x"), curr)

        # With max_depth=10, must raise ExpressionDepthExceededError
        with pytest.raises(ExpressionDepthExceededError, match="exceeds maximum permitted depth"):
            evaluate_affine_expression(curr, max_depth=10)

        # With sufficient max_depth, succeeds
        form = evaluate_affine_expression(curr, max_depth=100)
        assert form.coefficients == {"x": Fraction(26)}

    def test_deterministic_seeded_expression_evaluations(self) -> None:
        """Evaluates 50 deterministic randomly generated valid affine expressions."""
        rng = random.Random(1337)  # noqa: S311
        var_names = ["x1", "x2", "x3", "x4"]

        def generate_random_affine(d: int) -> tuple[ExprNode, dict[str, Fraction], Fraction]:
            if d <= 1 or rng.random() < 0.3:
                # Leaf: literal or variable
                if rng.random() < 0.5:
                    v = rng.choice(var_names)
                    return _var(v), {v: Fraction(1)}, Fraction(0)
                else:
                    c = Fraction(rng.randint(-10, 10), rng.randint(1, 5))
                    if c.denominator == 1:
                        return _lit(int(c)), {}, c
                    return _div(_lit(c.numerator), _lit(c.denominator)), {}, c

            op = rng.choice(["add", "sub", "mul_const"])
            if op in ("add", "sub"):
                left_node, left_c, left_const = generate_random_affine(d - 1)
                right_node, right_c, right_const = generate_random_affine(d - 1)
                all_vars = set(left_c.keys()) | set(right_c.keys())

                if op == "add":
                    node = _add(left_node, right_node)
                    tot_c = {
                        v: left_c.get(v, Fraction(0)) + right_c.get(v, Fraction(0))
                        for v in all_vars
                    }
                    tot_const = left_const + right_const
                else:
                    node = _sub(left_node, right_node)
                    tot_c = {
                        v: left_c.get(v, Fraction(0)) - right_c.get(v, Fraction(0))
                        for v in all_vars
                    }
                    tot_const = left_const - right_const

                cleaned_c = {k: v for k, v in tot_c.items() if v != Fraction(0)}
                return node, cleaned_c, tot_const
            else:
                # Multiply by constant
                scale = Fraction(rng.randint(-5, 5), rng.randint(1, 3))
                inner_node, inner_c, inner_const = generate_random_affine(d - 1)
                if rng.random() < 0.5:
                    node = _mul(_frac_lit(scale), inner_node)
                else:
                    node = _mul(inner_node, _frac_lit(scale))
                tot_c = {k: v * scale for k, v in inner_c.items() if v * scale != Fraction(0)}
                tot_const = inner_const * scale
                return node, tot_c, tot_const

        for _ in range(50):
            expr, expected_coeffs, expected_const = generate_random_affine(4)
            form = evaluate_affine_expression(expr)
            assert form.constant == expected_const
            assert form.coefficients == expected_coeffs


class TestMandatoryCorrections:
    """Owner-mandated corrections: literal strictness, ids, domains, empty sums, depth."""

    def test_string_literal_rejected(self) -> None:
        for text in ("0.1", "1/3", "NaN", "inf", "abc", "1e999", ""):
            with pytest.raises(UnsupportedLiteralError):
                evaluate_affine_expression(_lit(text))
        with pytest.raises(UnsupportedLiteralError):
            evaluate_affine_expression(_lit("1", data_type="string"))

    def test_bool_literal_rejected_for_numeric_types(self) -> None:
        for dt in ("real", "integer", "boolean"):
            with pytest.raises(UnsupportedLiteralError):
                evaluate_affine_expression(_lit(True, data_type=dt))
            with pytest.raises(UnsupportedLiteralError):
                evaluate_affine_expression(_lit(False, data_type=dt))

    def test_integer_literal_must_be_integral(self) -> None:
        with pytest.raises(UnsupportedLiteralError, match="not integral"):
            evaluate_affine_expression(_lit(2.5, data_type="integer"))
        assert evaluate_affine_expression(_lit(2.0, data_type="integer")).constant == 2

    def test_exact_big_integers_preserved(self) -> None:
        big = 10**40 + 1
        form = evaluate_affine_expression(_mul(_lit(big, data_type="integer"), _var("x")))
        assert form.coefficients == {"x": Fraction(big)}
        assert form.constant == 0

    def test_float_literal_exact_binary64_value(self) -> None:
        assert evaluate_affine_expression(_lit(0.1)).constant == Fraction(0.1)

    def test_nonfinite_float_literals_rejected(self) -> None:
        for v in (float("nan"), float("inf"), float("-inf")):
            with pytest.raises(UnsupportedLiteralError):
                evaluate_affine_expression(_lit(v))

    def test_uppercase_hyphenated_ids_supported(self) -> None:
        domains = {"VAR-01": "continuous", "CON-01": "continuous"}
        form = evaluate_affine_expression(
            _add(_var("VAR-01"), _mul(_lit(2), _var("CON-01"))), variable_domains=domains
        )
        assert form.coefficients == {"VAR-01": Fraction(1), "CON-01": Fraction(2)}

    def test_unknown_variable_rejected_when_table_supplied(self) -> None:
        with pytest.raises(ContinuousVariableOnlyError, match="not declared"):
            evaluate_affine_expression(_var("ghost"), variable_domains={"x": "continuous"})

    @pytest.mark.parametrize(
        "domain",
        [
            "integer",
            "binary",
            "semi_continuous",
            "semi_integer",
            "categorical",
            "discrete_set",
            "unknown",
            "weird",
        ],
    )
    def test_only_continuous_domain_accepted(self, domain: str) -> None:
        with pytest.raises(ContinuousVariableOnlyError):
            evaluate_affine_expression(_var("x"), variable_domains={"x": domain})

    def test_empty_sum_rejected(self) -> None:
        with pytest.raises(UnsupportedOperatorError, match="Empty sum"):
            evaluate_affine_expression(NaryOpNode(operator="sum", arguments=[]))

    def test_parameter_ref_rejected(self) -> None:
        with pytest.raises(UnsupportedParameterRefError):
            evaluate_affine_expression(ParameterRefNode(parameterId="P-1", defaultValue=1.0))

    def test_comparison_inside_expression_rejected(self) -> None:
        tree = _add(_var("x"), ComparisonNode(operator="<=", left=_var("x"), right=_lit(1)))
        with pytest.raises(UnsupportedNodeError):
            evaluate_affine_expression(tree)

    def test_deep_chain_raises_typed_error_not_recursion_error(self) -> None:
        node: ExprNode = _var("x")
        for _ in range(2000):
            node = _add(_var("x"), node)
        with pytest.raises(ExpressionDepthExceededError):
            evaluate_affine_expression(node)
        with pytest.raises(ExpressionDepthExceededError):
            evaluate_affine_expression(node, max_depth=10**6)


DOM = {"x": "continuous", "y": "continuous"}


class TestStructuralGrammarRegression:
    """Required explicit rejections (owner mandate)."""

    def test_x_minus_x_times_y(self) -> None:
        tree = _mul(_sub(_var("x"), _var("x")), _var("y"))
        with pytest.raises(NonlinearMultiplicationError):
            evaluate_affine_expression(tree, variable_domains=DOM)

    def test_x_over_y_minus_y_plus_2(self) -> None:
        divisor = _add(_sub(_var("y"), _var("y")), _lit(2))
        with pytest.raises(VariableDivisorError):
            evaluate_affine_expression(_div(_var("x"), divisor), variable_domains=DOM)

    def test_zero_times_x_times_y(self) -> None:
        tree = _mul(_lit(0), _mul(_var("x"), _var("y")))
        with pytest.raises(NonlinearMultiplicationError) as exc:
            evaluate_affine_expression(tree, variable_domains=DOM)
        assert exc.value.path == "$.right"

    def test_zero_times_one_over_zero(self) -> None:
        tree = _mul(_lit(0), _div(_lit(1), _lit(0)))
        with pytest.raises(DivisionByZeroError):
            evaluate_affine_expression(tree, variable_domains=DOM)

    def test_numerator_validated_before_zero_divisor(self) -> None:
        tree = _div(_mul(_var("x"), _var("y")), _lit(0))
        with pytest.raises(NonlinearMultiplicationError):
            evaluate_affine_expression(tree, variable_domains=DOM)

    def test_multiplication_by_zero_keeps_structural_reference(self) -> None:
        form = evaluate_affine_expression(_mul(_var("x"), _lit(0)), variable_domains=DOM)
        assert form.constant == 0 and not form.coefficients
        assert form.structural_variable_refs == frozenset({"x"})
        with pytest.raises(NonlinearMultiplicationError):
            evaluate_affine_expression(_mul(form_node := _mul(_var("x"), _lit(0)), _var("y")))
        assert form_node is not None

    def test_constant_times_constant_and_division(self) -> None:
        form = evaluate_affine_expression(_div(_mul(_lit(3), _lit(5)), _lit(4)))
        assert form.constant == Fraction(15, 4)


class TestResourceLimits:
    def test_node_limit(self) -> None:
        tree = _add(_add(_var("x"), _var("y")), _add(_var("x"), _var("y")))
        with pytest.raises(ResourceLimitExceededError) as exc:
            evaluate_affine_expression(tree, limits=AffineLimits(max_nodes=3))
        assert exc.value.limit_name == "max_nodes"

    def test_edge_limit(self) -> None:
        tree = _add(_var("x"), _var("y"))
        with pytest.raises(ResourceLimitExceededError) as exc:
            evaluate_affine_expression(tree, limits=AffineLimits(max_edges=1))
        assert exc.value.limit_name == "max_edges"

    def test_depth_limit_is_typed(self) -> None:
        node: ExprNode = _var("x")
        for _ in range(30):
            node = _add(_var("x"), node)
        with pytest.raises(ExpressionDepthExceededError):
            evaluate_affine_expression(node, limits=AffineLimits(max_depth=10))

    def test_sum_arity_limit(self) -> None:
        tree = NaryOpNode(operator="sum", arguments=[_var("x")] * 4)
        with pytest.raises(ResourceLimitExceededError) as exc:
            evaluate_affine_expression(tree, limits=AffineLimits(max_sum_arity=3))
        assert exc.value.limit_name == "max_sum_arity"
        ok = evaluate_affine_expression(tree, limits=AffineLimits(max_sum_arity=4))
        assert ok.coefficients == {"x": Fraction(4)}

    def test_rational_bit_limit_on_literal(self) -> None:
        with pytest.raises(ResourceLimitExceededError) as exc:
            evaluate_affine_expression(_lit(10**1300, data_type="integer"))
        assert exc.value.limit_name == "max_rational_bits"
        assert evaluate_affine_expression(_lit(10**1200, data_type="integer")).constant == 10**1200

    def test_rational_bit_limit_on_intermediate_product(self) -> None:
        big = _lit(2**2500, data_type="integer")
        tree = _mul(_mul(big, big), _var("x"))
        with pytest.raises(ResourceLimitExceededError) as exc:
            evaluate_affine_expression(tree)
        assert exc.value.limit_name == "max_rational_bits"

    def test_rational_bit_limit_on_denominator(self) -> None:
        d = _lit(2**2500, data_type="integer")
        tree = _div(_div(_lit(1), d), d)
        with pytest.raises(ResourceLimitExceededError):
            evaluate_affine_expression(tree)

    def test_custom_bit_limit(self) -> None:
        with pytest.raises(ResourceLimitExceededError):
            evaluate_affine_expression(
                _lit(2**70, data_type="integer"), limits=AffineLimits(max_rational_bits=64)
            )

    def test_work_limit(self) -> None:
        tree = NaryOpNode(operator="sum", arguments=[_var(f"v{i}") for i in range(50)])
        with pytest.raises(ResourceLimitExceededError) as exc:
            evaluate_affine_expression(tree, limits=AffineLimits(max_work=40))
        assert exc.value.limit_name == "max_work"

    def test_limits_are_deterministic(self) -> None:
        tree = NaryOpNode(operator="sum", arguments=[_var(f"v{i}") for i in range(50)])
        msgs = []
        for _ in range(2):
            with pytest.raises(ResourceLimitExceededError) as exc:
                evaluate_affine_expression(tree, limits=AffineLimits(max_work=40))
            msgs.append(str(exc.value))
        assert msgs[0] == msgs[1]

    def test_shared_dag_is_memoized_not_exponential(self) -> None:
        node: ExprNode = _var("x")
        for _ in range(200):
            node = _add(node, node)  # 2**200 tree visits if re-walked
        form = evaluate_affine_expression(node, limits=AffineLimits(max_depth=300))
        assert form.coefficients == {"x": Fraction(2) ** 200}

    def test_shared_dag_counts_distinct_nodes_only(self) -> None:
        leaf = _var("x")
        node: ExprNode = leaf
        for _ in range(20):
            node = _add(node, node)
        evaluate_affine_expression(node, limits=AffineLimits(max_nodes=25))
        with pytest.raises(ResourceLimitExceededError):
            evaluate_affine_expression(node, limits=AffineLimits(max_nodes=20))

    def test_cycle_rejected(self) -> None:
        a = _add(_var("x"), _var("y"))
        a.right = a
        with pytest.raises(CyclicExpressionError):
            evaluate_affine_expression(a)

    def test_indirect_cycle_rejected(self) -> None:
        a = _add(_var("x"), _var("y"))
        b = _add(a, _var("x"))
        a.left = b
        with pytest.raises(CyclicExpressionError):
            evaluate_affine_expression(b)

    def test_wide_sum_within_default_limits(self) -> None:
        tree = NaryOpNode(operator="sum", arguments=[_var(f"v{i}") for i in range(5000)])
        assert len(evaluate_affine_expression(tree).coefficients) == 5000


class TestAffineFormImmutability:
    def test_coefficients_read_only(self) -> None:
        form = evaluate_affine_expression(_add(_var("x"), _lit(1)))
        with pytest.raises(TypeError):
            form.coefficients["z"] = Fraction(1)  # type: ignore[index]
        with pytest.raises(TypeError):
            del form.coefficients["x"]  # type: ignore[attr-defined]

    def test_attributes_frozen(self) -> None:
        form = AffineForm(constant=Fraction(1))
        with pytest.raises(AttributeError):
            form.constant = Fraction(2)  # type: ignore[misc]
        with pytest.raises(AttributeError):
            form.coefficients = {}  # type: ignore[misc]

    def test_source_dict_mutation_does_not_leak(self) -> None:
        src = {"x": Fraction(1)}
        form = AffineForm(constant=Fraction(0), coefficients=src)
        src["y"] = Fraction(5)
        src["x"] = Fraction(9)
        assert dict(form.coefficients) == {"x": Fraction(1)}

    def test_zero_coefficients_stripped_and_hashable(self) -> None:
        a = AffineForm(Fraction(0), {"x": Fraction(0), "y": Fraction(2)}, frozenset({"x", "y"}))
        b = AffineForm(Fraction(0), {"y": Fraction(2)}, frozenset({"x", "y"}))
        assert a == b and hash(a) == hash(b)
        assert isinstance(a.structural_variable_refs, frozenset)

    def test_non_fraction_values_rejected(self) -> None:
        with pytest.raises(TypeError):
            AffineForm(Fraction(0), {"x": "1/3"})  # type: ignore[dict-item]
        with pytest.raises(TypeError):
            AffineForm(True)  # type: ignore[arg-type]


class TestVariableIdValidation:
    def test_blank_and_overlong_ids_rejected(self) -> None:
        for bad in ("", "   ", "v" * 257):
            with pytest.raises(UnsupportedNodeError):
                evaluate_affine_expression(_var(bad))
        assert evaluate_affine_expression(_var("v" * 256)).coefficients

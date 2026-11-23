"""Tests for exact rational arithmetic, rounding error, and interval support functions (S2)."""

import math
from fractions import Fraction

import pytest

from qorvexa.solver_fabric.exact import (
    BOUND_CONTRADICTION_EXACT,
    Binary64Rounded,
    BoundContradictionError,
    CoefficientOverflowError,
    CoefficientUnderflowError,
    ExactResourceError,
    Interval,
    IntervalEndpoint,
    check_rational_size,
    evaluate_infimum_support,
    evaluate_support_function,
    is_subnormal_binary64,
    lower_to_binary64,
    rational_bit_length,
    to_binary64,
    to_fraction,
)


class TestToFraction:
    """Tests exact conversion to Fraction with strict type validation."""

    def test_integer_inputs(self) -> None:
        assert to_fraction(0) == Fraction(0, 1)
        assert to_fraction(42) == Fraction(42, 1)
        assert to_fraction(-100) == Fraction(-100, 1)

    def test_fraction_inputs(self) -> None:
        f = Fraction(3, 7)
        assert to_fraction(f) is f

    def test_finite_float_inputs(self) -> None:
        assert to_fraction(0.5) == Fraction(1, 2)
        assert to_fraction(-0.75) == Fraction(-3, 4)
        # 0.1 canonical IEEE-754 binary64 representation
        f_01 = to_fraction(0.1)
        assert f_01 == Fraction(0.1)
        assert f_01.numerator == 3602879701896397
        assert f_01.denominator == 36028797018963968

    def test_reject_booleans(self) -> None:
        with pytest.raises(TypeError, match="Boolean value 'True' is rejected"):
            to_fraction(True)
        with pytest.raises(TypeError, match="Boolean value 'False' is rejected"):
            to_fraction(False)

    def test_reject_nan(self) -> None:
        with pytest.raises(ValueError, match="NaN is rejected"):
            to_fraction(float("nan"))

    def test_reject_infinities(self) -> None:
        with pytest.raises(ValueError, match="Infinity"):
            to_fraction(float("inf"))
        with pytest.raises(ValueError, match="Infinity"):
            to_fraction(float("-inf"))

    def test_reject_invalid_types(self) -> None:
        with pytest.raises(TypeError, match="Unsupported numeric type"):
            to_fraction([1, 2])  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="Unsupported numeric type"):
            to_fraction({"a": 1})  # type: ignore[arg-type]

    def test_string_inputs_rejected(self) -> None:
        for text in ("1/3", "0.25", "true", "nan", "inf", ""):
            with pytest.raises(TypeError, match="String values are rejected"):
                to_fraction(text)  # type: ignore[arg-type]

    def test_bool_nan_inf_rejected(self) -> None:
        with pytest.raises(TypeError):
            to_fraction(True)
        with pytest.raises(ValueError):
            to_fraction(float("nan"))
        with pytest.raises(ValueError):
            to_fraction(float("-inf"))

    def test_big_int_exact(self) -> None:
        assert to_fraction(10**50 + 7) == Fraction(10**50 + 7)


class TestBinary64Rounding:
    """Tests correctly rounded Fraction -> binary64 conversion and exact error reporting."""

    def test_exact_binary64_fractions(self) -> None:
        res = to_binary64(Fraction(1, 2))
        assert isinstance(res, Binary64Rounded)
        assert res.value == 0.5
        assert res.rounding_error == Fraction(0)
        assert res.is_exact is True

        res_neg = to_binary64(Fraction(-3, 8))
        assert res_neg.value == -0.375
        assert res_neg.rounding_error == Fraction(0)
        assert res_neg.is_exact is True

    def test_inexact_fraction_rounding_error(self) -> None:
        # 1/3 has infinite repeating binary representation
        res = to_binary64(Fraction(1, 3))
        assert res.is_exact is False
        assert res.value == float(1 / 3)
        # Rounding error must be exactly Fraction(res.value) - Fraction(1, 3)
        expected_error = Fraction(res.value) - Fraction(1, 3)
        assert res.rounding_error == expected_error
        assert abs(float(res.rounding_error)) < 1e-15

    def test_canonical_01_02_03_rounding_verification(self) -> None:
        """Verifies exact Fraction sum of 0.1 + 0.2 + 0.3 vs IEEE-754 float representation."""
        # Exact mathematical sum: 1/10 + 2/10 + 3/10 = 6/10 = 3/5
        f1 = Fraction(1, 10)
        f2 = Fraction(2, 10)
        f3 = Fraction(3, 10)
        exact_sum = f1 + f2 + f3
        assert exact_sum == Fraction(3, 5)

        # Standard float arithmetic suffers binary64 rounding error:
        float_sum = 0.1 + 0.2 + 0.3
        assert float_sum == 0.6000000000000001
        assert float_sum != 0.6

        # Convert exact sum Fraction(3, 5) to binary64
        res = to_binary64(exact_sum)
        assert res.value == 0.6  # Standard round-to-nearest produces 0.6
        # In binary64, 0.6 is not exactly 3/5:
        assert Fraction(0.6) != Fraction(3, 5)
        assert res.is_exact is False
        assert res.rounding_error == Fraction(0.6) - Fraction(3, 5)

    def test_to_binary64_type_check(self) -> None:
        with pytest.raises(TypeError, match="Expected Fraction"):
            to_binary64(0.5)  # type: ignore[arg-type]


class TestIntervalEndpoints:
    """Tests extended real line endpoints and interval bounds."""

    def test_finite_endpoint(self) -> None:
        ep = IntervalEndpoint.finite(Fraction(5, 2))
        assert ep.is_finite is True
        assert ep.is_pos_inf is False
        assert ep.is_neg_inf is False
        assert ep.value == Fraction(5, 2)

    def test_infinite_endpoints(self) -> None:
        pos = IntervalEndpoint.plus_infinity()
        neg = IntervalEndpoint.minus_infinity()
        assert pos.is_pos_inf is True
        assert pos.is_finite is False
        assert pos.value is None

        assert neg.is_neg_inf is True
        assert neg.is_finite is False
        assert neg.value is None

    def test_endpoint_ordering(self) -> None:
        neg = IntervalEndpoint.minus_infinity()
        f_neg = IntervalEndpoint.finite(-10)
        f_zero = IntervalEndpoint.finite(0)
        f_pos = IntervalEndpoint.finite(10)
        pos = IntervalEndpoint.plus_infinity()

        assert neg < f_neg < f_zero < f_pos < pos
        assert neg <= neg
        assert pos >= pos
        assert not (pos < pos)
        assert not (neg > f_neg)

    def test_interval_validation(self) -> None:
        # Valid intervals
        i1 = Interval.from_bounds(0, 10)
        assert i1.lower.value == Fraction(0)
        assert i1.upper.value == Fraction(10)

        i_semi = Interval.from_bounds(0, None)
        assert i_semi.lower.value == Fraction(0)
        assert i_semi.upper.is_pos_inf is True

        i_unbounded = Interval.from_bounds(None, None)
        assert i_unbounded.lower.is_neg_inf is True
        assert i_unbounded.upper.is_pos_inf is True

        # Inverted bounds raise ValueError
        with pytest.raises(ValueError, match="exceeds upper"):
            Interval.from_bounds(10, 5)

        # Illegal infinite endpoints
        with pytest.raises(ValueError, match="Lower bound cannot be \\+Infinity"):
            Interval(lower=IntervalEndpoint.plus_infinity(), upper=IntervalEndpoint.finite(5))

        with pytest.raises(ValueError, match="Upper bound cannot be -Infinity"):
            Interval(lower=IntervalEndpoint.finite(5), upper=IntervalEndpoint.minus_infinity())


class TestSupportFunctionEvaluation:
    """Tests support function h(y) = sup_{x in [lower, upper]} y * x without 0 * Infinity."""

    def test_bounded_interval(self) -> None:
        # Interval [2, 5]
        interval = Interval.from_bounds(2, 5)

        # y = 3 > 0: sup = 3 * 5 = 15
        h_pos = evaluate_support_function(interval, 3)
        assert h_pos.is_finite is True
        assert h_pos.value == Fraction(15)

        # y = -2 < 0: sup = -2 * 2 = -4
        h_neg = evaluate_support_function(interval, -2)
        assert h_neg.is_finite is True
        assert h_neg.value == Fraction(-4)

        # y = 0: sup = 0
        h_zero = evaluate_support_function(interval, 0)
        assert h_zero.is_finite is True
        assert h_zero.value == Fraction(0)

    def test_semi_infinite_positive_interval(self) -> None:
        # Interval [0, +inf)
        interval = Interval.from_bounds(0, None)

        # y = 0: MUST be strictly 0, avoiding 0 * Infinity
        h_zero = evaluate_support_function(interval, 0)
        assert h_zero.is_finite is True
        assert h_zero.value == Fraction(0)

        # y = 1 > 0: sup_{x >= 0} 1 * x = +Infinity (never clamped to 0!)
        h_pos = evaluate_support_function(interval, 1)
        assert h_pos.is_infinite is True
        assert h_pos.value is None

        # y = -3 < 0: sup_{x >= 0} -3 * x = -3 * 0 = 0
        h_neg = evaluate_support_function(interval, -3)
        assert h_neg.is_finite is True
        assert h_neg.value == Fraction(0)

    def test_semi_infinite_negative_interval(self) -> None:
        # Interval (-inf, 10]
        interval = Interval.from_bounds(None, 10)

        # y = 0: sup = 0
        h_zero = evaluate_support_function(interval, 0)
        assert h_zero.is_finite is True
        assert h_zero.value == Fraction(0)

        # y = 2 > 0: sup = 2 * 10 = 20
        h_pos = evaluate_support_function(interval, 2)
        assert h_pos.is_finite is True
        assert h_pos.value == Fraction(20)

        # y = -1 < 0: as x -> -inf, -1 * x -> +Infinity
        h_neg = evaluate_support_function(interval, -1)
        assert h_neg.is_infinite is True
        assert h_neg.value is None

    def test_fully_unbounded_interval(self) -> None:
        # Interval (-inf, +inf)
        interval = Interval.from_bounds(None, None)

        # y = 0: sup = 0
        assert evaluate_support_function(interval, 0).value == Fraction(0)

        # Any non-zero multiplier produces +Infinity
        assert evaluate_support_function(interval, 5).is_infinite is True
        assert evaluate_support_function(interval, -5).is_infinite is True

    def test_multiplier_rejection_checks(self) -> None:
        interval = Interval.from_bounds(0, 1)
        with pytest.raises(TypeError, match="Boolean value"):
            evaluate_support_function(interval, True)
        with pytest.raises(ValueError, match="NaN"):
            evaluate_support_function(interval, float("nan"))
        with pytest.raises(ValueError, match="Infinity"):
            evaluate_support_function(interval, float("inf"))

    def test_unclamped_multiplier_integrity(self) -> None:
        """Verifies wrong-sign multipliers on unbounded intervals are never clamped to 0."""
        # Non-negative ray [0, +inf) with positive multiplier
        interval = Interval.from_bounds(0, None)
        res = evaluate_support_function(interval, Fraction(1, 1000))
        assert res.is_infinite is True
        assert res.value is not Fraction(0)


class TestCheckedLowering:
    def test_exact_and_rounded(self) -> None:
        r = lower_to_binary64(Fraction(1, 2))
        assert r.value == 0.5 and r.is_exact
        r = lower_to_binary64(Fraction(1, 3))
        assert not r.is_exact and r.rounding_error != 0

    def test_zero_ok(self) -> None:
        assert lower_to_binary64(Fraction(0)).value == 0.0

    def test_overflow_rejected(self) -> None:
        with pytest.raises(CoefficientOverflowError):
            lower_to_binary64(Fraction(10**400))
        with pytest.raises(CoefficientOverflowError):
            lower_to_binary64(-Fraction(10**400))

    def test_nonzero_underflow_rejected(self) -> None:
        with pytest.raises(CoefficientUnderflowError):
            lower_to_binary64(Fraction(1, 10**400))
        with pytest.raises(CoefficientUnderflowError):
            lower_to_binary64(-Fraction(1, 10**400))

    def test_exact_sum_0_1_0_2_0_3(self) -> None:
        total = Fraction(0.1) + Fraction(0.2) + Fraction(0.3)
        assert lower_to_binary64(total).value == float(total)


class TestInfimumSupport:
    inf = float("inf")

    def _iv(self, lo: int | float | Fraction | None, hi: int | float | Fraction | None) -> Interval:
        return Interval.from_bounds(lo, hi)

    def test_table(self) -> None:
        cases = [
            ((1, 3), 2, Fraction(2)),
            ((1, 3), -2, Fraction(-6)),
            ((1, 3), 0, Fraction(0)),
            ((None, 3), 2, None),
            ((None, 3), -2, Fraction(-6)),
            ((None, 3), 0, Fraction(0)),
            ((1, None), 2, Fraction(2)),
            ((1, None), -2, None),
            ((None, None), 1, None),
            ((None, None), 0, Fraction(0)),
        ]
        for (lo, hi), a, expected in cases:
            r = evaluate_infimum_support(self._iv(lo, hi), a)
            if expected is None:
                assert not r.is_finite
            else:
                assert r.is_finite and r.value == expected

    def test_identity_with_sup_form(self) -> None:
        for lo, hi in [(1, 3), (None, 3), (1, None), (None, None), (-2, 2)]:
            for a in (-2, -1, 0, 1, 2):
                h_inf = evaluate_infimum_support(self._iv(lo, hi), a)
                h_sup = evaluate_support_function(self._iv(lo, hi), -a)
                if h_inf.is_finite:
                    assert h_sup.is_finite
                    assert h_inf.value is not None and h_sup.value is not None
                    assert h_inf.value == -h_sup.value
                else:
                    assert h_sup.is_infinite


class TestBoundContradiction:
    def test_typed_exact_contradiction(self) -> None:
        with pytest.raises(BoundContradictionError) as exc:
            Interval.from_bounds(5, 2)
        assert exc.value.code == BOUND_CONTRADICTION_EXACT == "BOUND_CONTRADICTION_EXACT"
        assert exc.value.lower == 5 and exc.value.upper == 2
        assert ["5", "<=", "x", "<=", "2"][0] == "5"  # documentation example

    def test_is_value_error_but_distinct_from_malformed_input(self) -> None:
        with pytest.raises(ValueError):
            Interval.from_bounds(1, 0)
        with pytest.raises(TypeError) as exc:
            Interval.from_bounds(True, 2)
        assert not isinstance(exc.value, BoundContradictionError)
        with pytest.raises(ValueError) as exc2:
            Interval.from_bounds(float("nan"), 1)
        assert not isinstance(exc2.value, BoundContradictionError)

    def test_direct_endpoint_construction_also_typed(self) -> None:
        with pytest.raises(BoundContradictionError):
            Interval(
                IntervalEndpoint.finite(Fraction(1, 2)), IntervalEndpoint.finite(Fraction(1, 3))
            )

    def test_exact_comparison_not_float(self) -> None:
        with pytest.raises(BoundContradictionError):
            Interval.from_bounds(Fraction(1, 3) + Fraction(1, 10**30), Fraction(1, 3))
        assert Interval.from_bounds(2, 2).lower.value == 2

    def test_infinite_and_open_bounds_not_contradictions(self) -> None:
        Interval.from_bounds(None, 2)
        Interval.from_bounds(5, None)
        Interval.from_bounds(None, None)

    def test_infinite_endpoint_misuse_is_not_a_bound_contradiction(self) -> None:
        with pytest.raises(ValueError) as exc:
            Interval(IntervalEndpoint.plus_infinity(), IntervalEndpoint.finite(1))
        assert not isinstance(exc.value, BoundContradictionError)


class TestRationalSizeLimits:
    def test_bit_length_and_check(self) -> None:
        assert rational_bit_length(Fraction(255, 2)) == 8
        assert check_rational_size(Fraction(2**100), 101) == 2**100
        with pytest.raises(ExactResourceError):
            check_rational_size(Fraction(2**100), 100)
        with pytest.raises(ExactResourceError):
            check_rational_size(Fraction(1, 2**100), 100)
        with pytest.raises(ExactResourceError):
            check_rational_size(Fraction(-(2**100)), 100)

    def test_every_binary64_value_fits_default_limit(self) -> None:
        for v in (1.7976931348623157e308, 5e-324, 0.1, 2.2250738585072014e-308):
            check_rational_size(to_fraction(v))


class TestSignedZeroAndSubnormals:
    def test_negative_zero_normalizes_to_exact_zero(self) -> None:
        assert to_fraction(-0.0) == 0
        out = lower_to_binary64(to_fraction(-0.0)).value
        assert out == 0.0 and math.copysign(1.0, out) == 1.0

    def test_exact_zero_lowers_to_positive_zero(self) -> None:
        assert math.copysign(1.0, lower_to_binary64(Fraction(0)).value) == 1.0

    def test_negative_tiny_nonzero_underflow_rejected(self) -> None:
        with pytest.raises(CoefficientUnderflowError):
            lower_to_binary64(-Fraction(1, 2**1076))

    def test_subnormals_are_accepted_and_reported(self) -> None:
        r = lower_to_binary64(Fraction(1, 2**1074))
        assert r.value == 5e-324 and r.is_exact and is_subnormal_binary64(r.value)
        r2 = lower_to_binary64(Fraction(3, 2**1076))  # rounds to a subnormal, inexact
        assert is_subnormal_binary64(r2.value) and not r2.is_exact
        n = lower_to_binary64(Fraction(2.2250738585072014e-308))
        assert not is_subnormal_binary64(n.value)
        assert not is_subnormal_binary64(0.0)

    def test_overflow_boundary(self) -> None:
        mx = Fraction(1.7976931348623157e308)
        assert lower_to_binary64(mx).is_exact
        with pytest.raises(CoefficientOverflowError):
            lower_to_binary64(mx * 2)

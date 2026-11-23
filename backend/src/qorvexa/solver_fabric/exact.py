"""Exact rational arithmetic and interval support function evaluation for QORVEXA Solver Fabric.

Uses Python standard library fractions.Fraction for exact arithmetic.
Enforces:
- Finite int/float conversion to exact Fraction (exactnessScope = "canonicalized_input_values")
- Rejection of boolean types, NaN, and infinities
- Correctly rounded binary64 IEEE-754 conversion with exact rounding error tracking
- Safe representation of infinite interval endpoints (without 0 * Infinity)
- Support function evaluation without silent clamping of wrong-sign multipliers
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from typing import Final

CANONICALIZED_INPUT_VALUES: Final[str] = "canonicalized_input_values"
BOUND_CONTRADICTION_EXACT: Final[str] = "BOUND_CONTRADICTION_EXACT"

# Deterministic default cap on the bit length of any exact numerator or denominator.
# IEEE-754 binary64 values need at most ~1100 bits, so this leaves generous headroom for
# exact integers and derived rationals while bounding the cost of exact arithmetic.
DEFAULT_MAX_RATIONAL_BITS: Final[int] = 4096


class ExactResourceError(ValueError):
    """An exact value exceeds the configured numerator/denominator bit-length cap."""


class BoundContradictionError(ValueError):
    """Exact bound contradiction (lower > upper), e.g. 5 <= x <= 2.

    Distinct from malformed input (TypeError/ValueError from conversion) and from a Farkas
    infeasibility certificate: this is a trivially exact proof that the variable's own
    interval is empty. Diagnostic code: BOUND_CONTRADICTION_EXACT.
    """

    code = BOUND_CONTRADICTION_EXACT

    def __init__(self, lower: Fraction, upper: Fraction) -> None:
        super().__init__(
            f"{BOUND_CONTRADICTION_EXACT}: lower bound {lower} exceeds upper bound {upper}"
        )
        self.lower = lower
        self.upper = upper


def rational_bit_length(frac: Fraction) -> int:
    """Largest bit length among |numerator| and denominator."""
    return max(abs(frac.numerator).bit_length(), frac.denominator.bit_length())


def check_rational_size(frac: Fraction, max_bits: int = DEFAULT_MAX_RATIONAL_BITS) -> Fraction:
    """Return `frac` or raise ExactResourceError if its numerator/denominator is too large."""
    bits = rational_bit_length(frac)
    if bits > max_bits:
        raise ExactResourceError(
            f"Exact value needs {bits} bits (limit {max_bits}) in numerator or denominator."
        )
    return frac


class BoundKind(StrEnum):
    """Classification of an interval boundary endpoint."""

    FINITE = "FINITE"
    PLUS_INFINITY = "PLUS_INFINITY"
    MINUS_INFINITY = "MINUS_INFINITY"


@dataclass(frozen=True)
class Binary64Rounded:
    """Represents a float rounded from an exact Fraction with its exact rounding error."""

    value: float
    rounding_error: Fraction  # Fraction(value) - original_fraction
    is_exact: bool


def to_fraction(val: int | float | Fraction) -> Fraction:
    """Converts a numeric value to an exact rational Fraction.

    Rejects:
    - Booleans (bool is a subclass of int in Python, so checked first)
    - NaN (not-a-number)
    - Infinities (+inf, -inf)

    For float inputs under exactnessScope = 'canonicalized_input_values',
    converts the binary64 IEEE-754 representation directly to its exact rational equivalent.
    """
    if isinstance(val, bool):
        raise TypeError(
            f"Boolean value '{val}' is rejected; booleans are not valid numeric inputs."
        )

    if isinstance(val, Fraction):
        return val

    if isinstance(val, int):
        return Fraction(val, 1)

    if isinstance(val, float):
        if math.isnan(val):
            raise ValueError("NaN is rejected; cannot convert NaN to an exact rational Fraction.")
        if math.isinf(val):
            raise ValueError(f"Infinity '{val}' is rejected; cannot represent as finite Fraction.")
        return Fraction(val)

    if isinstance(val, str):
        raise TypeError(
            "String values are rejected; numeric inputs must be int, float or Fraction."
        )

    raise TypeError(
        f"Unsupported numeric type '{type(val).__name__}' for exact Fraction conversion: {val!r}"
    )


def to_binary64(frac: Fraction) -> Binary64Rounded:
    """Converts an exact Fraction to an IEEE-754 binary64 float with rounding error tracking.

    Returns Binary64Rounded containing:
    - value: binary64 float
    - rounding_error: exact Fraction difference (Fraction(value) - frac)
    - is_exact: True iff rounding_error == 0
    """
    if not isinstance(frac, Fraction):
        raise TypeError(f"Expected Fraction, got {type(frac).__name__}")

    try:
        float_val = float(frac)
    except OverflowError as err:
        raise OverflowError(f"Fraction {frac} overflows IEEE-754 binary64 float") from err

    error = Fraction(float_val) - frac
    return Binary64Rounded(
        value=float_val,
        rounding_error=error,
        is_exact=(error == Fraction(0)),
    )


@dataclass(frozen=True)
class IntervalEndpoint:
    """Represents an endpoint of an interval in Q union {-inf, +inf}."""

    kind: BoundKind
    value: Fraction | None = None

    def __post_init__(self) -> None:
        if self.kind == BoundKind.FINITE:
            if self.value is None or not isinstance(self.value, Fraction):
                raise TypeError("Finite endpoint requires a Fraction value")
        else:
            if self.value is not None:
                raise ValueError(f"Infinite endpoint {self.kind} must have value=None")

    @classmethod
    def finite(cls, val: int | float | Fraction) -> IntervalEndpoint:
        """Create a finite endpoint from a numeric value."""
        return cls(kind=BoundKind.FINITE, value=to_fraction(val))

    @classmethod
    def plus_infinity(cls) -> IntervalEndpoint:
        """Create a +Infinity endpoint."""
        return cls(kind=BoundKind.PLUS_INFINITY, value=None)

    @classmethod
    def minus_infinity(cls) -> IntervalEndpoint:
        """Create a -Infinity endpoint."""
        return cls(kind=BoundKind.MINUS_INFINITY, value=None)

    @property
    def is_finite(self) -> bool:
        return self.kind == BoundKind.FINITE

    @property
    def is_pos_inf(self) -> bool:
        return self.kind == BoundKind.PLUS_INFINITY

    @property
    def is_neg_inf(self) -> bool:
        return self.kind == BoundKind.MINUS_INFINITY

    def __lt__(self, other: IntervalEndpoint) -> bool:
        if not isinstance(other, IntervalEndpoint):
            return NotImplemented
        if self.kind == BoundKind.MINUS_INFINITY:
            return other.kind != BoundKind.MINUS_INFINITY
        if self.kind == BoundKind.PLUS_INFINITY:
            return False
        # self is FINITE
        if other.kind == BoundKind.PLUS_INFINITY:
            return True
        if other.kind == BoundKind.MINUS_INFINITY:
            return False
        if self.value is None or other.value is None:
            raise ValueError("Endpoint comparison requires non-null finite values")
        return self.value < other.value

    def __le__(self, other: IntervalEndpoint) -> bool:
        return self == other or self < other

    def __gt__(self, other: IntervalEndpoint) -> bool:
        if not isinstance(other, IntervalEndpoint):
            return NotImplemented
        return other < self

    def __ge__(self, other: IntervalEndpoint) -> bool:
        return self == other or self > other


@dataclass(frozen=True)
class Interval:
    """Represents an interval on the extended real line [lower, upper]."""

    lower: IntervalEndpoint
    upper: IntervalEndpoint

    def __post_init__(self) -> None:
        if self.lower.is_pos_inf:
            raise ValueError("Lower bound cannot be +Infinity")
        if self.upper.is_neg_inf:
            raise ValueError("Upper bound cannot be -Infinity")
        if self.lower.is_finite and self.upper.is_finite:
            if self.lower.value is None or self.upper.value is None:
                raise ValueError("Finite interval endpoints must have non-null values")
            if self.lower.value > self.upper.value:
                raise BoundContradictionError(self.lower.value, self.upper.value)

    @classmethod
    def from_bounds(
        cls,
        lower: int | float | Fraction | None,
        upper: int | float | Fraction | None,
    ) -> Interval:
        """Construct an interval where None represents infinity."""
        lower_ep = (
            IntervalEndpoint.minus_infinity() if lower is None else IntervalEndpoint.finite(lower)
        )
        upper_ep = (
            IntervalEndpoint.plus_infinity() if upper is None else IntervalEndpoint.finite(upper)
        )
        return cls(lower=lower_ep, upper=upper_ep)


@dataclass(frozen=True)
class SupportValue:
    """Result of support function evaluation: finite Fraction or +Infinity."""

    kind: BoundKind  # BoundKind.FINITE or BoundKind.PLUS_INFINITY
    value: Fraction | None = None

    def __post_init__(self) -> None:
        if self.kind == BoundKind.FINITE:
            if self.value is None or not isinstance(self.value, Fraction):
                raise TypeError("Finite SupportValue requires a Fraction value")
        elif self.kind == BoundKind.PLUS_INFINITY:
            if self.value is not None:
                raise ValueError("+Infinity SupportValue must have value=None")
        else:
            raise ValueError(f"SupportValue cannot be {self.kind}")

    @property
    def is_finite(self) -> bool:
        return self.kind == BoundKind.FINITE

    @property
    def is_infinite(self) -> bool:
        return self.kind == BoundKind.PLUS_INFINITY

    @classmethod
    def finite(cls, val: Fraction) -> SupportValue:
        return cls(kind=BoundKind.FINITE, value=val)

    @classmethod
    def plus_infinity(cls) -> SupportValue:
        return cls(kind=BoundKind.PLUS_INFINITY, value=None)


def evaluate_support_function(
    interval: Interval,
    multiplier: int | float | Fraction,
) -> SupportValue:
    """Evaluates the support function h(y) = sup_{x in [lower, upper]} (y * x) exactly.

    Rules:
    - If y == 0:
        h(0) = 0 * x = 0 for all x in interval.
        Avoids 0 * Infinity: evaluates strictly to Fraction(0).
    - If y > 0:
        h(y) = y * upper.
        If upper == +Infinity, h(y) = +Infinity.
        If upper is finite, h(y) = y * upper.value (exact Fraction).
    - If y < 0:
        h(y) = y * lower.
        If lower == -Infinity, as x -> -Infinity, y * x -> +Infinity.
        So h(y) = +Infinity.
        If lower is finite, h(y) = y * lower.value (exact Fraction).

    Crucial invariant:
    Does NOT silently clamp wrong-sign nonzero multipliers on infinite bounds to 0 or finite values.
    Returns SupportValue.plus_infinity() when supremum is unbounded.
    """
    y = to_fraction(multiplier)

    # 1. Multiplier is zero: supremum of 0 over any non-empty set is 0. Avoid 0 * inf.
    if y == Fraction(0):
        return SupportValue.finite(Fraction(0))

    # 2. Multiplier is positive
    if y > Fraction(0):
        if interval.upper.is_pos_inf:
            return SupportValue.plus_infinity()
        if interval.upper.value is None:
            raise ValueError("Finite upper bound endpoint missing numeric value")
        return SupportValue.finite(y * interval.upper.value)

    # 3. Multiplier is negative (y < 0)
    if interval.lower.is_neg_inf:
        return SupportValue.plus_infinity()
    if interval.lower.value is None:
        raise ValueError("Finite lower bound endpoint missing numeric value")
    return SupportValue.finite(y * interval.lower.value)


class CoefficientOverflowError(OverflowError):
    """Exact value is too large for finite IEEE-754 binary64."""


class CoefficientUnderflowError(ValueError):
    """Nonzero exact value would round to zero in binary64."""


def lower_to_binary64(frac: Fraction) -> Binary64Rounded:
    """Checked binary64 lowering: rejects overflow and nonzero-to-zero underflow.

    Policy:
    - Correct rounding (round-half-even) via float(Fraction).
    - Overflow to infinity is rejected (CoefficientOverflowError).
    - A nonzero exact value that would become 0.0 is rejected (CoefficientUnderflowError).
    - Subnormal results are accepted: they are valid binary64 values and the rounding error is
      reported. Callers may count them via `is_subnormal_binary64`.
    - Signed zero: the exact value 0 lowers to +0.0. Exact rationals have no negative zero, and
      a -0.0 literal is normalized to exact 0 by to_fraction, so -0.0 is never produced.
    """
    if not isinstance(frac, Fraction):
        raise TypeError(f"Expected Fraction, got {type(frac).__name__}")
    try:
        result = to_binary64(frac)
    except OverflowError as err:
        raise CoefficientOverflowError(str(err)) from err
    if result.value == 0.0 and frac != 0:
        raise CoefficientUnderflowError(f"Nonzero value {frac} underflows to 0.0 in binary64")
    if math.isinf(result.value):
        raise CoefficientOverflowError(f"Value {frac} rounds to infinity")
    return result


@dataclass(frozen=True)
class ExtendedValue:
    """Finite Fraction or -Infinity (result of the infimum-form support function)."""

    kind: BoundKind
    value: Fraction | None = None

    def __post_init__(self) -> None:
        if self.kind == BoundKind.FINITE:
            if not isinstance(self.value, Fraction):
                raise TypeError("Finite ExtendedValue requires a Fraction value")
        elif self.kind == BoundKind.MINUS_INFINITY:
            if self.value is not None:
                raise ValueError("-Infinity ExtendedValue must have value=None")
        else:
            raise ValueError(f"ExtendedValue cannot be {self.kind}")

    @property
    def is_finite(self) -> bool:
        return self.kind == BoundKind.FINITE


def evaluate_infimum_support(
    interval: Interval, multiplier: int | float | Fraction
) -> ExtendedValue:
    """h(I, a) = inf_{x in I} a*x; -inf when unbounded below. Never computes 0 * inf.

    Identity: h_inf(I, a) = -h_sup(I, -a).
    """
    a = to_fraction(multiplier)
    if a == 0:
        return ExtendedValue(BoundKind.FINITE, Fraction(0))
    end = interval.lower if a > 0 else interval.upper
    if not end.is_finite:
        return ExtendedValue(BoundKind.MINUS_INFINITY)
    if end.value is None:
        raise ValueError("Finite endpoint missing numeric value")
    return ExtendedValue(BoundKind.FINITE, a * end.value)


def is_subnormal_binary64(value: float) -> bool:
    """True iff `value` is a nonzero subnormal binary64."""
    return value != 0.0 and abs(value) < 2.2250738585072014e-308

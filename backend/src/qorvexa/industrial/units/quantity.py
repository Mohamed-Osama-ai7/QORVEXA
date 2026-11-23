"""Exact typed quantities, compatibility classification and explicit traceable conversion."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from fractions import Fraction
from typing import Any, Final

from qorvexa.industrial.units.dimension import Dimension, UnitError
from qorvexa.industrial.units.parser import Unit, parse_unit
from qorvexa.industrial.units.registry import UnitRegistry, check_size

MAX_ABS: Final = Fraction(10) ** 30
MAX_DIGITS: Final = 40


class Compat(StrEnum):
    SAME_UNIT = "SAME_UNIT"  # identical dimension and scale
    CONVERTIBLE = "CONVERTIBLE"  # same dimension, different scale: explicit conversion required
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"  # physical parts equal, currencies differ: never FX
    DIMENSION_MISMATCH = "DIMENSION_MISMATCH"


def classify(a: Unit, b: Unit) -> Compat:
    if a.dimension == b.dimension:
        return Compat.SAME_UNIT if a.scale == b.scale else Compat.CONVERTIBLE
    if a.dimension.currency_part() != b.dimension.currency_part() and (
        a.dimension.physical_part() == b.dimension.physical_part()
    ):
        return Compat.CURRENCY_MISMATCH
    return Compat.DIMENSION_MISMATCH


def exact_number(v: Any) -> Fraction:
    """Exact rational from int / Decimal / Fraction / decimal string. Floats and bools refused."""
    if isinstance(v, bool | float):
        raise UnitError("FLOAT_REFUSED", "binary floats and booleans are not exact numbers")
    try:
        if isinstance(v, Fraction):
            f = v
        elif isinstance(v, int):
            f = Fraction(v)
        elif isinstance(v, Decimal | str):
            d = v if isinstance(v, Decimal) else Decimal(v.strip())
            if not d.is_finite():
                raise UnitError("INVALID_NUMBER", "value must be finite")
            if len(d.as_tuple().digits) > MAX_DIGITS:
                raise UnitError("INVALID_NUMBER", f"more than {MAX_DIGITS} significant digits")
            f = Fraction(d)
        else:
            raise UnitError("INVALID_NUMBER", f"unsupported number type {type(v).__name__}")
    except (InvalidOperation, ValueError) as exc:
        raise UnitError("INVALID_NUMBER", "not a decimal number") from exc
    if abs(f) > MAX_ABS:
        raise UnitError("OVERFLOW", "magnitude exceeds 1e30")
    return check_size(f)


@dataclass(frozen=True)
class Quantity:
    value: Fraction
    unit: Unit

    @staticmethod
    def of(value: Any, unit: str | Unit, registry: UnitRegistry | None = None) -> Quantity:
        u = unit if isinstance(unit, Unit) else parse_unit(unit, registry)
        return Quantity(exact_number(value), u)

    def _require_same(self, other: Quantity) -> None:
        c = classify(self.unit, other.unit)
        if c is Compat.SAME_UNIT:
            return
        msg = f"{self.unit.symbol} vs {other.unit.symbol}"
        if c is Compat.CONVERTIBLE:
            raise UnitError("CONVERSION_REQUIRED", f"convert explicitly first ({msg})")
        raise UnitError(c.value, msg)

    def add(self, other: Quantity) -> Quantity:
        self._require_same(other)
        return Quantity(self._bound(self.value + other.value), self.unit)

    def sub(self, other: Quantity) -> Quantity:
        self._require_same(other)
        return Quantity(self._bound(self.value - other.value), self.unit)

    def neg(self) -> Quantity:
        return Quantity(-self.value, self.unit)

    def mul(self, other: Quantity) -> Quantity:
        return Quantity(self._bound(self.value * other.value), self.unit.mul(other.unit))

    def div(self, other: Quantity) -> Quantity:
        if other.value == 0:
            raise UnitError("DIVISION_BY_ZERO", "division by a zero quantity")
        return Quantity(self._bound(self.value / other.value), self.unit.div(other.unit))

    @staticmethod
    def _bound(x: Fraction) -> Fraction:
        if abs(x) > MAX_ABS * MAX_ABS:
            raise UnitError("OVERFLOW", "result magnitude exceeds supported range")
        return check_size(x)

    def compare(self, other: Quantity) -> int:
        """Exact three-way comparison across convertible units; never across dimensions."""
        c = classify(self.unit, other.unit)
        if c not in (Compat.SAME_UNIT, Compat.CONVERTIBLE):
            raise UnitError(c.value, f"{self.unit.symbol} vs {other.unit.symbol}")
        a, b = self.value * self.unit.scale, other.value * other.unit.scale
        return (a > b) - (a < b)

    def to(self, target: str | Unit, registry: UnitRegistry | None = None) -> ConversionResult:
        t = target if isinstance(target, Unit) else parse_unit(target, registry)
        c = classify(self.unit, t)
        if c is Compat.CURRENCY_MISMATCH:
            raise UnitError(
                "CURRENCY_MISMATCH",
                f"{self.unit.symbol} -> {t.symbol}: no exchange-rate policy exists; refusing",
            )
        if c is Compat.DIMENSION_MISMATCH:
            raise UnitError(
                "DIMENSION_MISMATCH",
                f"{self.unit.symbol} [{self.unit.dimension}] cannot become "
                f"{t.symbol} [{t.dimension}]",
            )
        factor = check_size(self.unit.scale / t.scale)
        result = Quantity(self._bound(self.value * factor), t)
        return ConversionResult(self, t, factor, result)

    def __str__(self) -> str:
        v = self.value
        text = str(v.numerator) if v.denominator == 1 else f"{v.numerator}/{v.denominator}"
        return f"{text} {self.unit.symbol}"


@dataclass(frozen=True)
class ConversionResult:
    """Traceable exact conversion: result.value == source.value x factor."""

    source: Quantity
    target_unit: Unit
    factor: Fraction
    result: Quantity
    rule: str = "EXACT_SCALE_RATIO"

    @property
    def trace(self) -> tuple[str, ...]:
        return (
            f"source: {self.source}",
            f"factor: {self.source.unit.symbol} -> {self.target_unit.symbol} = "
            f"{self.factor.numerator}/{self.factor.denominator}",
            f"result: {self.result}",
        )


def dimension_of(unit: str, registry: UnitRegistry | None = None) -> Dimension:
    return parse_unit(unit, registry).dimension

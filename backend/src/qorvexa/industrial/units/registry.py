"""Strict unit registry: canonical symbols, aliases, exact rational scales to coherent SI-like base.

Nothing is guessed: unknown, ambiguous and offset-based units are refused with explicit codes.
Custom units must be declared explicitly relative to an existing unit by an exact factor.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Final

from qorvexa.industrial.units.dimension import (
    AREA,
    COUNT,
    ENERGY,
    LENGTH,
    MASS,
    POWER,
    TIME,
    VOLUME,
    Dimension,
    UnitError,
    base,
)


@dataclass(frozen=True)
class UnitDef:
    symbol: str
    dimension: Dimension
    scale: Fraction  # value in this unit * scale = value in the coherent base unit
    aliases: tuple[str, ...] = ()
    category: str = ""


def _f(s: str) -> Fraction:
    return Fraction(Decimal(s))


CURRENCIES: Final = ("USD", "EUR", "EGP", "GBP", "SAR", "AED", "JPY", "CNY", "CHF", "CAD", "AUD")

_BUILTIN: Final[tuple[UnitDef, ...]] = (
    UnitDef("m", LENGTH, Fraction(1), ("meter", "metre"), "length"),
    UnitDef("mm", LENGTH, _f("0.001"), ("millimeter",), "length"),
    UnitDef("cm", LENGTH, _f("0.01"), ("centimeter",), "length"),
    UnitDef("km", LENGTH, Fraction(1000), ("kilometer",), "length"),
    UnitDef("in", LENGTH, _f("0.0254"), ("inch",), "length"),
    UnitDef("ft", LENGTH, _f("0.3048"), ("foot", "feet"), "length"),
    UnitDef("kg", MASS, Fraction(1), ("kilogram", "kilograms"), "mass"),
    UnitDef("g", MASS, _f("0.001"), ("gram", "grams"), "mass"),
    UnitDef("mg", MASS, _f("0.000001"), ("milligram",), "mass"),
    UnitDef("tonne", MASS, Fraction(1000), ("tonnes", "metric_ton"), "mass"),
    UnitDef("lb", MASS, _f("0.45359237"), ("pound_mass", "lbs"), "mass"),
    UnitDef("s", TIME, Fraction(1), ("sec", "second", "seconds"), "time"),
    UnitDef("min", TIME, Fraction(60), ("minute", "minutes"), "time"),
    UnitDef("h", TIME, Fraction(3600), ("hr", "hour", "hours"), "time"),
    UnitDef("day", TIME, Fraction(86400), ("days",), "time"),
    UnitDef("week", TIME, Fraction(604800), ("weeks",), "time"),
    UnitDef("piece", COUNT, Fraction(1), ("pcs", "pc", "pieces", "each"), "count"),
    UnitDef("dozen", COUNT, Fraction(12), (), "count"),
    UnitDef("ha", AREA, Fraction(10000), ("hectare",), "area"),
    UnitDef("L", VOLUME, _f("0.001"), ("l", "liter", "litre", "liters"), "volume"),
    UnitDef("mL", VOLUME, _f("0.000001"), ("ml", "milliliter"), "volume"),
    UnitDef("J", ENERGY, Fraction(1), ("joule",), "energy"),
    UnitDef("kJ", ENERGY, Fraction(1000), (), "energy"),
    UnitDef("MJ", ENERGY, Fraction(1_000_000), (), "energy"),
    UnitDef("Wh", ENERGY, Fraction(3600), (), "energy"),
    UnitDef("kWh", ENERGY, Fraction(3_600_000), (), "energy"),
    UnitDef("MWh", ENERGY, Fraction(3_600_000_000), (), "energy"),
    UnitDef("W", POWER, Fraction(1), ("watt",), "power"),
    UnitDef("kW", POWER, Fraction(1000), (), "power"),
    UnitDef("MW", POWER, Fraction(1_000_000), (), "power"),
    *(UnitDef(c, base(f"CUR:{c}"), Fraction(1), (), "currency") for c in CURRENCIES),
)

# refused with a precise reason instead of a guess
AMBIGUOUS: Final = frozenset(
    {"ton", "tons", "oz", "ounce", "gal", "gallon", "pound", "btu", "BTU", "hp", "cal", "mil", "t"}
)
OFFSET_UNITS: Final = frozenset(
    {"degC", "degF", "celsius", "fahrenheit", "°C", "°F", "C", "F", "K", "kelvin"}
)
MAX_BITS: Final = 2048


def check_size(x: Fraction) -> Fraction:
    if x.numerator.bit_length() > MAX_BITS or x.denominator.bit_length() > MAX_BITS:
        raise UnitError("OVERFLOW", "exact scale exceeds supported size")
    return x


class UnitRegistry:
    def __init__(self, defs: tuple[UnitDef, ...] = _BUILTIN) -> None:
        self._by_symbol: dict[str, UnitDef] = {}
        self._alias: dict[str, str] = {}
        for d in defs:
            self._add(d)

    def _add(self, d: UnitDef) -> None:
        for name in (d.symbol, *d.aliases):
            if name in self._alias or name in AMBIGUOUS or name in OFFSET_UNITS:
                raise UnitError("DUPLICATE_UNIT", f"unit name {name!r} is already defined/refused")
        self._by_symbol[d.symbol] = d
        for name in (d.symbol, *d.aliases):
            self._alias[name] = d.symbol

    @staticmethod
    def default() -> UnitRegistry:
        return UnitRegistry()

    def extended(self, symbol: str, of: str, factor: str) -> UnitRegistry:
        """Return a NEW registry with 1 ``symbol`` = ``factor`` x ``of`` (exact decimal string)."""
        if not symbol.isidentifier() or len(symbol) > 24:
            raise UnitError("INVALID_CUSTOM_UNIT", "custom unit symbol must be a short identifier")
        target = self.resolve(of)
        try:
            ratio = Fraction(Decimal(factor))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise UnitError(
                "INVALID_CUSTOM_UNIT", "factor must be an exact decimal string"
            ) from exc
        if ratio <= 0 or isinstance(factor, float):
            raise UnitError("INVALID_CUSTOM_UNIT", "factor must be positive")
        if target.category == "currency":
            raise UnitError("INVALID_CUSTOM_UNIT", "currencies cannot be redefined or scaled")
        new = UnitRegistry(tuple(self._by_symbol.values()))
        new._add(UnitDef(symbol, target.dimension, check_size(target.scale * ratio), (), "custom"))
        return new

    def extended_raw(self, symbol: str, dimension: Dimension, scale: Fraction) -> UnitRegistry:
        """NEW registry with a custom unit of an exact dimension and scale (no currencies)."""
        if not symbol.isidentifier() or len(symbol) > 24 or not symbol.isascii():
            raise UnitError("INVALID_CUSTOM_UNIT", "custom unit symbol must be a short identifier")
        if scale <= 0:
            raise UnitError("INVALID_CUSTOM_UNIT", "scale must be positive")
        if dimension.currency_part().exps:
            raise UnitError("INVALID_CUSTOM_UNIT", "currencies cannot be redefined or scaled")
        new = UnitRegistry(tuple(self._by_symbol.values()))
        new._add(UnitDef(symbol, dimension, check_size(scale), (), "custom"))
        return new

    def resolve(self, name: str) -> UnitDef:
        if name in OFFSET_UNITS:
            raise UnitError(
                "OFFSET_UNIT_UNSUPPORTED", f"{name!r} is offset-based; not supported (no guessing)"
            )
        if name in AMBIGUOUS:
            raise UnitError("AMBIGUOUS_UNIT", f"{name!r} is ambiguous; use an explicit unit")
        sym = self._alias.get(name)
        if sym is None:
            raise UnitError("UNKNOWN_UNIT", f"unknown unit {name!r}")
        return self._by_symbol[sym]

    def fingerprint(self) -> tuple[tuple[str, str, str], ...]:
        """Deterministic identity of every custom unit (symbol, dimension, exact scale)."""
        return tuple(
            (d.symbol, str(d.dimension), f"{d.scale.numerator}/{d.scale.denominator}")
            for d in sorted(self._by_symbol.values(), key=lambda x: x.symbol)
            if d.category == "custom"
        )

    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_symbol))

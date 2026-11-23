"""Deterministic registry construction from declared custom units (duplicates/cycles rejected).

``1 <symbol> = <factor> x <expression>``; the expression may use built-ins and other custom
units. Definitions are resolved in dependency order (sorted by symbol, so the result never
depends on declaration order); unresolved leftovers are reported as circular or invalid.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

from qorvexa.industrial.units.dimension import UnitError
from qorvexa.industrial.units.parser import parse_unit
from qorvexa.industrial.units.quantity import exact_number
from qorvexa.industrial.units.registry import UnitRegistry

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class CustomUnitDef:
    symbol: str
    expression: str  # base unit expression, e.g. "piece" or "kg*m^2"
    factor: Fraction  # exact; 1 symbol = factor x expression


def parse_factor(text: str) -> Fraction:
    if "/" in text:  # exact "n/d" form used in evidence and records
        n, _, d = text.partition("/")
        try:
            f = Fraction(exact_number(n.strip()), exact_number(d.strip()))
        except ZeroDivisionError as exc:
            raise UnitError("INVALID_CUSTOM_UNIT", "zero denominator") from exc
    else:
        f = exact_number(text)
    if f <= 0:
        raise UnitError("INVALID_CUSTOM_UNIT", "factor must be positive")
    return f


def build_registry(
    defs: Sequence[CustomUnitDef], base: UnitRegistry | None = None
) -> tuple[UnitRegistry, dict[str, UnitError]]:
    """Return (registry of all resolvable definitions, per-symbol errors)."""
    reg = base or UnitRegistry.default()
    errors: dict[str, UnitError] = {}
    seen: set[str] = set()
    pending: dict[str, CustomUnitDef] = {}
    for d in sorted(defs, key=lambda x: x.symbol):
        if d.symbol in seen:
            errors[d.symbol] = UnitError("DUPLICATE_UNIT", f"{d.symbol!r} defined more than once")
            pending.pop(d.symbol, None)
            continue
        seen.add(d.symbol)
        pending[d.symbol] = d
    progress = True
    while pending and progress:
        progress = False
        for sym in sorted(pending):
            d = pending[sym]
            try:
                u = parse_unit(d.expression, reg)
                reg = reg.extended_raw(sym, u.dimension, u.scale * d.factor)
            except UnitError as exc:
                if (
                    exc.code == "UNKNOWN_UNIT"
                    and _IDENT.findall(d.expression)
                    and any(n in pending for n in _IDENT.findall(d.expression))
                ):
                    continue  # may resolve once its dependency is defined
                errors[sym] = exc
                del pending[sym]
                progress = True
                break
            del pending[sym]
            progress = True
            break
    for sym in sorted(pending):
        errors[sym] = UnitError(
            "CIRCULAR_DEFINITION", f"{sym!r} depends on itself or on an unresolvable unit"
        )
    return reg, errors

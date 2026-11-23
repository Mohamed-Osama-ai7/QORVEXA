"""Exact Decimal helpers (fixed 50-digit context; no binary floats in decision arithmetic)."""

from __future__ import annotations

from decimal import Context, Decimal, InvalidOperation
from typing import Final

CTX: Final = Context(prec=50)
REL_TOL: Final = Decimal("1e-6")
ZERO: Final = Decimal(0)


def close(a: Decimal, b: Decimal) -> bool:
    """Equality within the report tolerance (solver output is binary64)."""
    return abs(a - b) <= REL_TOL * max(Decimal(1), abs(a), abs(b))


def absolute_change(base: Decimal, new: Decimal) -> Decimal:
    return CTX.subtract(new, base)


def relative_change(base: Decimal, new: Decimal) -> Decimal | None:
    """(new - base) / |base|; None when the baseline is zero (undefined, never a fake number)."""
    if base == 0:
        return None
    return CTX.divide(CTX.subtract(new, base), abs(base))


def fmt(d: Decimal | None, places: int = 6) -> str:
    """Deterministic text for explanations."""
    if d is None:
        return "n/a"
    try:
        q = d.quantize(Decimal(1).scaleb(-places), context=CTX)
    except InvalidOperation:  # more than 50 digits: fall back to the exact plain form
        q = d
    s = format(q, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s

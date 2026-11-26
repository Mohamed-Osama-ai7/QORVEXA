"""Pure normalization helpers: timestamps, units, source-quality mapping. No I/O."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Final, Literal

from qorvexa.connectors.telemetry.models import DataQuality


class UnitConversionError(ValueError):
    """The unit is unknown for conversion or belongs to a different physical kind."""


# ---- timestamps ------------------------------------------------------------------------------

EpochUnit = Literal["s", "ms"]


@dataclass(frozen=True)
class TimestampResult:
    value: datetime | None
    reason: str | None = None


def parse_timestamp(
    raw: object, *, naive_tz: tzinfo | None = None, epoch_unit: EpochUnit | None = None
) -> TimestampResult:
    """Return an aware UTC datetime or a reason. Naive and numeric timestamps are refused unless
    the caller explicitly configured how to read them (guessing a timezone corrupts ordering)."""
    if raw is None:
        return TimestampResult(None, "TIMESTAMP_MISSING")
    if isinstance(raw, bool):
        return TimestampResult(None, "TIMESTAMP_UNPARSEABLE")
    if isinstance(raw, int | float):
        if epoch_unit is None:
            return TimestampResult(None, "TIMESTAMP_NUMERIC_UNIT_UNKNOWN")
        if not math.isfinite(raw):
            return TimestampResult(None, "TIMESTAMP_UNPARSEABLE")
        try:
            seconds = raw if epoch_unit == "s" else raw / 1000.0
            return TimestampResult(datetime.fromtimestamp(seconds, UTC))
        except (OverflowError, OSError, ValueError):
            return TimestampResult(None, "TIMESTAMP_OUT_OF_RANGE")
    dt: datetime
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if not text or len(text) > 64:
            return TimestampResult(None, "TIMESTAMP_UNPARSEABLE")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return TimestampResult(None, "TIMESTAMP_UNPARSEABLE")
    else:
        return TimestampResult(None, "TIMESTAMP_UNPARSEABLE")
    if dt.tzinfo is None or dt.utcoffset() is None:
        if naive_tz is None:
            return TimestampResult(None, "TIMESTAMP_NAIVE")
        dt = dt.replace(tzinfo=naive_tz)
    return TimestampResult(dt.astimezone(UTC))


# ---- units -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _U:
    kind: str
    scale: float  # base = x * scale + offset
    offset: float = 0.0


_UNITS: Final[dict[str, _U]] = {
    # temperature, base degC
    "degC": _U("temperature", 1.0),
    "degF": _U("temperature", 5.0 / 9.0, -32.0 * 5.0 / 9.0),
    "K": _U("temperature", 1.0, -273.15),
    # pressure, base Pa
    "Pa": _U("pressure", 1.0),
    "kPa": _U("pressure", 1e3),
    "MPa": _U("pressure", 1e6),
    "bar": _U("pressure", 1e5),
    "psi": _U("pressure", 6894.757293168),
    # velocity (vibration velocity, line speed), base m/s
    "m/s": _U("velocity", 1.0),
    "mm/s": _U("velocity", 1e-3),
    "in/s": _U("velocity", 0.0254),
}
_ALIASES: Final[dict[str, str]] = {
    "c": "degC", "°c": "degC", "degc": "degC", "celsius": "degC",
    "f": "degF", "°f": "degF", "degf": "degF", "fahrenheit": "degF",
    "k": "K", "kelvin": "K",
    "pa": "Pa", "kpa": "kPa", "mpa": "MPa", "bar": "bar", "psi": "psi",
    "m/s": "m/s", "mm/s": "mm/s", "mm/sec": "mm/s", "in/s": "in/s",
}  # fmt: skip


def canonical_unit_name(unit: str) -> str:
    """Map an alias to its canonical symbol; unknown units are returned stripped but unchanged."""
    stripped = unit.strip()
    return _ALIASES.get(stripped.lower(), stripped)


def convert_unit(value: float, from_unit: str, to_unit: str) -> tuple[float, str | None]:
    """Convert ``value``; return ``(result, rule)``. ``rule`` is ``None`` for an identity mapping.

    Raises ``UnitConversionError`` for unknown units (unless both names are identical) or when the
    units measure different kinds. Never guesses."""
    a, b = canonical_unit_name(from_unit), canonical_unit_name(to_unit)
    if a == b:
        return value, None
    ua, ub = _UNITS.get(a), _UNITS.get(b)
    if ua is None or ub is None:
        raise UnitConversionError(f"no conversion rule for {a!r} -> {b!r}")
    if ua.kind != ub.kind:
        raise UnitConversionError(f"{a!r} ({ua.kind}) cannot convert to {b!r} ({ub.kind})")
    base = value * ua.scale + ua.offset
    result = (base - ub.offset) / ub.scale
    rule = f"{a}->{b}: base=x*{ua.scale!r}+{ua.offset!r}; out=(base-{ub.offset!r})/{ub.scale!r}"
    return result, rule


# ---- source quality mapping ------------------------------------------------------------------

_TEXT_QUALITY: Final[dict[str, DataQuality]] = {
    "GOOD": DataQuality.GOOD,
    "OK": DataQuality.GOOD,
    "UNCERTAIN": DataQuality.UNCERTAIN,
    "BAD": DataQuality.BAD,
    "STALE": DataQuality.STALE,
    "MISSING": DataQuality.MISSING,
    "INVALID": DataQuality.INVALID,
}


def map_source_quality(
    raw: object, *, absent: DataQuality = DataQuality.UNCERTAIN
) -> tuple[DataQuality, str | None]:
    """Map an OPC UA StatusCode (int, severity in top two bits) or a text flag to ``DataQuality``.

    An absent flag is *not* assumed good: it maps to ``absent`` (default UNCERTAIN)."""
    if raw is None:
        return absent, "SOURCE_QUALITY_ABSENT"
    if isinstance(raw, bool):
        return DataQuality.INVALID, "SOURCE_QUALITY_UNRECOGNIZED"
    if isinstance(raw, int):
        if not 0 <= raw <= 0xFFFFFFFF:
            return DataQuality.INVALID, "SOURCE_QUALITY_UNRECOGNIZED"
        severity = raw >> 30
        if severity == 0:
            return DataQuality.GOOD, None
        if severity == 1:
            return DataQuality.UNCERTAIN, "SOURCE_QUALITY_UNCERTAIN"
        if severity == 2:
            return DataQuality.BAD, "SOURCE_QUALITY_BAD"
        return DataQuality.INVALID, "SOURCE_QUALITY_UNRECOGNIZED"
    if isinstance(raw, str):
        mapped = _TEXT_QUALITY.get(raw.strip().upper())
        if mapped is None:
            return DataQuality.INVALID, "SOURCE_QUALITY_UNRECOGNIZED"
        return mapped, None if mapped is DataQuality.GOOD else f"SOURCE_QUALITY_{mapped.value}"
    return DataQuality.INVALID, "SOURCE_QUALITY_UNRECOGNIZED"

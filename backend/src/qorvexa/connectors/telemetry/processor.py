"""Stateful, bounded-memory normalizer: RawMeasurement -> TelemetryRecord.

Rules: never raise on bad *data* (it becomes a record with a quality flag and reasons), never
substitute a value, never convert a unit without recording the rule, never treat absent source
quality as GOOD. Only unusable *identifiers* are rejected (no record can be keyed without them).
"""

from __future__ import annotations

import math
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from enum import StrEnum

from qorvexa.connectors.telemetry.models import (
    IDENTIFIER_PATTERN,
    DataQuality,
    RawMeasurement,
    TelemetryRecord,
    TelemetrySource,
    worst_quality,
)
from qorvexa.connectors.telemetry.normalize import (
    EpochUnit,
    UnitConversionError,
    canonical_unit_name,
    convert_unit,
    map_source_quality,
    parse_timestamp,
)

SeriesKey = tuple[str, str, str]


class Disposition(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ProcessResult:
    disposition: Disposition
    record: TelemetryRecord | None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class TagSpec:
    """Expected canonical unit for a tag; incoming values are converted or flagged INVALID."""

    canonical_unit: str


@dataclass(frozen=True)
class ProcessorConfig:
    max_age_s: float | None = 60.0  # None disables staleness (e.g. historian backfill)
    max_future_skew_s: float = 5.0
    naive_timezone: tzinfo | None = None
    epoch_unit: EpochUnit | None = None
    absent_quality: DataQuality = DataQuality.UNCERTAIN
    dedupe_window: int = 1024
    max_series: int = 10_000
    tag_specs: Mapping[str, TagSpec] = field(default_factory=dict)


@dataclass
class _Series:
    seen: OrderedDict[datetime, float | None] = field(default_factory=OrderedDict)
    high_water: datetime | None = None


class TelemetryProcessor:
    def __init__(
        self,
        config: ProcessorConfig | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._cfg = config or ProcessorConfig()
        self._clock = clock
        self._series: dict[SeriesKey, _Series] = {}
        self._lock = threading.Lock()

    # -- public ------------------------------------------------------------------------------

    def process(self, raw: RawMeasurement) -> ProcessResult:
        for label, ident in (
            ("factory_id", raw.factory_id),
            ("equipment_id", raw.equipment_id),
            ("tag_id", raw.tag_id),
        ):
            if not IDENTIFIER_PATTERN.fullmatch(ident):
                return ProcessResult(Disposition.REJECTED, None, (f"INVALID_{label.upper()}",))

        now = self._clock()
        reasons: list[str] = []
        qualities: list[DataQuality] = []

        source_q, source_reason = map_source_quality(
            raw.source_quality, absent=self._cfg.absent_quality
        )
        qualities.append(source_q)
        if source_reason:
            reasons.append(source_reason)

        ts = parse_timestamp(
            raw.timestamp, naive_tz=self._cfg.naive_timezone, epoch_unit=self._cfg.epoch_unit
        )
        timestamp = ts.value
        if timestamp is None:
            qualities.append(DataQuality.INVALID)
            reasons.append(ts.reason or "TIMESTAMP_UNPARSEABLE")
        elif timestamp > now + timedelta(seconds=self._cfg.max_future_skew_s):
            qualities.append(DataQuality.INVALID)
            reasons.append("TIMESTAMP_IN_FUTURE")
            timestamp = None

        value, unit, rule, q, r = self._normalize_value(raw)
        qualities.append(q)
        reasons.extend(r)

        if (
            timestamp is not None
            and self._cfg.max_age_s is not None
            and (now - timestamp).total_seconds() > self._cfg.max_age_s
        ):
            qualities.append(DataQuality.STALE)
            reasons.append("AGE_EXCEEDS_LIMIT")

        key: SeriesKey = (raw.factory_id, raw.equipment_id, raw.tag_id)
        out_of_order = False
        if timestamp is not None:
            with self._lock:
                verdict = self._register(key, timestamp, value)
            if verdict == "limit":
                return ProcessResult(Disposition.REJECTED, None, ("SERIES_LIMIT_REACHED",))
            if verdict == "duplicate":
                return ProcessResult(Disposition.DUPLICATE, None, ("DUPLICATE_TIMESTAMP",))
            if verdict == "conflict":
                return ProcessResult(Disposition.REJECTED, None, ("CONFLICTING_DUPLICATE",))
            if verdict == "out_of_order":
                out_of_order = True
                reasons.append("OUT_OF_ORDER")

        record = TelemetryRecord(
            factory_id=raw.factory_id,
            equipment_id=raw.equipment_id,
            tag_id=raw.tag_id,
            value=value,
            unit=unit,
            timestamp=timestamp,
            ingested_at=now,
            source=raw.source,
            source_id=raw.source_id,
            quality=worst_quality(*qualities),
            quality_reasons=tuple(dict.fromkeys(reasons)),
            raw_value_repr=_safe_repr(raw.value),
            raw_unit=raw.unit,
            conversion_rule=rule,
            source_quality_raw=None
            if raw.source_quality is None
            else _safe_repr(raw.source_quality),
            out_of_order=out_of_order,
            synthetic=raw.synthetic or raw.source is TelemetrySource.SIMULATED,
        )
        return ProcessResult(Disposition.ACCEPTED, record, record.quality_reasons)

    def stale_series(self, now: datetime | None = None) -> list[SeriesKey]:
        """Series whose newest sample is older than ``max_age_s`` (connector freshness signal)."""
        if self._cfg.max_age_s is None:
            return []
        current = now or self._clock()
        limit = timedelta(seconds=self._cfg.max_age_s)
        with self._lock:
            return sorted(
                k
                for k, s in self._series.items()
                if s.high_water and current - s.high_water > limit
            )

    # -- internals ---------------------------------------------------------------------------

    def _normalize_value(
        self, raw: RawMeasurement
    ) -> tuple[float | None, str | None, str | None, DataQuality, list[str]]:
        unit = raw.unit.strip() if raw.unit is not None else None
        v = raw.value
        if v is None:
            return None, unit, None, DataQuality.MISSING, ["VALUE_MISSING"]
        if isinstance(v, bool) or not isinstance(v, int | float):
            return None, unit, None, DataQuality.INVALID, ["VALUE_NOT_NUMERIC"]
        number = float(v)
        if not math.isfinite(number):
            return None, unit, None, DataQuality.INVALID, ["VALUE_NOT_FINITE"]
        spec = self._cfg.tag_specs.get(raw.tag_id)
        if spec is None:
            return number, unit, None, DataQuality.GOOD, []
        if unit is None or not unit:
            return None, None, None, DataQuality.INVALID, ["UNIT_MISSING"]
        try:
            converted, rule = convert_unit(number, unit, spec.canonical_unit)
        except UnitConversionError:
            return None, unit, None, DataQuality.INVALID, ["UNIT_MISMATCH"]
        if not math.isfinite(converted):
            return None, unit, None, DataQuality.INVALID, ["VALUE_NOT_FINITE"]
        return converted, canonical_unit_name(spec.canonical_unit), rule, DataQuality.GOOD, []

    def _register(self, key: SeriesKey, ts: datetime, value: float | None) -> str:
        series = self._series.get(key)
        if series is None:
            if len(self._series) >= self._cfg.max_series:
                return "limit"
            series = self._series[key] = _Series()
        if ts in series.seen:
            return "duplicate" if series.seen[ts] == value else "conflict"
        verdict = "ok"
        if series.high_water is not None and ts < series.high_water:
            verdict = "out_of_order"
        series.seen[ts] = value
        if len(series.seen) > self._cfg.dedupe_window:
            series.seen.popitem(last=False)
        if series.high_water is None or ts > series.high_water:
            series.high_water = ts
        return verdict


def _safe_repr(value: object) -> str:
    return repr(value)[:80]

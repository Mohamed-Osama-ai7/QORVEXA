from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta, timezone

import pytest

from qorvexa.connectors.telemetry import (
    DataQuality,
    Disposition,
    ProcessorConfig,
    RawMeasurement,
    TagSpec,
    TelemetryProcessor,
    TelemetrySource,
    UnitConversionError,
    convert_unit,
    map_source_quality,
    parse_timestamp,
    worst_quality,
)

NOW = datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC)


def make(**kw: object) -> RawMeasurement:
    base: dict[str, object] = {
        "factory_id": "factory-001",
        "equipment_id": "machine-001",
        "tag_id": "temperature",
        "value": 75.2,
        "unit": "C",
        "timestamp": "2026-11-23T12:00:00Z",
        "source_quality": "GOOD",
        "source": TelemetrySource.MQTT,
    }
    base.update(kw)
    return RawMeasurement(**base)  # type: ignore[arg-type]


def proc(**cfg: object) -> TelemetryProcessor:
    return TelemetryProcessor(ProcessorConfig(**cfg), clock=lambda: NOW)  # type: ignore[arg-type]


# ---- timestamps ------------------------------------------------------------------------------


def test_timestamp_forms_normalize_to_utc() -> None:
    assert parse_timestamp("2026-11-23T12:00:00Z").value == NOW
    assert parse_timestamp("2026-11-23T14:00:00+02:00").value == NOW
    assert (
        parse_timestamp(datetime(2026, 11, 23, 9, 0, tzinfo=timezone(timedelta(hours=-3)))).value
        == NOW
    )


def test_naive_and_numeric_timestamps_need_explicit_configuration() -> None:
    assert parse_timestamp("2026-11-23T12:00:00").reason == "TIMESTAMP_NAIVE"
    assert (
        parse_timestamp("2026-11-23T14:00:00", naive_tz=timezone(timedelta(hours=2))).value == NOW
    )
    assert parse_timestamp(1_795_435_200).reason == "TIMESTAMP_NUMERIC_UNIT_UNKNOWN"
    assert parse_timestamp(1_795_435_200, epoch_unit="s").value == datetime.fromtimestamp(
        1_795_435_200, UTC
    )
    assert parse_timestamp(1_795_435_200_000, epoch_unit="ms").value == datetime.fromtimestamp(
        1_795_435_200, UTC
    )


@pytest.mark.parametrize("bad", [None, "", "yesterday", True, [1], "9" * 100, float("nan")])
def test_unparseable_timestamps_are_reported(bad: object) -> None:
    assert parse_timestamp(bad, epoch_unit="s").value is None


# ---- units -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "src", "dst", "expected"),
    [
        (100.0, "degC", "degF", 212.0),
        (212.0, "F", "C", 100.0),
        (0.0, "K", "degC", -273.15),
        (25.0, "°C", "K", 298.15),
        (1.0, "bar", "kPa", 100.0),
        (14.503773773, "psi", "bar", 1.0),
        (2.1, "mm/s", "m/s", 0.0021),
        (1.0, "in/s", "mm/s", 25.4),
    ],
)
def test_known_conversions(value: float, src: str, dst: str, expected: float) -> None:
    out, rule = convert_unit(value, src, dst)
    assert out == pytest.approx(expected, rel=1e-6, abs=1e-9)
    assert rule is not None and "->" in rule


def test_identity_has_no_rule_and_mismatch_is_refused() -> None:
    assert convert_unit(5.0, "C", "degC") == (5.0, None)
    assert convert_unit(5.0, "rpm", "rpm") == (5.0, None)  # unknown but identical: no guess needed
    with pytest.raises(UnitConversionError):
        convert_unit(1.0, "bar", "degC")
    with pytest.raises(UnitConversionError):
        convert_unit(1.0, "rpm", "Hz")


# ---- source quality --------------------------------------------------------------------------


def test_opcua_status_code_severity_mapping() -> None:
    assert map_source_quality(0x00000000)[0] is DataQuality.GOOD
    assert map_source_quality(0x40000000)[0] is DataQuality.UNCERTAIN
    assert map_source_quality(0x80000000)[0] is DataQuality.BAD
    assert map_source_quality(0xC0000000)[0] is DataQuality.INVALID
    assert map_source_quality(-1)[0] is DataQuality.INVALID
    assert map_source_quality(2**32)[0] is DataQuality.INVALID


def test_text_quality_and_absence_is_not_good() -> None:
    assert map_source_quality("good") == (DataQuality.GOOD, None)
    assert map_source_quality("Bad")[0] is DataQuality.BAD
    assert map_source_quality("whatever")[0] is DataQuality.INVALID
    assert map_source_quality(None) == (DataQuality.UNCERTAIN, "SOURCE_QUALITY_ABSENT")
    assert map_source_quality(True)[0] is DataQuality.INVALID


def test_worst_quality_ordering() -> None:
    assert (
        worst_quality(DataQuality.GOOD, DataQuality.STALE, DataQuality.UNCERTAIN)
        is DataQuality.STALE
    )
    assert worst_quality(DataQuality.BAD, DataQuality.INVALID) is DataQuality.INVALID


# ---- processor -------------------------------------------------------------------------------


def test_good_measurement_roundtrip_and_provenance() -> None:
    res = proc().process(make())
    assert res.disposition is Disposition.ACCEPTED and res.record is not None
    r = res.record
    assert (r.value, r.unit, r.quality, r.timestamp) == (75.2, "C", DataQuality.GOOD, NOW)
    assert r.source is TelemetrySource.MQTT and r.raw_unit == "C" and r.conversion_rule is None
    assert r.synthetic is False and r.ingested_at == NOW


def test_unit_conversion_is_recorded_when_spec_present() -> None:
    p = proc(tag_specs={"temperature": TagSpec("degC")})
    r = p.process(make(value=212.0, unit="degF")).record
    assert r is not None
    assert r.value == pytest.approx(100.0) and r.unit == "degC" and r.raw_unit == "degF"
    assert r.conversion_rule is not None and "degF->degC" in r.conversion_rule


def test_unit_problems_become_invalid_without_a_value() -> None:
    p = proc(tag_specs={"temperature": TagSpec("degC")})
    mismatch = p.process(make(value=1.0, unit="bar", timestamp="2026-11-23T11:59:59Z")).record
    missing = p.process(make(value=1.0, unit=None, timestamp="2026-11-23T11:59:58Z")).record
    assert (
        mismatch is not None and mismatch.quality is DataQuality.INVALID and mismatch.value is None
    )
    assert "UNIT_MISMATCH" in mismatch.quality_reasons
    assert (
        missing is not None and "UNIT_MISSING" in missing.quality_reasons and missing.value is None
    )


@pytest.mark.parametrize(
    ("value", "quality", "reason"),
    [
        (None, DataQuality.MISSING, "VALUE_MISSING"),
        (float("nan"), DataQuality.INVALID, "VALUE_NOT_FINITE"),
        (float("inf"), DataQuality.INVALID, "VALUE_NOT_FINITE"),
        ("75.2", DataQuality.INVALID, "VALUE_NOT_NUMERIC"),
        (True, DataQuality.INVALID, "VALUE_NOT_NUMERIC"),
    ],
)
def test_bad_values_are_flagged_never_substituted(
    value: object, quality: DataQuality, reason: str
) -> None:
    r = proc().process(make(value=value)).record
    assert (
        r is not None and r.value is None and r.quality is quality and reason in r.quality_reasons
    )
    if isinstance(value, float) and math.isnan(value):
        assert r.raw_value_repr == "nan"


def test_stale_future_and_missing_timestamp() -> None:
    old = proc(max_age_s=60).process(make(timestamp="2026-11-23T11:55:00Z")).record
    assert (
        old is not None
        and old.quality is DataQuality.STALE
        and "AGE_EXCEEDS_LIMIT" in old.quality_reasons
    )
    assert old.value == 75.2  # stale data keeps its value, flagged

    backfill = proc(max_age_s=None).process(make(timestamp="2026-01-01T00:00:00Z")).record
    assert backfill is not None and backfill.quality is DataQuality.GOOD

    future = proc().process(make(timestamp="2026-11-23T12:10:00Z")).record
    assert (
        future is not None
        and future.timestamp is None
        and "TIMESTAMP_IN_FUTURE" in future.quality_reasons
    )

    skew_ok = proc().process(make(timestamp="2026-11-23T12:00:03Z")).record
    assert skew_ok is not None and skew_ok.quality is DataQuality.GOOD

    none_ts = proc().process(make(timestamp=None)).record
    assert (
        none_ts is not None and none_ts.quality is DataQuality.INVALID and none_ts.timestamp is None
    )


def test_source_quality_and_checks_combine_to_worst() -> None:
    r = proc().process(make(source_quality=0x80000000, timestamp="2026-11-23T11:55:00Z")).record
    assert r is not None and r.quality is DataQuality.BAD
    assert {"SOURCE_QUALITY_BAD", "AGE_EXCEEDS_LIMIT"} <= set(r.quality_reasons)


def test_duplicates_conflicts_and_out_of_order() -> None:
    p = proc()
    first = p.process(make(timestamp="2026-11-23T11:59:50Z"))
    again = p.process(make(timestamp="2026-11-23T11:59:50Z"))
    conflict = p.process(make(timestamp="2026-11-23T11:59:50Z", value=99.0))
    assert first.disposition is Disposition.ACCEPTED
    assert again.disposition is Disposition.DUPLICATE and again.record is None
    assert conflict.disposition is Disposition.REJECTED and conflict.reasons == (
        "CONFLICTING_DUPLICATE",
    )

    p.process(make(timestamp="2026-11-23T11:59:59Z"))
    late = p.process(make(timestamp="2026-11-23T11:59:55Z", value=70.0)).record
    assert late is not None and late.out_of_order and "OUT_OF_ORDER" in late.quality_reasons


def test_series_are_independent_per_factory_equipment_tag() -> None:
    p = proc()
    a = p.process(make())
    b = p.process(make(equipment_id="machine-002"))
    c = p.process(make(tag_id="vibration"))
    assert all(x.disposition is Disposition.ACCEPTED for x in (a, b, c))


def test_dedupe_memory_and_series_count_are_bounded() -> None:
    p = proc(dedupe_window=3, max_age_s=None)
    for i in range(10):
        p.process(make(timestamp=f"2026-11-23T11:00:{i:02d}Z"))
    # oldest timestamps fell out of the window: not remembered, accepted again as out-of-order
    again = p.process(make(timestamp="2026-11-23T11:00:00Z"))
    assert (
        again.disposition is Disposition.ACCEPTED
        and again.record is not None
        and again.record.out_of_order
    )

    q = proc(max_series=2)
    q.process(make(tag_id="t1"))
    q.process(make(tag_id="t2"))
    over = q.process(make(tag_id="t3"))
    assert over.disposition is Disposition.REJECTED and over.reasons == ("SERIES_LIMIT_REACHED",)


@pytest.mark.parametrize("field", ["factory_id", "equipment_id", "tag_id"])
@pytest.mark.parametrize("bad", ["", " x", "a/b", "x" * 200, "a b", "../etc"])
def test_unusable_identifiers_are_rejected(field: str, bad: str) -> None:
    res = proc().process(make(**{field: bad}))
    assert res.disposition is Disposition.REJECTED and res.record is None
    assert res.reasons == (f"INVALID_{field.upper()}",)


def test_simulated_source_is_always_marked_synthetic() -> None:
    r = proc().process(make(source=TelemetrySource.SIMULATED)).record
    assert r is not None and r.synthetic is True


def test_stale_series_report() -> None:
    p = proc(max_age_s=60)
    p.process(make(tag_id="fresh", timestamp="2026-11-23T11:59:30Z"))
    p.process(make(tag_id="old", timestamp="2026-11-23T11:55:00Z"))
    assert p.stale_series(NOW) == [("factory-001", "machine-001", "old")]
    assert p.stale_series(NOW + timedelta(minutes=5)) == [
        ("factory-001", "machine-001", "fresh"),
        ("factory-001", "machine-001", "old"),
    ]
    assert proc(max_age_s=None).stale_series(NOW) == []


def test_absent_quality_policy_is_configurable() -> None:
    r = proc().process(make(source_quality=None)).record
    assert r is not None and r.quality is DataQuality.UNCERTAIN
    r2 = proc(absent_quality=DataQuality.GOOD).process(make(source_quality=None)).record
    assert r2 is not None and r2.quality is DataQuality.GOOD

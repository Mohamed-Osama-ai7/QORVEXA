from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest

from qorvexa.connectors.simulated import (
    FaultProfile,
    SimulatedFactory,
    SimulatedMachine,
    mqtt_topic,
)
from qorvexa.connectors.simulated.factory import frame_to_measurements
from qorvexa.connectors.telemetry import (
    STATE_TAG,
    DataQuality,
    Disposition,
    MachineState,
    ProcessorConfig,
    RawMeasurement,
    StateCode,
    TagSpec,
    TelemetryProcessor,
    TelemetrySource,
    derive_machine_state,
)

START = datetime(2026, 11, 23, 8, 0, 0, tzinfo=UTC)


def run_through_processor(
    factory: SimulatedFactory, ticks: int
) -> list[tuple[Disposition, object]]:
    end = START + timedelta(seconds=10 * ticks)
    proc = TelemetryProcessor(
        ProcessorConfig(max_age_s=None, tag_specs={"temperature": TagSpec("degC")}),
        clock=lambda: end,
    )
    return [(r.disposition, r.record) for r in map(proc.process, factory.measurements(ticks))]


def test_same_seed_is_bit_identical_and_different_seed_differs() -> None:
    a = [f.payload for f in SimulatedFactory(seed=7).frames(200)]
    b = [f.payload for f in SimulatedFactory(seed=7).frames(200)]
    c = [f.payload for f in SimulatedFactory(seed=8).frames(200)]
    assert a == b
    assert a != c


def test_payload_follows_documented_schema_and_is_labelled_synthetic() -> None:
    frame = next(iter(SimulatedFactory(seed=1).frames(1)))
    p = frame.payload
    assert frame.topic == mqtt_topic("factory-sim-001", "machine-001")
    assert frame.topic == "qorvexa/v1/factory-sim-001/machine-001/telemetry"
    assert p["synthetic"] is True and p["quality"] == "GOOD"
    assert p["timestamp"] == "2026-11-23T08:00:00Z"
    assert set(p["measurements"]) == {"temperature", "vibration", "production_count", STATE_TAG}
    assert p["measurements"]["temperature"]["unit"] == "C"
    json.dumps(p)  # JSON-serializable as an MQTT payload
    for m in frame_to_measurements(frame):
        assert m.source is TelemetrySource.SIMULATED and m.synthetic


def test_clean_stream_is_all_good_and_physically_sane() -> None:
    results = run_through_processor(SimulatedFactory(seed=3), 300)
    assert all(d is Disposition.ACCEPTED for d, _ in results)
    records = [r for _, r in results if r is not None]
    assert {r.quality for r in records} == {DataQuality.GOOD}  # type: ignore[attr-defined]
    states = {int(r.value) for r in records if r.tag_id == STATE_TAG}  # type: ignore[attr-defined]
    assert states <= {int(s) for s in StateCode}
    for machine in ("machine-001", "machine-002"):
        counts = [
            r.value for r in records if r.tag_id == "production_count" and r.equipment_id == machine
        ]  # type: ignore[attr-defined]
        assert counts == sorted(counts)  # monotonic non-decreasing
        vib = [r.value for r in records if r.tag_id == "vibration" and r.equipment_id == machine]  # type: ignore[attr-defined]
        assert min(vib) >= 0.0


def test_failures_happen_and_machines_recover() -> None:
    fac = SimulatedFactory(seed=11, machines=[SimulatedMachine("m1", failure_probability=0.2)])
    codes = [
        int(m.value)  # type: ignore[arg-type]
        for m in fac.measurements(400)
        if m.tag_id == STATE_TAG
    ]
    assert int(StateCode.DOWN) in codes
    first_down = codes.index(int(StateCode.DOWN))
    assert int(StateCode.RUNNING) in codes[first_down:]


def test_machine_ids_must_be_unique_and_start_aware() -> None:
    with pytest.raises(ValueError):
        SimulatedFactory(seed=1, machines=[SimulatedMachine("a"), SimulatedMachine("a")])
    with pytest.raises(ValueError):
        SimulatedFactory(seed=1, start=datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        FaultProfile(gap_rate=1.5)


# ---- fault injection is observable downstream ------------------------------------------------


def test_gaps_reduce_frame_count_deterministically() -> None:
    clean = len(list(SimulatedFactory(seed=5).frames(500)))
    gappy = len(list(SimulatedFactory(seed=5, faults=FaultProfile(gap_rate=0.2)).frames(500)))
    assert gappy < clean
    assert gappy == len(
        list(SimulatedFactory(seed=5, faults=FaultProfile(gap_rate=0.2)).frames(500))
    )


def test_missing_malformed_and_bad_quality_are_flagged_by_the_processor() -> None:
    faults = FaultProfile(missing_value_rate=0.1, malformed_value_rate=0.1, bad_quality_rate=0.1)
    results = run_through_processor(SimulatedFactory(seed=9, faults=faults), 300)
    records = [r for _, r in results if r is not None]
    reasons = Counter(x for r in records for x in r.quality_reasons)  # type: ignore[attr-defined]
    assert reasons["VALUE_MISSING"] > 0
    assert reasons["VALUE_NOT_NUMERIC"] > 0
    assert reasons["SOURCE_QUALITY_BAD"] > 0
    for r in records:
        if r.quality in (DataQuality.MISSING, DataQuality.INVALID):  # type: ignore[attr-defined]
            assert r.value is None  # type: ignore[attr-defined]


def test_duplicates_and_out_of_order_are_caught() -> None:
    faults = FaultProfile(duplicate_rate=0.15, out_of_order_rate=0.15)
    results = run_through_processor(SimulatedFactory(seed=21, faults=faults), 400)
    dispositions = Counter(d for d, _ in results)
    assert dispositions[Disposition.DUPLICATE] > 0
    assert any(r is not None and r.out_of_order for _, r in results)  # type: ignore[attr-defined]
    assert dispositions[Disposition.REJECTED] == 0  # duplicates are identical, never conflicting


# ---- observed machine state ------------------------------------------------------------------


def _state_record(
    value: float | None, quality_flag: object = "GOOD", ts: str = "2026-11-23T08:00:00Z"
):  # type: ignore[no-untyped-def]
    proc = TelemetryProcessor(ProcessorConfig(max_age_s=60), clock=lambda: START)
    raw = RawMeasurement(
        factory_id="f1",
        equipment_id="m1",
        tag_id=STATE_TAG,
        value=value,
        timestamp=ts,
        source_quality=quality_flag,
        source=TelemetrySource.SIMULATED,
    )
    return proc.process(raw).record


@pytest.mark.parametrize("state", list(StateCode))
def test_state_codes_map_to_machine_states(state: StateCode) -> None:
    snap = derive_machine_state("f1", "m1", _state_record(float(state)))
    assert snap.state is MachineState[state.name] and snap.quality is DataQuality.GOOD
    assert snap.synthetic is True and snap.state_timestamp == START


def test_untrusted_state_data_is_unknown_never_down() -> None:
    cases = {
        "missing": derive_machine_state("f1", "m1", _state_record(None)),
        "bad quality": derive_machine_state("f1", "m1", _state_record(6.0, "BAD")),
        "stale": derive_machine_state("f1", "m1", _state_record(1.0, ts="2026-11-23T07:50:00Z")),
        "no observation": derive_machine_state("f1", "m1", None),
        "unknown code": derive_machine_state("f1", "m1", _state_record(42.0)),
        "fractional code": derive_machine_state("f1", "m1", _state_record(1.5)),
    }
    for name, snap in cases.items():
        assert snap.state is MachineState.UNKNOWN, name
        assert snap.state is not MachineState.DOWN, name
        assert snap.reasons, name


def test_uncertain_state_is_reported_with_its_quality() -> None:
    snap = derive_machine_state("f1", "m1", _state_record(1.0, "UNCERTAIN"))
    assert snap.state is MachineState.RUNNING and snap.quality is DataQuality.UNCERTAIN

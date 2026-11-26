"""Seeded simulated factory that emits MQTT-style frames and ``RawMeasurement`` objects.

Everything produced is labelled ``synthetic`` (payload field and ``TelemetrySource.SIMULATED``).
Fault injection is deterministic given the seed, so connector and normalization tests can assert
exact behaviour. No network or file I/O.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import numpy as np

from qorvexa.connectors.telemetry.machine_state import STATE_TAG, StateCode
from qorvexa.connectors.telemetry.models import RawMeasurement, TelemetrySource

TOPIC_PREFIX: Final = "qorvexa/v1"
UNITS: Final[dict[str, str]] = {
    "temperature": "C",
    "vibration": "mm/s",
    "production_count": "piece",
    STATE_TAG: "state_code",
}


def mqtt_topic(factory_id: str, equipment_id: str) -> str:
    """Topic convention: ``qorvexa/v1/<factory_id>/<equipment_id>/telemetry`` (subscribe-only)."""
    return f"{TOPIC_PREFIX}/{factory_id}/{equipment_id}/telemetry"


@dataclass(frozen=True)
class FaultProfile:
    """Per-frame fault probabilities in [0, 1]. All zero means a clean stream."""

    gap_rate: float = 0.0  # frame never emitted
    missing_value_rate: float = 0.0  # a tag value becomes null
    malformed_value_rate: float = 0.0  # a tag value becomes a non-numeric string
    bad_quality_rate: float = 0.0  # frame quality flag = BAD
    duplicate_rate: float = 0.0  # frame emitted twice
    out_of_order_rate: float = 0.0  # frame emitted one tick late

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be within [0, 1]")


@dataclass(frozen=True)
class SimulatedMachine:
    equipment_id: str
    base_temperature_c: float = 55.0
    running_temperature_rise_c: float = 18.0
    running_vibration_mm_s: float = 2.0
    idle_vibration_mm_s: float = 0.3
    failure_probability: float = 0.01
    repair_ticks: tuple[int, int] = (6, 18)


@dataclass(frozen=True)
class Frame:
    """One MQTT-style message: a topic and a JSON-serializable payload."""

    topic: str
    payload: dict[str, Any]


@dataclass
class _MachineRuntime:
    spec: SimulatedMachine
    state: StateCode = StateCode.RUNNING
    repair_left: int = 0
    temperature: float = 0.0
    produced: int = 0
    pending: list[Frame] = field(default_factory=list)


class SimulatedFactory:
    def __init__(
        self,
        *,
        seed: int,
        factory_id: str = "factory-sim-001",
        machines: Sequence[SimulatedMachine] | None = None,
        start: datetime = datetime(2026, 11, 23, 8, 0, 0, tzinfo=UTC),
        step_s: float = 10.0,
        faults: FaultProfile | None = None,
    ) -> None:
        if start.tzinfo is None or step_s <= 0:
            raise ValueError("start must be timezone-aware and step_s positive")
        self.factory_id = factory_id
        self._start = start.astimezone(UTC)
        self._step = timedelta(seconds=step_s)
        self._faults = faults or FaultProfile()
        self._rng = np.random.default_rng(seed)
        specs = machines or (SimulatedMachine("machine-001"), SimulatedMachine("machine-002"))
        ids = [m.equipment_id for m in specs]
        if len(set(ids)) != len(ids):
            raise ValueError("equipment ids must be unique")
        self._machines = [
            _MachineRuntime(m, temperature=m.base_temperature_c + m.running_temperature_rise_c)
            for m in specs
        ]
        self._tick = 0

    @property
    def machine_ids(self) -> tuple[str, ...]:
        return tuple(m.spec.equipment_id for m in self._machines)

    # -- generation --------------------------------------------------------------------------

    def frames(self, ticks: int) -> Iterator[Frame]:
        for _ in range(ticks):
            yield from self._next_tick()

    def measurements(self, ticks: int) -> Iterator[RawMeasurement]:
        for frame in self.frames(ticks):
            yield from frame_to_measurements(frame)

    def _next_tick(self) -> Iterator[Frame]:
        ts = self._start + self._tick * self._step
        self._tick += 1
        for m in self._machines:
            # a frame delayed from the previous tick is released now, after this tick's new frame
            late, m.pending = m.pending, []
            self._advance(m)
            frame = self._build_frame(m, ts)
            f = self._faults
            if self._rng.random() < f.gap_rate:
                yield from late
                continue
            frame = self._inject(frame)
            if self._rng.random() < f.out_of_order_rate:
                m.pending.append(frame)
                yield from late
                continue
            yield frame
            yield from late
            if self._rng.random() < f.duplicate_rate:
                yield frame

    def _advance(self, m: _MachineRuntime) -> None:
        r = self._rng
        if m.state is StateCode.DOWN:
            m.repair_left -= 1
            if m.repair_left <= 0:
                m.state = StateCode.RUNNING
        elif m.state is StateCode.RUNNING:
            if r.random() < m.spec.failure_probability:
                m.state = StateCode.DOWN
                lo, hi = m.spec.repair_ticks
                m.repair_left = int(r.integers(lo, hi + 1))
            elif r.random() < 0.02:
                m.state = StateCode.IDLE
        elif m.state is StateCode.IDLE and r.random() < 0.3:
            m.state = StateCode.RUNNING
        running = m.state is StateCode.RUNNING
        target = m.spec.base_temperature_c + (m.spec.running_temperature_rise_c if running else 0.0)
        m.temperature += 0.3 * (target - m.temperature) + float(r.normal(0.0, 0.2))
        if running:
            m.produced += 1

    def _build_frame(self, m: _MachineRuntime, ts: datetime) -> Frame:
        running = m.state is StateCode.RUNNING
        vib = m.spec.running_vibration_mm_s if running else m.spec.idle_vibration_mm_s
        vib = max(0.0, vib + float(self._rng.normal(0.0, 0.05)))
        measurements: dict[str, dict[str, Any]] = {
            "temperature": {"value": round(m.temperature, 3), "unit": UNITS["temperature"]},
            "vibration": {"value": round(vib, 4), "unit": UNITS["vibration"]},
            "production_count": {"value": m.produced, "unit": UNITS["production_count"]},
            STATE_TAG: {"value": int(m.state), "unit": UNITS[STATE_TAG]},
        }
        return Frame(
            mqtt_topic(self.factory_id, m.spec.equipment_id),
            {
                "factory_id": self.factory_id,
                "equipment_id": m.spec.equipment_id,
                "timestamp": ts.isoformat().replace("+00:00", "Z"),
                "measurements": measurements,
                "quality": "GOOD",
                "synthetic": True,
            },
        )

    def _inject(self, frame: Frame) -> Frame:
        f, r = self._faults, self._rng
        payload = {
            **frame.payload,
            "measurements": {k: dict(v) for k, v in frame.payload["measurements"].items()},
        }
        for tag in payload["measurements"]:
            if r.random() < f.missing_value_rate:
                payload["measurements"][tag]["value"] = None
            elif r.random() < f.malformed_value_rate:
                payload["measurements"][tag]["value"] = "N/A"
        if r.random() < f.bad_quality_rate:
            payload["quality"] = "BAD"
        return Frame(frame.topic, payload)


def frame_to_measurements(
    frame: Frame, *, source_id: str = "simulated-mqtt"
) -> Iterator[RawMeasurement]:
    """Flatten a frame the way an MQTT connector will: one ``RawMeasurement`` per tag."""
    p = frame.payload
    for tag, item in p["measurements"].items():
        yield RawMeasurement(
            factory_id=p["factory_id"],
            equipment_id=p["equipment_id"],
            tag_id=tag,
            value=item.get("value"),
            unit=item.get("unit"),
            timestamp=p["timestamp"],
            source_quality=p.get("quality"),
            source=TelemetrySource.SIMULATED,
            source_id=source_id,
            synthetic=True,
        )

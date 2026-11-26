"""Observed machine state derived from telemetry.

This is the *observed* state of real or simulated equipment as reported by a state tag. It is not
the Digital Twin's internal DES micro-state (``industrial/digital_twin``) and does not replace it.

Core rule: missing, stale, bad or unrecognized data yields ``UNKNOWN``. It never implies ``DOWN``.
"""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum

from pydantic import BaseModel, ConfigDict

from qorvexa.connectors.telemetry.models import DataQuality, TelemetryRecord, TelemetrySource


class MachineState(StrEnum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    STARVED = "STARVED"
    SETUP = "SETUP"
    MAINTENANCE = "MAINTENANCE"
    DOWN = "DOWN"
    UNKNOWN = "UNKNOWN"


class StateCode(IntEnum):
    """Numeric encoding of ``machine_state`` on the wire (PROPOSED convention, not project-wide)."""

    IDLE = 0
    RUNNING = 1
    BLOCKED = 2
    STARVED = 3
    SETUP = 4
    MAINTENANCE = 5
    DOWN = 6


STATE_TAG = "machine_state"


class MachineStateSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    factory_id: str
    equipment_id: str
    state: MachineState
    state_timestamp: datetime | None
    last_update: datetime | None
    source: TelemetrySource | None
    quality: DataQuality
    reasons: tuple[str, ...] = ()
    synthetic: bool = False


def derive_machine_state(
    factory_id: str, equipment_id: str, record: TelemetryRecord | None
) -> MachineStateSnapshot:
    if record is None:
        return MachineStateSnapshot(
            factory_id=factory_id,
            equipment_id=equipment_id,
            state=MachineState.UNKNOWN,
            state_timestamp=None,
            last_update=None,
            source=None,
            quality=DataQuality.MISSING,
            reasons=("NO_STATE_OBSERVATION",),
        )
    base = {
        "factory_id": factory_id,
        "equipment_id": equipment_id,
        "state_timestamp": record.timestamp,
        "last_update": record.ingested_at,
        "source": record.source,
        "quality": record.quality,
        "synthetic": record.synthetic,
    }
    if record.quality not in (DataQuality.GOOD, DataQuality.UNCERTAIN) or record.value is None:
        reasons = ("STATE_DATA_NOT_TRUSTED", *record.quality_reasons)
        return MachineStateSnapshot(**base, state=MachineState.UNKNOWN, reasons=reasons)
    code = record.value
    if code != int(code) or int(code) not in StateCode._value2member_map_:
        return MachineStateSnapshot(
            **base,
            state=MachineState.UNKNOWN,
            reasons=("STATE_CODE_UNRECOGNIZED",),
        )
    return MachineStateSnapshot(
        **base,
        state=MachineState[StateCode(int(code)).name],
        reasons=record.quality_reasons,
    )

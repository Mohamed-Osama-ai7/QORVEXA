"""Telemetry normalization for read-only industrial connectors (no network I/O)."""

from qorvexa.connectors.telemetry.machine_state import (
    STATE_TAG,
    MachineState,
    MachineStateSnapshot,
    StateCode,
    derive_machine_state,
)
from qorvexa.connectors.telemetry.models import (
    DataQuality,
    RawMeasurement,
    TelemetryRecord,
    TelemetrySource,
    worst_quality,
)
from qorvexa.connectors.telemetry.normalize import (
    UnitConversionError,
    convert_unit,
    map_source_quality,
    parse_timestamp,
)
from qorvexa.connectors.telemetry.processor import (
    Disposition,
    ProcessorConfig,
    ProcessResult,
    TagSpec,
    TelemetryProcessor,
)

__all__ = [
    "STATE_TAG",
    "DataQuality",
    "Disposition",
    "MachineState",
    "MachineStateSnapshot",
    "ProcessResult",
    "ProcessorConfig",
    "RawMeasurement",
    "StateCode",
    "TagSpec",
    "TelemetryProcessor",
    "TelemetryRecord",
    "TelemetrySource",
    "UnitConversionError",
    "convert_unit",
    "derive_machine_state",
    "map_source_quality",
    "parse_timestamp",
    "worst_quality",
]

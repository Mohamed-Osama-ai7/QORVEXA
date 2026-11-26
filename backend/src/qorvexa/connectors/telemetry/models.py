"""Internal telemetry representation shared by every read-only connector.

PROPOSED contract: ``DataQuality`` and ``TelemetrySource`` are not yet project-wide enums. They live
here until the Technical Lead confirms the shared convention (brief section 15).
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

IDENTIFIER_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class DataQuality(StrEnum):
    GOOD = "GOOD"
    UNCERTAIN = "UNCERTAIN"
    STALE = "STALE"
    BAD = "BAD"
    MISSING = "MISSING"
    INVALID = "INVALID"


# Worst wins when several checks disagree.
QUALITY_SEVERITY: Final[dict[DataQuality, int]] = {
    DataQuality.GOOD: 0,
    DataQuality.UNCERTAIN: 1,
    DataQuality.STALE: 2,
    DataQuality.BAD: 3,
    DataQuality.MISSING: 4,
    DataQuality.INVALID: 5,
}


def worst_quality(*qualities: DataQuality) -> DataQuality:
    return max(qualities, key=lambda q: QUALITY_SEVERITY[q])


class TelemetrySource(StrEnum):
    OPCUA = "opcua"
    MQTT = "mqtt"
    HISTORIAN = "historian"
    SIMULATED = "simulated"


class RawMeasurement(BaseModel):
    """What a connector hands over, before any trust is placed in it (types deliberately loose)."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    factory_id: str
    equipment_id: str
    tag_id: str
    value: object = None
    unit: str | None = None
    timestamp: object = None
    source_quality: object = None
    source: TelemetrySource
    source_id: str = Field(default="unspecified", max_length=128)
    synthetic: bool = False


class TelemetryRecord(BaseModel):
    """A normalized measurement.

    ``value`` is ``None`` whenever it is missing or invalid; nothing is ever substituted.
    ``timestamp`` is timezone-aware UTC, or ``None`` when it could not be parsed."""

    model_config = ConfigDict(frozen=True)

    factory_id: str
    equipment_id: str
    tag_id: str
    value: float | None
    unit: str | None
    timestamp: datetime | None
    ingested_at: datetime
    source: TelemetrySource
    source_id: str
    quality: DataQuality
    quality_reasons: tuple[str, ...] = ()
    raw_value_repr: str | None = None
    raw_unit: str | None = None
    conversion_rule: str | None = None
    source_quality_raw: str | None = None
    out_of_order: bool = False
    synthetic: bool = False

    @field_validator("timestamp", "ingested_at")
    @classmethod
    def _aware_utc(cls, v: datetime | None) -> datetime | None:
        if v is not None and (v.tzinfo is None or v.utcoffset() is None):
            raise ValueError("timestamps must be timezone-aware")
        return v

    @property
    def series_key(self) -> tuple[str, str, str]:
        return (self.factory_id, self.equipment_id, self.tag_id)

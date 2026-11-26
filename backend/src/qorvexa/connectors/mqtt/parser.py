"""Strict parsing of MQTT telemetry messages into ``RawMeasurement`` objects.

Topic convention (PROPOSED): ``qorvexa/v1/<factory_id>/<equipment_id>/telemetry``.
The factory/equipment IDs in the payload must equal those in the topic, so a publisher cannot
write into another equipment's series by lying in the body.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Final

from qorvexa.connectors.telemetry.models import IDENTIFIER_PATTERN, RawMeasurement, TelemetrySource

MAX_PAYLOAD_BYTES: Final = 64 * 1024
MAX_TAGS_PER_MESSAGE: Final = 200
_TOPIC: Final = re.compile(r"^qorvexa/v1/([^/+#$]+)/([^/+#$]+)/telemetry$")
_FILTER: Final = re.compile(r"^qorvexa/v1/([^/+#$]+)/(\+|[^/+#$]+)/telemetry$")


@dataclass(frozen=True)
class ParseResult:
    measurements: tuple[RawMeasurement, ...] = ()
    error: str | None = None


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-standard JSON constant {name}")


def parse_message(
    topic: str,
    payload: bytes,
    *,
    source: TelemetrySource = TelemetrySource.MQTT,
    source_id: str = "mqtt",
) -> ParseResult:
    m = _TOPIC.fullmatch(topic)
    if m is None:
        return ParseResult(error="TOPIC_NOT_ALLOWED")
    factory_id, equipment_id = m.group(1), m.group(2)
    if not (
        IDENTIFIER_PATTERN.fullmatch(factory_id) and IDENTIFIER_PATTERN.fullmatch(equipment_id)
    ):
        return ParseResult(error="TOPIC_IDENTIFIER_INVALID")
    if len(payload) > MAX_PAYLOAD_BYTES:
        return ParseResult(error="PAYLOAD_TOO_LARGE")
    try:
        body = json.loads(payload.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return ParseResult(error="PAYLOAD_NOT_JSON")
    if not isinstance(body, dict):
        return ParseResult(error="PAYLOAD_NOT_OBJECT")
    if body.get("factory_id") != factory_id or body.get("equipment_id") != equipment_id:
        return ParseResult(error="TOPIC_PAYLOAD_MISMATCH")
    measurements = body.get("measurements")
    if not isinstance(measurements, dict) or not measurements:
        return ParseResult(error="MEASUREMENTS_INVALID")
    if len(measurements) > MAX_TAGS_PER_MESSAGE:
        return ParseResult(error="TOO_MANY_TAGS")
    if "timestamp" not in body:
        return ParseResult(error="TIMESTAMP_MISSING")
    quality = body.get("quality")
    synthetic = body.get("synthetic") is True or source is TelemetrySource.SIMULATED
    out: list[RawMeasurement] = []
    for tag, item in measurements.items():
        if not isinstance(tag, str) or not IDENTIFIER_PATTERN.fullmatch(tag):
            return ParseResult(error="TAG_ID_INVALID")
        if not isinstance(item, dict):
            return ParseResult(error="MEASUREMENT_ITEM_INVALID")
        unit = item.get("unit")
        if unit is not None and not isinstance(unit, str):
            return ParseResult(error="UNIT_INVALID")
        out.append(
            RawMeasurement(
                factory_id=factory_id,
                equipment_id=equipment_id,
                tag_id=tag,
                value=item.get("value"),
                unit=unit,
                timestamp=body["timestamp"],
                source_quality=quality,
                source=source,
                source_id=source_id,
                synthetic=synthetic,
            )
        )
    return ParseResult(measurements=tuple(out))


def validate_subscription(topic_filter: str, allowed_factories: frozenset[str]) -> str | None:
    """Return a reason if the filter is not approved, else ``None``.

    Only ``qorvexa/v1/<allowed factory>/(+|<equipment>)/telemetry`` is accepted: no ``#``, no
    ``$SYS``, no wildcard on the factory level."""
    m = _FILTER.fullmatch(topic_filter)
    if m is None:
        return "SUBSCRIPTION_PATTERN_NOT_ALLOWED"
    if m.group(1) not in allowed_factories:
        return "SUBSCRIPTION_FACTORY_NOT_ALLOWED"
    return None

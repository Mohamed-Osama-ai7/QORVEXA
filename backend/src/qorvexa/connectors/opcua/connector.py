"""Read-only OPC UA ingestion connector over ``OpcUaTransport``.

Only *approved* nodes are ever read or subscribed to; each maps to a factory/equipment/tag. Reads
and subscription changes become ``RawMeasurement``s for the shared ``TelemetryProcessor``. Reconnect
uses bounded backoff and a subscription is re-created after every reconnect. ``step()`` is
non-blocking and runs under an injected clock.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from random import Random
from typing import Literal

from qorvexa.connectors.connector_health import BackoffPolicy, ConnectorHealth, ConnectorState
from qorvexa.connectors.opcua.status import error_category, is_good, severity
from qorvexa.connectors.opcua.transport import NodeReading, OpcUaTransport, OpcUaTransportError
from qorvexa.connectors.readonly import ensure_read_only
from qorvexa.connectors.telemetry.models import (
    IDENTIFIER_PATTERN,
    RawMeasurement,
    TelemetryRecord,
    TelemetrySource,
)
from qorvexa.connectors.telemetry.processor import Disposition, TelemetryProcessor
from qorvexa.observability.metrics import DEFAULT_REGISTRY, MetricsRegistry

log = logging.getLogger("qorvexa.connectors.opcua")
Mode = Literal["poll", "subscribe"]
MAX_APPROVED_NODES = 2000
MAX_DISCOVERY_NODES = 2000


@dataclass(frozen=True)
class ApprovedNode:
    node_id: str
    factory_id: str
    equipment_id: str
    tag_id: str
    unit: str | None = None

    def __post_init__(self) -> None:
        if not self.node_id or len(self.node_id) > 256 or any(ord(c) < 32 for c in self.node_id):
            raise ValueError("invalid node_id")
        for v in (self.factory_id, self.equipment_id, self.tag_id):
            if not IDENTIFIER_PATTERN.fullmatch(v):
                raise ValueError(f"invalid identifier: {v!r}")


@dataclass(frozen=True)
class DiscoveryReport:
    namespaces: tuple[str, ...]
    approved_found: tuple[str, ...]
    approved_missing: tuple[str, ...]
    unapproved_seen: int  # counted only; their identities are never exposed
    truncated: bool


@dataclass(frozen=True)
class OpcUaStepSummary:
    readings: int = 0
    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    node_errors: int = 0
    connected: bool = False


class OpcUaReadConnector:
    def __init__(
        self,
        *,
        connector_id: str,
        transport: OpcUaTransport,
        nodes: Sequence[ApprovedNode],
        processor: TelemetryProcessor,
        sink: Callable[[TelemetryRecord], None],
        mode: Mode = "poll",
        subscription_interval_ms: int = 1000,
        source: TelemetrySource = TelemetrySource.OPCUA,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        backoff: BackoffPolicy | None = None,
        rng: Random | None = None,
        registry: MetricsRegistry = DEFAULT_REGISTRY,
        max_batch: int = 500,
    ) -> None:
        ensure_read_only(transport)
        if not nodes:
            raise ValueError("at least one approved node is required")
        if len(nodes) > MAX_APPROVED_NODES:
            raise ValueError("too many approved nodes")
        by_id = {n.node_id: n for n in nodes}
        if len(by_id) != len(nodes):
            raise ValueError("duplicate approved node_id")
        if len({(n.factory_id, n.equipment_id, n.tag_id) for n in nodes}) != len(nodes):
            raise ValueError("two nodes map to the same factory/equipment/tag")
        if not 100 <= subscription_interval_ms <= 3_600_000:
            raise ValueError("subscription_interval_ms out of range")
        self._transport = transport
        self._nodes = by_id
        self._ids = tuple(by_id)
        self._processor = processor
        self._sink = sink
        self._mode = mode
        self._interval = subscription_interval_ms
        self._source = source
        self._clock = clock
        self._backoff = backoff or BackoffPolicy()
        self._rng = rng or Random(0)  # noqa: S311 - jitter only
        self._max_batch = max_batch
        self._id = connector_id
        self._health = ConnectorHealth(
            connector_id, "opcua", synthetic=source is TelemetrySource.SIMULATED
        )
        self._msgs = registry.counter(
            "qorvexa_connector_messages_total",
            "Messages handled by industrial connectors, by outcome.",
            ["connector", "outcome"],
        )
        self._fail = registry.counter(
            "qorvexa_connector_connection_failures_total",
            "Connection failures of industrial connectors.",
            ["connector"],
        )
        self._up = registry.gauge(
            "qorvexa_connector_up", "1 when the connector is connected.", ["connector"]
        )
        self._node_err = registry.counter(
            "qorvexa_opcua_node_errors_total",
            "OPC UA per-node read errors by category.",
            ["connector", "category"],
        )
        self._up.set(0, connector=self._id)

    @property
    def health(self) -> ConnectorHealth:
        return self._health

    def reset(self) -> None:
        self._health = self._health.evolve(
            state=ConnectorState.DISCONNECTED, consecutive_failures=0, next_attempt_at=None
        )

    def close(self) -> None:
        try:
            if self._mode == "subscribe" and self._health.state is ConnectorState.CONNECTED:
                self._transport.delete_subscription()
        except OpcUaTransportError:
            pass
        finally:
            try:
                self._transport.close()
            finally:
                self._health = self._health.evolve(
                    state=ConnectorState.DISCONNECTED, next_attempt_at=None
                )
                self._up.set(0, connector=self._id)

    # -- discovery (approved nodes only) -----------------------------------------------------

    def discover(self, root: str = "i=85", max_depth: int = 6) -> DiscoveryReport:
        """Browse from ``root`` and report which approved nodes exist. Requires a connection."""
        if self._health.state is not ConnectorState.CONNECTED:
            raise RuntimeError("connect (step) before discover")
        found: set[str] = set()
        unapproved = 0
        visited = 0
        truncated = False
        queue: deque[tuple[str, int]] = deque([(root, 0)])
        while queue:
            node, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for entry in self._transport.browse(node):
                visited += 1
                if visited > MAX_DISCOVERY_NODES:
                    truncated = True
                    queue.clear()
                    break
                if entry.node_id in self._nodes:
                    found.add(entry.node_id)
                elif entry.is_variable:
                    unapproved += 1
                if not entry.is_variable:
                    queue.append((entry.node_id, depth + 1))
        return DiscoveryReport(
            namespaces=tuple(self._transport.namespaces()),
            approved_found=tuple(sorted(found)),
            approved_missing=tuple(sorted(set(self._nodes) - found)),
            unapproved_seen=unapproved,
            truncated=truncated,
        )

    # -- main loop body ----------------------------------------------------------------------

    def step(self) -> OpcUaStepSummary:
        now = self._clock()
        h = self._health
        if h.state is ConnectorState.FAILED:
            return OpcUaStepSummary()
        if h.state is not ConnectorState.CONNECTED:
            if h.next_attempt_at is not None and now < h.next_attempt_at:
                return OpcUaStepSummary()
            if not self._try_connect(now):
                return OpcUaStepSummary()
        try:
            if self._mode == "poll":
                readings = self._transport.read(self._ids)
            else:
                readings = self._transport.poll_changes(self._max_batch)
        except OpcUaTransportError as exc:
            self._on_failure(now, exc.category)
            return OpcUaStepSummary()
        return self._handle(readings, now)

    def _try_connect(self, now: datetime) -> bool:
        try:
            self._transport.connect()
            if self._mode == "subscribe":
                self._transport.create_subscription(self._ids, self._interval)
        except OpcUaTransportError as exc:
            self._on_failure(now, exc.category)
            return False
        self._health = self._health.evolve(
            state=ConnectorState.CONNECTED,
            last_connected_at=now,
            consecutive_failures=0,
            next_attempt_at=None,
            total_connections=self._health.total_connections + 1,
        )
        self._up.set(1, connector=self._id)
        log.info(
            "connector connected",
            extra={"component": "opcua", "event_type": "connect", "connector_id": self._id},
        )
        return True

    def _on_failure(self, now: datetime, category: str) -> None:
        h = self._health
        failures = h.consecutive_failures + 1
        self._fail.inc(connector=self._id)
        self._up.set(0, connector=self._id)
        extra = {"component": "opcua", "connector_id": self._id, "error_category": category}
        if failures >= self._backoff.max_attempts:
            self._health = h.evolve(
                state=ConnectorState.FAILED,
                consecutive_failures=failures,
                last_error_category=category,
                next_attempt_at=None,
            )
            log.error(
                "reconnect limit reached; connector stopped",
                extra={**extra, "event_type": "reconnect_limit"},
            )
            return
        delay = self._backoff.delay(failures, self._rng)
        self._health = h.evolve(
            state=ConnectorState.DISCONNECTED,
            consecutive_failures=failures,
            last_error_category=category,
            next_attempt_at=now + timedelta(seconds=delay),
        )
        log.warning(
            "connector disconnected; will retry", extra={**extra, "event_type": "disconnect"}
        )

    def _to_raw(self, reading: NodeReading, node: ApprovedNode) -> RawMeasurement:
        status = reading.status_code
        ts = reading.source_timestamp
        quality: object = status
        if ts is None and reading.server_timestamp is not None:
            # a server timestamp is not the time of the measurement: use it, but never as GOOD
            ts = reading.server_timestamp
            quality = 0x40000000 if is_good(status) else status
        value = reading.value if severity(status) < 2 else None
        return RawMeasurement(
            factory_id=node.factory_id,
            equipment_id=node.equipment_id,
            tag_id=node.tag_id,
            value=value,
            unit=node.unit,
            timestamp=ts,
            source_quality=quality,
            source=self._source,
            source_id=self._id,
            synthetic=self._source is TelemetrySource.SIMULATED,
        )

    def _handle(self, readings: Sequence[NodeReading], now: datetime) -> OpcUaStepSummary:
        accepted = duplicates = rejected = node_errors = unexpected = 0
        for reading in readings:
            node = self._nodes.get(reading.node_id)
            if node is None:  # a server returning something we did not ask for is dropped
                unexpected += 1
                self._msgs.inc(connector=self._id, outcome="unapproved_node")
                continue
            category = error_category(reading.status_code)
            if category is not None:
                node_errors += 1
                self._node_err.inc(connector=self._id, category=category.value)
            result = self._processor.process(self._to_raw(reading, node))
            if result.disposition is Disposition.ACCEPTED and result.record is not None:
                accepted += 1
                self._sink(result.record)
                self._msgs.inc(connector=self._id, outcome="accepted")
            elif result.disposition is Disposition.DUPLICATE:
                duplicates += 1
                self._msgs.inc(connector=self._id, outcome="duplicate")
            else:
                rejected += 1
                self._msgs.inc(connector=self._id, outcome="rejected")
        h = self._health
        total = len(readings)
        self._health = h.evolve(
            last_message_at=now if total else h.last_message_at,
            messages_received=h.messages_received + total,
            records_accepted=h.records_accepted + accepted,
            duplicates=h.duplicates + duplicates,
            records_rejected=h.records_rejected + rejected + unexpected,
            extra={**h.extra, "node_errors": h.extra.get("node_errors", 0) + node_errors},
        )
        return OpcUaStepSummary(total, accepted, duplicates, rejected, node_errors, connected=True)

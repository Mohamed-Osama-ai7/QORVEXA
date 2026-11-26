"""Subscribe-only MQTT ingestion connector.

``step()`` is non-blocking and deterministic under an injected clock: it (re)connects when allowed,
polls the transport, parses, normalizes, delivers records to a sink and updates health + metrics.
Reconnects use bounded exponential backoff; after ``max_attempts`` the connector is ``FAILED`` and
stays down until ``reset()`` is called (it never hammers an endpoint forever).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from random import Random

from qorvexa.connectors.connector_health import BackoffPolicy, ConnectorHealth, ConnectorState
from qorvexa.connectors.mqtt.parser import parse_message, validate_subscription
from qorvexa.connectors.mqtt.transport import InboundMessage, MqttTransport, TransportError
from qorvexa.connectors.readonly import ensure_read_only
from qorvexa.connectors.telemetry.models import TelemetryRecord, TelemetrySource
from qorvexa.connectors.telemetry.processor import Disposition, TelemetryProcessor
from qorvexa.observability.metrics import DEFAULT_REGISTRY, MetricsRegistry

log = logging.getLogger("qorvexa.connectors.mqtt")


@dataclass(frozen=True)
class StepSummary:
    messages: int = 0
    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    parse_errors: int = 0
    connected: bool = False


class MqttIngestConnector:
    def __init__(
        self,
        *,
        connector_id: str,
        transport: MqttTransport,
        subscriptions: Collection[str],
        allowed_factories: Collection[str],
        processor: TelemetryProcessor,
        sink: Callable[[TelemetryRecord], None],
        source: TelemetrySource = TelemetrySource.MQTT,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        backoff: BackoffPolicy | None = None,
        rng: Random | None = None,
        registry: MetricsRegistry = DEFAULT_REGISTRY,
        qos: int = 1,
        max_batch: int = 500,
    ) -> None:
        ensure_read_only(transport)
        allowed = frozenset(allowed_factories)
        if not subscriptions:
            raise ValueError("at least one subscription is required")
        for f in subscriptions:
            reason = validate_subscription(f, allowed)
            if reason is not None:
                raise ValueError(f"{reason}: {f!r}")
        if qos not in (0, 1):
            raise ValueError("qos must be 0 or 1")
        self._transport = transport
        self._subs = tuple(subscriptions)
        self._processor = processor
        self._sink = sink
        self._source = source
        self._clock = clock
        self._backoff = backoff or BackoffPolicy()
        self._rng = rng or Random(0)  # noqa: S311 - jitter only, not security
        self._qos = qos
        self._max_batch = max_batch
        self._id = connector_id
        self._health = ConnectorHealth(
            connector_id, "mqtt", synthetic=source is TelemetrySource.SIMULATED
        )
        self._msgs = registry.counter(
            "qorvexa_connector_messages_total",
            "Messages handled by industrial connectors, by outcome.",
            ["connector", "outcome"],
        )
        self._conn_failures = registry.counter(
            "qorvexa_connector_connection_failures_total",
            "Connection failures of industrial connectors.",
            ["connector"],
        )
        self._up = registry.gauge(
            "qorvexa_connector_up", "1 when the connector is connected.", ["connector"]
        )
        self._up.set(0, connector=self._id)

    @property
    def health(self) -> ConnectorHealth:
        return self._health

    def reset(self) -> None:
        """Operator action: leave FAILED and allow connection attempts again."""
        self._health = self._health.evolve(
            state=ConnectorState.DISCONNECTED, consecutive_failures=0, next_attempt_at=None
        )

    def close(self) -> None:
        try:
            self._transport.close()
        finally:
            self._health = self._health.evolve(
                state=ConnectorState.DISCONNECTED, next_attempt_at=None
            )
            self._up.set(0, connector=self._id)

    # -- main loop body ----------------------------------------------------------------------

    def step(self) -> StepSummary:
        now = self._clock()
        h = self._health
        if h.state is ConnectorState.FAILED:
            return StepSummary()
        if h.state is not ConnectorState.CONNECTED:
            if h.next_attempt_at is not None and now < h.next_attempt_at:
                return StepSummary()
            if not self._try_connect(now):
                return StepSummary()
        try:
            batch = self._transport.poll(self._max_batch)
        except TransportError as exc:
            self._on_failure(now, exc.category)
            return StepSummary()
        return self._handle(batch, now)

    def _try_connect(self, now: datetime) -> bool:
        try:
            self._transport.connect()
            for f in self._subs:
                self._transport.subscribe(f, self._qos)
        except TransportError as exc:
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
            extra={"component": "mqtt", "event_type": "connect", "connector_id": self._id},
        )
        return True

    def _on_failure(self, now: datetime, category: str) -> None:
        h = self._health
        failures = h.consecutive_failures + 1
        self._conn_failures.inc(connector=self._id)
        self._up.set(0, connector=self._id)
        if failures >= self._backoff.max_attempts:
            self._health = h.evolve(
                state=ConnectorState.FAILED,
                consecutive_failures=failures,
                last_error_category=category,
                next_attempt_at=None,
            )
            log.error(
                "reconnect limit reached; connector stopped",
                extra={
                    "component": "mqtt",
                    "event_type": "reconnect_limit",
                    "connector_id": self._id,
                    "error_category": category,
                },
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
            "connector disconnected; will retry",
            extra={
                "component": "mqtt",
                "event_type": "disconnect",
                "connector_id": self._id,
                "error_category": category,
            },
        )

    def _handle(self, batch: Sequence[InboundMessage], now: datetime) -> StepSummary:
        messages = accepted = duplicates = rejected = parse_errors = 0
        for msg in batch:
            messages += 1
            parsed = parse_message(msg.topic, msg.payload, source=self._source, source_id=self._id)
            if parsed.error is not None:
                parse_errors += 1
                self._msgs.inc(connector=self._id, outcome="parse_error")
                log.warning(
                    "message rejected",
                    extra={
                        "component": "mqtt",
                        "event_type": "parse_error",
                        "connector_id": self._id,
                        "error_category": parsed.error,
                    },
                )
                continue
            for raw in parsed.measurements:
                result = self._processor.process(raw)
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
        self._health = h.evolve(
            last_message_at=now if messages else h.last_message_at,
            messages_received=h.messages_received + messages,
            records_accepted=h.records_accepted + accepted,
            duplicates=h.duplicates + duplicates,
            records_rejected=h.records_rejected + rejected,
            parse_errors=h.parse_errors + parse_errors,
        )
        return StepSummary(messages, accepted, duplicates, rejected, parse_errors, connected=True)

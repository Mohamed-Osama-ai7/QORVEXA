# ruff: noqa: S311  # seeded Random is used only for deterministic backoff jitter in tests
from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from random import Random

import pytest

from qorvexa.connectors.connector_health import BackoffPolicy, ConnectorState
from qorvexa.connectors.mqtt import (
    InboundMessage,
    MqttIngestConnector,
    parse_message,
    validate_subscription,
)
from qorvexa.connectors.readonly import ReadOnlyViolationError, ensure_read_only
from qorvexa.connectors.simulated import FaultProfile, SimulatedFactory, SimulatedMqttTransport
from qorvexa.connectors.telemetry import (
    DataQuality,
    ProcessorConfig,
    TelemetryProcessor,
    TelemetryRecord,
    TelemetrySource,
)
from qorvexa.observability import MetricsRegistry
from qorvexa.observability.health import HealthState

T0 = datetime(2026, 11, 23, 8, 0, 0, tzinfo=UTC)
FILTER = "qorvexa/v1/factory-sim-001/+/telemetry"
GOOD_TOPIC = "qorvexa/v1/f1/m1/telemetry"


def payload(**over: object) -> bytes:
    body: dict[str, object] = {
        "factory_id": "f1",
        "equipment_id": "m1",
        "timestamp": "2026-11-23T12:00:00Z",
        "measurements": {"temperature": {"value": 75.2, "unit": "C"}},
        "quality": "GOOD",
    }
    body.update(over)
    return json.dumps(body).encode()


# ---- parser ----------------------------------------------------------------------------------


def test_parse_valid_message() -> None:
    res = parse_message(GOOD_TOPIC, payload())
    assert res.error is None and len(res.measurements) == 1
    m = res.measurements[0]
    assert (m.factory_id, m.equipment_id, m.tag_id, m.value, m.unit) == (
        "f1",
        "m1",
        "temperature",
        75.2,
        "C",
    )
    assert m.source is TelemetrySource.MQTT and m.synthetic is False


@pytest.mark.parametrize(
    ("topic", "body", "error"),
    [
        ("qorvexa/v1/f1/m1/control", payload(), "TOPIC_NOT_ALLOWED"),
        ("other/f1/m1/telemetry", payload(), "TOPIC_NOT_ALLOWED"),
        ("qorvexa/v1/f1/m1/telemetry/extra", payload(), "TOPIC_NOT_ALLOWED"),
        (GOOD_TOPIC, b"\xff\xfe", "PAYLOAD_NOT_JSON"),
        (GOOD_TOPIC, b"{bad", "PAYLOAD_NOT_JSON"),
        (GOOD_TOPIC, b'{"a": NaN}', "PAYLOAD_NOT_JSON"),
        (GOOD_TOPIC, b"[1,2]", "PAYLOAD_NOT_OBJECT"),
        (GOOD_TOPIC, payload(factory_id="other"), "TOPIC_PAYLOAD_MISMATCH"),
        (GOOD_TOPIC, payload(equipment_id="m2"), "TOPIC_PAYLOAD_MISMATCH"),
        (GOOD_TOPIC, payload(measurements={}), "MEASUREMENTS_INVALID"),
        (GOOD_TOPIC, payload(measurements=[1]), "MEASUREMENTS_INVALID"),
        (GOOD_TOPIC, payload(measurements={"t": 5}), "MEASUREMENT_ITEM_INVALID"),
        (GOOD_TOPIC, payload(measurements={"bad tag": {"value": 1}}), "TAG_ID_INVALID"),
        (GOOD_TOPIC, payload(measurements={"t": {"value": 1, "unit": 5}}), "UNIT_INVALID"),
        (GOOD_TOPIC, b"x" * (64 * 1024 + 1), "PAYLOAD_TOO_LARGE"),
        (
            GOOD_TOPIC,
            payload(measurements={f"t{i}": {"value": 1} for i in range(201)}),
            "TOO_MANY_TAGS",
        ),
    ],
)
def test_malformed_messages_are_rejected_with_a_category(
    topic: str, body: bytes, error: str
) -> None:
    assert parse_message(topic, body).error == error


def test_missing_timestamp_is_rejected_but_unparseable_is_left_to_the_processor() -> None:
    body = json.loads(payload())
    del body["timestamp"]
    assert parse_message(GOOD_TOPIC, json.dumps(body).encode()).error == "TIMESTAMP_MISSING"
    assert parse_message(GOOD_TOPIC, payload(timestamp="soon")).error is None


@pytest.mark.parametrize(
    ("flt", "reason"),
    [
        (FILTER, None),
        ("qorvexa/v1/factory-sim-001/machine-001/telemetry", None),
        ("#", "SUBSCRIPTION_PATTERN_NOT_ALLOWED"),
        ("$SYS/#", "SUBSCRIPTION_PATTERN_NOT_ALLOWED"),
        ("qorvexa/v1/+/+/telemetry", "SUBSCRIPTION_PATTERN_NOT_ALLOWED"),
        ("qorvexa/v1/factory-sim-001/#", "SUBSCRIPTION_PATTERN_NOT_ALLOWED"),
        ("qorvexa/v1/factory-sim-001/+/control", "SUBSCRIPTION_PATTERN_NOT_ALLOWED"),
        ("qorvexa/v1/other-factory/+/telemetry", "SUBSCRIPTION_FACTORY_NOT_ALLOWED"),
    ],
)
def test_subscription_allowlist(flt: str, reason: str | None) -> None:
    assert validate_subscription(flt, frozenset({"factory-sim-001"})) == reason


# ---- read-only guard -------------------------------------------------------------------------


class _Writable:
    def connect(self) -> None: ...
    def subscribe(self, topic_filter: str, qos: int) -> None: ...
    def poll(self, max_messages: int) -> Sequence[InboundMessage]:
        return []

    def close(self) -> None: ...
    def publish(self, topic: str, payload: bytes) -> None: ...


def test_read_only_guard_rejects_clients_with_write_methods() -> None:
    ensure_read_only(SimulatedMqttTransport(SimulatedFactory(seed=1)))
    with pytest.raises(ReadOnlyViolationError, match="publish"):
        ensure_read_only(_Writable())
    with pytest.raises(ReadOnlyViolationError):
        build(_Writable())  # type: ignore[arg-type]


# ---- connector -------------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def build(
    transport: SimulatedMqttTransport,
    clock: Clock | None = None,
    backoff: BackoffPolicy | None = None,
    sink: list[TelemetryRecord] | None = None,
    registry: MetricsRegistry | None = None,
    subs: Sequence[str] = (FILTER,),
) -> MqttIngestConnector:
    clk = clock or Clock()
    out = sink if sink is not None else []
    return MqttIngestConnector(
        connector_id="mqtt-sim",
        transport=transport,
        subscriptions=subs,
        allowed_factories=["factory-sim-001"],
        processor=TelemetryProcessor(ProcessorConfig(max_age_s=None), clock=clk),
        sink=out.append,
        source=TelemetrySource.SIMULATED,
        clock=clk,
        backoff=backoff or BackoffPolicy(base_s=2, max_s=30, max_attempts=4, jitter=0.0),
        rng=Random(1),
        registry=registry or MetricsRegistry(),
    )


def test_constructor_refuses_unapproved_subscriptions() -> None:
    with pytest.raises(ValueError, match="SUBSCRIPTION_PATTERN_NOT_ALLOWED"):
        build(SimulatedMqttTransport(SimulatedFactory(seed=1)), subs=["#"])


def test_happy_path_delivers_records_and_updates_health() -> None:
    sink: list[TelemetryRecord] = []
    t = SimulatedMqttTransport(SimulatedFactory(seed=1, start=T0))
    c = build(t, sink=sink)
    assert c.health.state is ConnectorState.INIT
    s = c.step()
    assert s.connected and s.messages == 2 and s.accepted == 8  # 2 machines x 4 tags
    assert t.subscribed == [(FILTER, 1)]
    assert c.health.state is ConnectorState.CONNECTED and c.health.synthetic
    assert {r.quality for r in sink} == {DataQuality.GOOD} and all(r.synthetic for r in sink)
    assert c.health.records_accepted == 8 and c.health.messages_received == 2


def test_only_subscribed_equipment_is_delivered() -> None:
    sink: list[TelemetryRecord] = []
    t = SimulatedMqttTransport(SimulatedFactory(seed=1, start=T0))
    build(t, sink=sink, subs=["qorvexa/v1/factory-sim-001/machine-002/telemetry"]).step()
    assert {r.equipment_id for r in sink} == {"machine-002"}


def test_malformed_traffic_is_counted_and_never_reaches_the_sink() -> None:
    sink: list[TelemetryRecord] = []
    reg = MetricsRegistry()
    t = SimulatedMqttTransport(SimulatedFactory(seed=1, start=T0, machines=None))
    t.inject_raw("qorvexa/v1/factory-sim-001/machine-001/telemetry", b"{broken")
    c = build(t, sink=sink, registry=reg)
    s = c.step()
    assert s.parse_errors == 1 and c.health.parse_errors == 1
    assert (
        'qorvexa_connector_messages_total{connector="mqtt-sim",outcome="parse_error"} 1'
        in reg.render()
    )


def test_duplicates_and_conflicts_are_not_double_counted() -> None:
    sink: list[TelemetryRecord] = []
    t = SimulatedMqttTransport(
        SimulatedFactory(seed=4, start=T0, faults=FaultProfile(duplicate_rate=1.0))
    )
    c = build(t, sink=sink)
    s = c.step()
    assert s.duplicates == 8 and s.accepted == 8 and len(sink) == 8


def test_reconnect_uses_backoff_and_recovers() -> None:
    clock = Clock()
    t = SimulatedMqttTransport(SimulatedFactory(seed=2, start=T0), fail_connects=2)
    c = build(t, clock=clock)
    assert not c.step().connected and c.health.state is ConnectorState.DISCONNECTED
    assert c.health.last_error_category == "connection_refused"
    assert c.health.next_attempt_at == T0 + timedelta(seconds=2)
    c.step()  # too early: no new attempt
    assert t.connect_calls == 1
    clock.advance(2)
    assert not c.step().connected and t.connect_calls == 2
    assert c.health.next_attempt_at == clock.now + timedelta(seconds=4)  # exponential
    clock.advance(4)
    assert c.step().connected and c.health.state is ConnectorState.CONNECTED
    assert c.health.consecutive_failures == 0 and c.health.total_connections == 1


def test_connection_loss_mid_stream_reconnects_and_resubscribes() -> None:
    clock = Clock()
    t = SimulatedMqttTransport(SimulatedFactory(seed=3, start=T0), drop_at_polls={2})
    c = build(t, clock=clock)
    assert c.step().connected
    assert not c.step().connected  # poll #2 dropped
    assert (
        c.health.state is ConnectorState.DISCONNECTED
        and c.health.last_error_category == "connection_lost"
    )
    clock.advance(2)
    assert c.step().connected
    assert t.connect_calls == 2 and len(t.subscribed) == 2


def test_reconnect_limit_stops_the_connector_until_reset() -> None:
    clock = Clock()
    t = SimulatedMqttTransport(SimulatedFactory(seed=2, start=T0), fail_connects=100)
    c = build(t, clock=clock)
    for _ in range(10):
        c.step()
        clock.advance(60)
    assert c.health.state is ConnectorState.FAILED
    assert t.connect_calls == 4  # max_attempts, then no more hammering
    c.step()
    assert t.connect_calls == 4
    c.reset()
    c.step()
    assert t.connect_calls == 5


def test_health_maps_to_observability_states() -> None:
    clock = Clock()
    t = SimulatedMqttTransport(SimulatedFactory(seed=2, start=T0))
    c = build(t, clock=clock)
    assert c.health.as_check(clock.now).state is HealthState.UNKNOWN
    c.step()
    assert c.health.as_check(clock.now).state is HealthState.UP
    assert c.health.as_check(clock.now + timedelta(seconds=500)).state is HealthState.DEGRADED

    failing = build(
        SimulatedMqttTransport(SimulatedFactory(seed=2), fail_connects=100), clock=clock
    )
    failing.step()
    assert failing.health.as_check(clock.now).state is HealthState.DEGRADED
    for _ in range(6):
        clock.advance(60)
        failing.step()
    assert failing.health.as_check(clock.now).state is HealthState.DOWN


def test_metrics_gauge_and_failure_counter() -> None:
    reg = MetricsRegistry()
    clock = Clock()
    t = SimulatedMqttTransport(SimulatedFactory(seed=2, start=T0), fail_connects=1)
    c = build(t, clock=clock, registry=reg)
    c.step()
    assert 'qorvexa_connector_up{connector="mqtt-sim"} 0' in reg.render()
    assert 'qorvexa_connector_connection_failures_total{connector="mqtt-sim"} 1' in reg.render()
    clock.advance(2)
    c.step()
    assert 'qorvexa_connector_up{connector="mqtt-sim"} 1' in reg.render()
    c.close()
    assert 'qorvexa_connector_up{connector="mqtt-sim"} 0' in reg.render()


def test_transport_error_category_is_the_only_thing_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    t = SimulatedMqttTransport(SimulatedFactory(seed=2), fail_connects=1)
    with caplog.at_level("WARNING", logger="qorvexa.connectors.mqtt"):
        build(t).step()
    rec = next(r for r in caplog.records if r.name == "qorvexa.connectors.mqtt")
    assert rec.error_category == "connection_refused" and rec.connector_id == "mqtt-sim"  # type: ignore[attr-defined]


def test_backoff_policy_validation_and_cap() -> None:
    p = BackoffPolicy(base_s=1, factor=2, max_s=10, max_attempts=5, jitter=0.0)
    assert [p.delay(i, Random(0)) for i in (1, 2, 3, 4, 5)] == [1, 2, 4, 8, 10]
    with pytest.raises(ValueError):
        BackoffPolicy(jitter=1.0)
    with pytest.raises(ValueError):
        BackoffPolicy(max_attempts=0)
    jittered = BackoffPolicy(base_s=10, jitter=0.2).delay(1, Random(5))
    assert 8.0 <= jittered <= 12.0

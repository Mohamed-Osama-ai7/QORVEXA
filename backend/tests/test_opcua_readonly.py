# ruff: noqa: S106, S311  # fake credentials / seeded jitter in tests
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from random import Random
from typing import Any

import pytest

from qorvexa.connectors.connector_health import BackoffPolicy, ConnectorState
from qorvexa.connectors.edge import EdgeSecurityError
from qorvexa.connectors.opcua import (
    ApprovedNode,
    NodeErrorCategory,
    NodeReading,
    OpcUaConnectionConfig,
    OpcUaReadConnector,
    error_category,
)
from qorvexa.connectors.opcua import status as st
from qorvexa.connectors.readonly import ReadOnlyViolationError, ensure_read_only
from qorvexa.connectors.simulated import SimulatedFactory, SimulatedOpcUaTransport, opcua_node_id
from qorvexa.connectors.telemetry import (
    STATE_TAG,
    DataQuality,
    ProcessorConfig,
    TelemetryProcessor,
    TelemetryRecord,
    TelemetrySource,
)
from qorvexa.observability import MetricsRegistry
from qorvexa.observability.health import HealthState

T0 = datetime(2026, 11, 23, 8, 0, 0, tzinfo=UTC)
FID = "factory-sim-001"


def nid(equipment: str, tag: str) -> str:
    return opcua_node_id(FID, equipment, tag)


def node(equipment: str, tag: str, unit: str | None = None) -> ApprovedNode:
    return ApprovedNode(nid(equipment, tag), FID, equipment, tag, unit)


TEMP = node("machine-001", "temperature", "C")
STATE = node("machine-001", STATE_TAG)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def build(
    transport: Any,
    *,
    nodes: Sequence[ApprovedNode] = (TEMP, STATE),
    clock: Clock | None = None,
    mode: str = "poll",
    sink: list[TelemetryRecord] | None = None,
    registry: MetricsRegistry | None = None,
    max_age_s: float | None = None,
    backoff: BackoffPolicy | None = None,
) -> OpcUaReadConnector:
    clk = clock or Clock()
    out = sink if sink is not None else []
    return OpcUaReadConnector(
        connector_id="opcua-sim",
        transport=transport,
        nodes=nodes,
        processor=TelemetryProcessor(ProcessorConfig(max_age_s=max_age_s), clock=clk),
        sink=out.append,
        mode=mode,  # type: ignore[arg-type]
        source=TelemetrySource.SIMULATED,
        clock=clk,
        backoff=backoff or BackoffPolicy(base_s=2, max_s=30, max_attempts=4, jitter=0.0),
        rng=Random(1),
        registry=registry or MetricsRegistry(),
    )


def run(c: OpcUaReadConnector, clock: Clock, steps: int) -> None:
    for _ in range(steps):
        c.step()
        clock.advance(10)


# ---- status codes ----------------------------------------------------------------------------


def test_status_code_classification() -> None:
    assert st.is_good(st.GOOD) and not st.is_good(st.BAD_TIMEOUT)
    assert error_category(st.GOOD) is None
    assert error_category(st.UNCERTAIN_LAST_USABLE_VALUE) is None
    assert error_category(st.BAD_NODE_ID_UNKNOWN) is NodeErrorCategory.NODE_UNKNOWN
    assert error_category(st.BAD_USER_ACCESS_DENIED) is NodeErrorCategory.ACCESS_DENIED
    assert error_category(st.BAD_NO_DATA) is NodeErrorCategory.NO_DATA
    assert error_category(st.BAD_TIMEOUT) is NodeErrorCategory.COMMUNICATION
    assert error_category(0x80ABCDEF) is NodeErrorCategory.OTHER_BAD


# ---- config ----------------------------------------------------------------------------------


def test_config_validates_endpoint_and_env_names() -> None:
    cfg = OpcUaConnectionConfig(
        "opc.tcp://opc.example.com:4840",
        allowed_hosts=["opc.example.com"],
        username_env="OPCUA_USER",
        password_env="OPCUA_PASS",
    )
    assert "OPCUA_PASS" in repr(cfg) and "hunter2" not in repr(cfg)
    assert cfg.resolve_credentials({"OPCUA_USER": "u", "OPCUA_PASS": "hunter2"}) == ("u", "hunter2")
    with pytest.raises(ValueError, match="not set"):
        cfg.resolve_credentials({})
    anonymous = OpcUaConnectionConfig(
        "opc.tcp://opc.example.com", allowed_hosts=["opc.example.com"]
    )
    assert anonymous.resolve_credentials({}) is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"endpoint_url": "opc.tcp://evil.example.com", "allowed_hosts": ["opc.example.com"]},
        {"endpoint_url": "http://opc.example.com", "allowed_hosts": ["opc.example.com"]},
        {"endpoint_url": "opc.tcp://u:p@opc.example.com", "allowed_hosts": ["opc.example.com"]},
        {"endpoint_url": "opc.tcp://127.0.0.1:4840", "allowed_hosts": ["127.0.0.1"]},
        {"endpoint_url": "opc.tcp://10.0.0.5", "allowed_hosts": ["10.0.0.5"]},
    ],
)
def test_config_rejects_unapproved_endpoints(kwargs: dict[str, Any]) -> None:
    with pytest.raises(EdgeSecurityError):
        OpcUaConnectionConfig(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"username_env": "ONLY_USER"},
        {"username_env": "lower", "password_env": "P"},
        {"client_private_key_path_env": "bad name"},
        {"connect_timeout_s": 0},
        {"request_timeout_s": 120},
    ],
)
def test_config_rejects_bad_settings(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        OpcUaConnectionConfig("opc.tcp://opc.example.com", ["opc.example.com"], **kwargs)


# ---- read-only enforcement -------------------------------------------------------------------


class _FullClient:
    """What a full-featured library client looks like: it can write and call methods."""

    def connect(self) -> None: ...
    def close(self) -> None: ...
    def namespaces(self) -> Sequence[str]:
        return []

    def browse(self, node_id: str) -> Sequence[Any]:
        return []

    def read(self, node_ids: Sequence[str]) -> Sequence[NodeReading]:
        return []

    def create_subscription(self, node_ids: Sequence[str], interval_ms: int) -> None: ...
    def poll_changes(self, max_items: int) -> Sequence[NodeReading]:
        return []

    def delete_subscription(self) -> None: ...
    def write_value(self, node_id: str, value: object) -> None: ...
    def call_method(self, node_id: str, method: str) -> None: ...


def test_read_only_guard_refuses_clients_that_can_write() -> None:
    ensure_read_only(SimulatedOpcUaTransport(SimulatedFactory(seed=1)))
    with pytest.raises(ReadOnlyViolationError, match="write_value"):
        ensure_read_only(_FullClient())
    with pytest.raises(ReadOnlyViolationError):
        build(_FullClient())


class _Recorder:
    """Exposes only the protocol and records every attribute the connector touches."""

    ALLOWED = frozenset(
        {
            "connect",
            "close",
            "namespaces",
            "browse",
            "read",
            "create_subscription",
            "poll_changes",
            "delete_subscription",
        }
    )

    def __init__(self, inner: SimulatedOpcUaTransport) -> None:
        self._inner = inner
        self.touched: set[str] = set()

    def __getattr__(self, name: str) -> Any:
        if name not in self.ALLOWED:
            raise AttributeError(name)
        self.touched.add(name)
        return getattr(self._inner, name)


def test_connector_only_uses_read_operations() -> None:
    clock = Clock()
    rec = _Recorder(SimulatedOpcUaTransport(SimulatedFactory(seed=1, start=T0)))
    for mode in ("poll", "subscribe"):
        c = build(rec, clock=clock, mode=mode)
        run(c, clock, 3)
        c.discover()
        c.close()
    assert rec.touched <= _Recorder.ALLOWED
    assert {"read", "poll_changes", "browse", "create_subscription"} <= rec.touched


# ---- construction ----------------------------------------------------------------------------


def test_constructor_validates_approved_nodes() -> None:
    t = SimulatedOpcUaTransport(SimulatedFactory(seed=1))
    with pytest.raises(ValueError, match="at least one"):
        build(t, nodes=[])
    with pytest.raises(ValueError, match="duplicate approved node_id"):
        build(t, nodes=[TEMP, TEMP])
    with pytest.raises(ValueError, match="same factory"):
        build(t, nodes=[TEMP, ApprovedNode("ns=2;s=other", FID, "machine-001", "temperature")])
    with pytest.raises(ValueError, match="invalid identifier"):
        ApprovedNode("ns=2;s=x", FID, "bad id", "temperature")
    with pytest.raises(ValueError, match="invalid node_id"):
        ApprovedNode("bad\nid", FID, "m", "t")


# ---- polling ---------------------------------------------------------------------------------


def test_poll_delivers_normalized_synthetic_records() -> None:
    sink: list[TelemetryRecord] = []
    clock = Clock()
    c = build(SimulatedOpcUaTransport(SimulatedFactory(seed=1, start=T0)), sink=sink, clock=clock)
    s = c.step()
    assert s.connected and s.readings == 2 and s.accepted == 2 and s.node_errors == 0
    by_tag = {r.tag_id: r for r in sink}
    temp = by_tag["temperature"]
    assert temp.quality is DataQuality.GOOD and temp.synthetic and temp.unit == "C"
    assert temp.source is TelemetrySource.SIMULATED and temp.source_id == "opcua-sim"
    assert temp.timestamp == T0 and temp.value is not None
    assert by_tag[STATE_TAG].value in {float(i) for i in range(7)}
    assert c.health.state is ConnectorState.CONNECTED and c.health.protocol == "opcua"


def test_values_match_the_source_factory_exactly() -> None:
    sink: list[TelemetryRecord] = []
    clock = Clock()
    run(
        build(SimulatedOpcUaTransport(SimulatedFactory(seed=5, start=T0)), sink=sink, clock=clock),
        clock,
        5,
    )
    truth = [
        m.value
        for m in SimulatedFactory(seed=5, start=T0).measurements(6)
        if m.equipment_id == "machine-001" and m.tag_id == "temperature"
    ]
    got = [r.value for r in sink if r.tag_id == "temperature"]
    assert got == truth[: len(got)] and len(got) == 5


def test_unchanged_value_is_a_duplicate_not_a_new_record() -> None:
    sink: list[TelemetryRecord] = []
    clock = Clock()
    t = SimulatedOpcUaTransport(SimulatedFactory(seed=1, start=T0), freeze_after=1)
    c = build(t, sink=sink, clock=clock)
    first = c.step()
    clock.advance(10)
    again = c.step()
    assert first.accepted == 2 and again.accepted == 0 and again.duplicates == 2
    assert len(sink) == 2


def test_hung_source_is_detected_as_stale() -> None:
    clock = Clock()
    proc_cfg = ProcessorConfig(max_age_s=60)
    proc = TelemetryProcessor(proc_cfg, clock=clock)
    sink: list[TelemetryRecord] = []
    c = OpcUaReadConnector(
        connector_id="opcua-sim",
        transport=SimulatedOpcUaTransport(SimulatedFactory(seed=1, start=T0), freeze_after=1),
        nodes=[TEMP],
        processor=proc,
        sink=sink.append,
        source=TelemetrySource.SIMULATED,
        clock=clock,
        registry=MetricsRegistry(),
    )
    for _ in range(10):
        c.step()
        clock.advance(10)
    assert c.health.state is ConnectorState.CONNECTED  # the server is up...
    assert proc.stale_series(clock.now) == [(FID, "machine-001", "temperature")]  # ...data is not


def test_missing_source_timestamp_falls_back_but_is_never_good() -> None:
    sink: list[TelemetryRecord] = []
    t = SimulatedOpcUaTransport(SimulatedFactory(seed=1, start=T0), omit_source_timestamp=True)
    build(t, sink=sink).step()
    assert sink and all(r.quality is DataQuality.UNCERTAIN for r in sink)
    assert all(
        "SOURCE_QUALITY_UNCERTAIN" in r.quality_reasons and r.value is not None for r in sink
    )


class _Scripted(SimulatedOpcUaTransport):
    def __init__(self, readings: Sequence[NodeReading]) -> None:
        super().__init__(SimulatedFactory(seed=1, start=T0))
        self._script = readings

    def read(self, node_ids: Sequence[str]) -> Sequence[NodeReading]:
        return self._script


def test_bad_status_never_carries_a_value_and_uncertain_is_flagged() -> None:
    sink: list[TelemetryRecord] = []
    script = [
        NodeReading(TEMP.node_id, 99.9, 0x80000000, T0, T0),
        NodeReading(STATE.node_id, 1, st.UNCERTAIN_LAST_USABLE_VALUE, T0, T0),
    ]
    build(_Scripted(script), sink=sink).step()
    temp = next(r for r in sink if r.tag_id == "temperature")
    state = next(r for r in sink if r.tag_id == STATE_TAG)
    assert temp.value is None and temp.quality in (DataQuality.BAD, DataQuality.MISSING)
    assert state.value == 1.0 and state.quality is DataQuality.UNCERTAIN


def test_server_returning_unrequested_nodes_is_dropped() -> None:
    sink: list[TelemetryRecord] = []
    reg = MetricsRegistry()
    script = [
        NodeReading(TEMP.node_id, 70.0, st.GOOD, T0, T0),
        NodeReading(nid("machine-002", "temperature"), 70.0, st.GOOD, T0, T0),
    ]
    c = build(_Scripted(script), sink=sink, registry=reg)
    s = c.step()
    assert s.accepted == 1 and {r.equipment_id for r in sink} == {"machine-001"}
    assert 'outcome="unapproved_node"} 1' in reg.render()


def test_unknown_and_denied_nodes_are_counted_by_category() -> None:
    sink: list[TelemetryRecord] = []
    reg = MetricsRegistry()
    ghost = node("machine-001", "no_such_tag")
    secret = node("machine-001", "vibration")
    t = SimulatedOpcUaTransport(SimulatedFactory(seed=1, start=T0), denied_nodes=[secret.node_id])
    c = build(t, nodes=[TEMP, ghost, secret], sink=sink, registry=reg)
    s = c.step()
    assert s.node_errors == 2 and s.readings == 3
    bad = [r for r in sink if r.tag_id in ("no_such_tag", "vibration")]
    assert len(bad) == 2 and all(r.value is None for r in bad)
    assert all(
        r.quality in (DataQuality.MISSING, DataQuality.INVALID, DataQuality.BAD) for r in bad
    )
    text = reg.render()
    assert (
        'qorvexa_opcua_node_errors_total{connector="opcua-sim",category="node_unknown"} 1' in text
    )
    assert (
        'qorvexa_opcua_node_errors_total{connector="opcua-sim",category="access_denied"} 1' in text
    )
    assert c.health.extra["node_errors"] == 2


# ---- discovery -------------------------------------------------------------------------------


def test_discovery_reports_approved_nodes_and_hides_the_rest() -> None:
    clock = Clock()
    ghost = node("machine-001", "no_such_tag")
    c = build(
        SimulatedOpcUaTransport(SimulatedFactory(seed=1, start=T0)),
        nodes=[TEMP, ghost],
        clock=clock,
    )
    with pytest.raises(RuntimeError, match="connect"):
        c.discover()
    c.step()
    rep = c.discover()
    assert rep.namespaces == ("http://opcfoundation.org/UA/", "urn:qorvexa:simulated")
    assert rep.approved_found == (TEMP.node_id,) and rep.approved_missing == (ghost.node_id,)
    assert rep.unapproved_seen == 7  # 2 machines x 4 tags, minus the one approved variable
    assert not rep.truncated
    assert not any("vibration" in x for x in rep.approved_found + rep.approved_missing)


# ---- subscriptions ---------------------------------------------------------------------------


def test_subscription_delivers_changes_only_and_cleans_up() -> None:
    sink: list[TelemetryRecord] = []
    clock = Clock()
    t = SimulatedOpcUaTransport(SimulatedFactory(seed=1, start=T0), freeze_after=2)
    c = build(t, sink=sink, clock=clock, mode="subscribe")
    first = c.step()
    clock.advance(10)
    second = c.step()
    clock.advance(10)
    third = c.step()  # source frozen -> no data change -> nothing delivered
    assert first.accepted == 2 and second.accepted == 2 and third.readings == 0
    assert t.subscriptions_created == 1
    c.close()
    assert t.subscription_deleted == 1 and c.health.state is ConnectorState.DISCONNECTED


def test_subscription_is_recreated_after_a_reconnect() -> None:
    clock = Clock()
    t = SimulatedOpcUaTransport(SimulatedFactory(seed=3, start=T0), drop_at_calls={2})
    c = build(t, clock=clock, mode="subscribe")
    assert c.step().connected
    clock.advance(10)
    assert not c.step().connected and c.health.last_error_category == "connection_lost"
    clock.advance(2)
    assert c.step().connected
    assert t.connect_calls == 2 and t.subscriptions_created == 2


# ---- reconnect / health / metrics ------------------------------------------------------------


def test_reconnect_backoff_limit_and_reset() -> None:
    clock = Clock()
    t = SimulatedOpcUaTransport(SimulatedFactory(seed=2, start=T0), fail_connects=100)
    c = build(t, clock=clock)
    assert not c.step().connected
    assert c.health.next_attempt_at == T0 + timedelta(seconds=2)
    c.step()
    assert t.connect_calls == 1  # too early
    for _ in range(10):
        clock.advance(60)
        c.step()
    assert c.health.state is ConnectorState.FAILED and t.connect_calls == 4
    c.step()
    assert t.connect_calls == 4
    c.reset()
    c.step()
    assert t.connect_calls == 5


def test_recovers_after_refused_connections() -> None:
    clock = Clock()
    t = SimulatedOpcUaTransport(SimulatedFactory(seed=2, start=T0), fail_connects=2)
    c = build(t, clock=clock)
    c.step()
    clock.advance(2)
    c.step()
    clock.advance(4)
    assert c.step().connected and c.health.consecutive_failures == 0


def test_health_and_metrics() -> None:
    reg = MetricsRegistry()
    clock = Clock()
    c = build(
        SimulatedOpcUaTransport(SimulatedFactory(seed=2, start=T0)), clock=clock, registry=reg
    )
    assert c.health.as_check(clock.now).state is HealthState.UNKNOWN
    c.step()
    check = c.health.as_check(clock.now)
    assert check.state is HealthState.UP and "synthetic" in check.detail
    assert c.health.as_check(clock.now + timedelta(seconds=500)).state is HealthState.DEGRADED
    assert 'qorvexa_connector_up{connector="opcua-sim"} 1' in reg.render()
    c.close()
    assert 'qorvexa_connector_up{connector="opcua-sim"} 0' in reg.render()

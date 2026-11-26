# ruff: noqa: S105, S106  # test fixtures deliberately use fake secrets to prove redaction
from __future__ import annotations

import json
import logging
import threading
import time

import pytest

from qorvexa.observability import (
    CheckResult,
    Criticality,
    HealthCheck,
    HealthState,
    JsonFormatter,
    MetricsRegistry,
    OverallStatus,
    correlation_scope,
    evaluate_readiness,
    get_correlation_id,
    liveness,
    redact,
)
from qorvexa.observability.context import sanitize_correlation_id
from qorvexa.observability.metrics import MAX_SERIES_PER_METRIC


def _record(msg: str, **extra: object) -> logging.LogRecord:
    rec = logging.LogRecord("qorvexa.test", logging.INFO, __file__, 1, msg, (), None)
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


# ---- logging ---------------------------------------------------------------------------------


def test_json_log_has_required_fields_and_correlation_id() -> None:
    with correlation_scope("abc12345-test") as cid:
        out = json.loads(
            JsonFormatter("svc").format(
                _record("started", component="mqtt", event_type="connect", factory_id="f-1")
            )
        )
    assert out["severity"] == "INFO"
    assert out["service"] == "svc"
    assert out["correlation_id"] == cid == "abc12345-test"
    assert out["component"] == "mqtt" and out["factory_id"] == "f-1"
    assert out["timestamp"].endswith("+00:00")


def test_secret_fields_and_url_credentials_are_redacted() -> None:
    out = json.loads(
        JsonFormatter().format(
            _record(
                "connect mqtts://user:hunter2@broker:8883/t failed",
                password="hunter2",
                api_token="t",
                detail={"private_key": "k", "ok": "fine"},
            )
        )
    )
    blob = json.dumps(out)
    assert "hunter2" not in blob and '"k"' not in blob
    assert out["password"] == "[REDACTED]" and out["detail"]["ok"] == "fine"
    assert "mqtts://[REDACTED]@broker" in out["message"]


def test_exception_logs_type_not_traceback() -> None:
    try:
        raise ValueError("bad opc.tcp://a:secret@h/x")
    except ValueError:
        import sys

        rec = _record("boom")
        rec.exc_info = sys.exc_info()
    out = json.loads(JsonFormatter().format(rec))
    assert out["error_type"] == "ValueError" and "secret" not in out["error"]
    assert "Traceback" not in json.dumps(out)


def test_redact_is_recursive() -> None:
    assert redact({"a": [{"Authorization": "x"}]}) == {"a": [{"Authorization": "[REDACTED]"}]}


# ---- correlation -----------------------------------------------------------------------------


def test_correlation_scope_restores_and_rejects_unsafe_ids() -> None:
    assert get_correlation_id() is None
    with correlation_scope("bad id\nwith newline") as cid:
        assert cid != "bad id\nwith newline" and len(cid) == 32
        assert get_correlation_id() == cid
    assert get_correlation_id() is None
    assert sanitize_correlation_id("short") != "short"  # < 8 chars


# ---- metrics ---------------------------------------------------------------------------------


def test_counter_gauge_histogram_exposition() -> None:
    reg = MetricsRegistry()
    c = reg.counter("qorvexa_msgs_total", "Messages.", ["source"])
    g = reg.gauge("qorvexa_connector_up", "Up.", ["connector"])
    h = reg.histogram("qorvexa_sim_seconds", "Sim time.", [], buckets=[0.1, 1.0])
    c.inc(2, source="mqtt")
    c.inc(source="mqtt")
    g.set(1, connector="opcua-1")
    for v in (0.05, 0.5, 5.0):
        h.observe(v)
    text = reg.render()
    assert "# TYPE qorvexa_msgs_total counter" in text
    assert 'qorvexa_msgs_total{source="mqtt"} 3' in text
    assert 'qorvexa_connector_up{connector="opcua-1"} 1' in text
    assert 'qorvexa_sim_seconds_bucket{le="0.1"} 1' in text
    assert 'qorvexa_sim_seconds_bucket{le="1"} 2' in text
    assert 'qorvexa_sim_seconds_bucket{le="+Inf"} 3' in text
    assert "qorvexa_sim_seconds_count 3" in text
    assert "qorvexa_sim_seconds_sum 5.55" in text
    assert text.endswith("\n")


def test_label_escaping_and_validation() -> None:
    reg = MetricsRegistry()
    c = reg.counter("x_total", "h", ["v"])
    c.inc(v='a"b\\c\nd')
    assert 'v="a\\"b\\\\c\\nd"' in reg.render()
    with pytest.raises(ValueError):
        c.inc(wrong="1")
    with pytest.raises(ValueError):
        c.inc(-1, v="a")
    with pytest.raises(ValueError):
        reg.counter("bad name", "h")
    with pytest.raises(ValueError):
        reg.counter("y_total", "h", ["le"])


def test_registry_is_idempotent_but_rejects_conflicts() -> None:
    reg = MetricsRegistry()
    a = reg.counter("n_total", "h", ["a"])
    assert reg.counter("n_total", "h", ["a"]) is a
    with pytest.raises(ValueError):
        reg.gauge("n_total", "h", ["a"])
    with pytest.raises(ValueError):
        reg.counter("n_total", "h", ["b"])


def test_label_cardinality_is_capped() -> None:
    reg = MetricsRegistry()
    c = reg.counter("cap_total", "h", ["id"])
    for i in range(MAX_SERIES_PER_METRIC + 50):
        c.inc(id=str(i))
    assert c.value(id="__overflow__") == 50
    assert reg.render().count("cap_total{") == MAX_SERIES_PER_METRIC + 1


def test_counter_is_thread_safe() -> None:
    reg = MetricsRegistry()
    c = reg.counter("t_total", "h")

    def work() -> None:
        for _ in range(1000):
            c.inc()

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert c.value() == 8000


def test_histogram_rejects_bad_buckets_and_nan() -> None:
    reg = MetricsRegistry()
    with pytest.raises(ValueError):
        reg.histogram("h1", "h", buckets=[1, 1])
    h = reg.histogram("h2", "h")
    with pytest.raises(ValueError):
        h.observe(float("nan"))


# ---- health ----------------------------------------------------------------------------------


def _up() -> CheckResult:
    return CheckResult(HealthState.UP)


def _down() -> CheckResult:
    return CheckResult(HealthState.DOWN, "refused")


def _boom() -> CheckResult:
    raise RuntimeError("secret detail")


def test_liveness_is_independent_of_dependencies() -> None:
    assert liveness() == {"status": "alive"}


def test_readiness_ready_degraded_and_not_ready() -> None:
    ok = evaluate_readiness([HealthCheck("db", _up)])
    assert ok.status is OverallStatus.READY and ok.ready

    optional_down = evaluate_readiness(
        [HealthCheck("db", _up), HealthCheck("opcua", _down, Criticality.OPTIONAL)]
    )
    assert optional_down.status is OverallStatus.DEGRADED and optional_down.ready

    critical_down = evaluate_readiness([HealthCheck("db", _down), HealthCheck("mqtt", _up)])
    assert critical_down.status is OverallStatus.NOT_READY and not critical_down.ready


def test_raising_and_slow_checks_are_down_and_do_not_leak_messages() -> None:
    def slow() -> CheckResult:
        time.sleep(0.5)
        return CheckResult(HealthState.UP)

    report = evaluate_readiness([HealthCheck("a", _boom), HealthCheck("b", slow, timeout_s=0.05)])
    states = {c.name: c for c in report.components}
    assert states["a"].state is HealthState.DOWN and states["a"].detail == "RuntimeError"
    assert states["b"].state is HealthState.DOWN and "timed out" in states["b"].detail
    assert report.status is OverallStatus.NOT_READY
    assert "secret" not in json.dumps(report.as_dict())


def test_unknown_critical_dependency_is_not_ready() -> None:
    r = evaluate_readiness([HealthCheck("x", lambda: CheckResult(HealthState.UNKNOWN))])
    assert r.status is OverallStatus.NOT_READY

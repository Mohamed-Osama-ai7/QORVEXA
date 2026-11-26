# Observability Foundation (Member 6) — Step 1

Package: `backend/src/qorvexa/observability/` · tests: `backend/tests/observability/` (15).
Standard library only: **no new dependencies, `uv.lock` untouched, no shared file (`api/`, `contracts/`) modified.**

| Module | Purpose |
|---|---|
| `context.py` | `correlation_scope()` binds a correlation ID via `contextvars`; caller-supplied IDs are accepted only if 8-64 chars of `[A-Za-z0-9._-]`, otherwise a new one is minted (blocks log injection). |
| `structured_logging.py` | `JsonFormatter` / `configure_json_logging()`: one JSON object per line (`timestamp`, `severity`, `service`, `logger`, `message`, `correlation_id`, plus `component`, `event_type`, `factory_id`, `connector_id`, `error_category` when passed via `extra=`). Fields whose *name* looks secret and `scheme://user:pass@` URL credentials are redacted. Exceptions log the class and a redacted message only (no traceback/locals). |
| `metrics.py` | `MetricsRegistry` with `Counter`, `Gauge`, `Histogram` and Prometheus text exposition 0.0.4. Per-metric series are capped (1000, then `__overflow__`) so unbounded labels cannot exhaust memory. |
| `health.py` | `liveness()` (never touches dependencies) and `evaluate_readiness()` over `HealthCheck`s marked `CRITICAL` or `OPTIONAL`. A raising or timed-out probe is `DOWN`; an optional dependency that is down gives `DEGRADED` (still ready); a critical one gives `NOT_READY`. Probe exception messages are not echoed. |

## Not done yet (needs Technical Lead approval - shared files)
* Wire a request middleware (correlation ID header, request count/latency histogram) and `GET /metrics`, `/api/v1/health/live`, `/api/v1/health/ready` into `api/app.py`. The existing `GET /api/v1/health` is a static liveness response and is left unchanged.
* `/metrics` must be authenticated or network-restricted before any non-local deployment.
* OpenTelemetry tracing (adds `opentelemetry-*` dependencies).

## Verification
`ruff format --check`, `ruff check`, `mypy` clean for this package; `pytest tests/observability` -> 15 passed (Linux, Python 3.12.3). Full-suite and CI results not run by the author of this change.

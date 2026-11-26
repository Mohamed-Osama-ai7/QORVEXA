"""Observability foundation: structured logs, correlation IDs, metrics and health aggregation.

Owned by Member 6. Standard-library only; wiring into ``qorvexa.api`` (middleware, ``/metrics``,
readiness route) is a shared-file change that requires Technical Lead approval and is intentionally
not done here.
"""

from qorvexa.observability.context import correlation_scope, get_correlation_id
from qorvexa.observability.health import (
    CheckResult,
    Criticality,
    HealthCheck,
    HealthState,
    OverallStatus,
    ReadinessReport,
    evaluate_readiness,
    liveness,
)
from qorvexa.observability.metrics import (
    DEFAULT_REGISTRY,
    PROMETHEUS_CONTENT_TYPE,
    Counter,
    Gauge,
    Histogram,
    MetricsRegistry,
)
from qorvexa.observability.structured_logging import JsonFormatter, configure_json_logging, redact

__all__ = [
    "DEFAULT_REGISTRY",
    "PROMETHEUS_CONTENT_TYPE",
    "CheckResult",
    "Counter",
    "Criticality",
    "Gauge",
    "HealthCheck",
    "HealthState",
    "Histogram",
    "JsonFormatter",
    "MetricsRegistry",
    "OverallStatus",
    "ReadinessReport",
    "configure_json_logging",
    "correlation_scope",
    "evaluate_readiness",
    "get_correlation_id",
    "liveness",
    "redact",
]

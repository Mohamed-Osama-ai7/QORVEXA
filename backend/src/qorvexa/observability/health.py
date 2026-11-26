"""Liveness / readiness / dependency-health aggregation.

* **Liveness** - the process is running. Never depends on external systems.
* **Readiness** - every *critical* dependency is healthy. An unhealthy *optional* dependency
  (for example one industrial connector) degrades the report but does not make the service unready.
* A check that raises or exceeds its timeout is reported as DOWN, never as healthy.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final


class Criticality(StrEnum):
    CRITICAL = "critical"
    OPTIONAL = "optional"


class HealthState(StrEnum):
    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"


class OverallStatus(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    NOT_READY = "not_ready"


@dataclass(frozen=True)
class CheckResult:
    state: HealthState
    detail: str = ""


@dataclass(frozen=True)
class HealthCheck:
    name: str
    check: Callable[[], CheckResult]
    criticality: Criticality = Criticality.CRITICAL
    timeout_s: float = 2.0


@dataclass(frozen=True)
class ComponentReport:
    name: str
    criticality: Criticality
    state: HealthState
    detail: str
    latency_ms: float


@dataclass(frozen=True)
class ReadinessReport:
    status: OverallStatus
    components: Sequence[ComponentReport] = field(default_factory=tuple)

    @property
    def ready(self) -> bool:
        return self.status is not OverallStatus.NOT_READY

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "components": [
                {
                    "name": c.name,
                    "criticality": c.criticality.value,
                    "state": c.state.value,
                    "detail": c.detail,
                    "latency_ms": round(c.latency_ms, 3),
                }
                for c in self.components
            ],
        }


_MAX_DETAIL: Final = 200


def liveness() -> dict[str, str]:
    return {"status": "alive"}


def _run(check: HealthCheck) -> ComponentReport:
    started = time.perf_counter()
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        result = pool.submit(check.check).result(timeout=check.timeout_s)
    except FutureTimeout:
        result = CheckResult(HealthState.DOWN, f"timed out after {check.timeout_s}s")
    except Exception as exc:  # a failing probe is a DOWN dependency, not a crash
        result = CheckResult(HealthState.DOWN, f"{type(exc).__name__}")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return ComponentReport(
        name=check.name,
        criticality=check.criticality,
        state=result.state,
        detail=result.detail[:_MAX_DETAIL],
        latency_ms=(time.perf_counter() - started) * 1000.0,
    )


def evaluate_readiness(checks: Sequence[HealthCheck]) -> ReadinessReport:
    reports = tuple(_run(c) for c in checks)
    critical_bad = any(
        r.criticality is Criticality.CRITICAL and r.state in (HealthState.DOWN, HealthState.UNKNOWN)
        for r in reports
    )
    if critical_bad:
        return ReadinessReport(OverallStatus.NOT_READY, reports)
    any_bad = any(r.state is not HealthState.UP for r in reports)
    return ReadinessReport(OverallStatus.DEGRADED if any_bad else OverallStatus.READY, reports)

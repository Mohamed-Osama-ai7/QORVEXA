"""Connector lifecycle state, bounded reconnect policy and health reporting."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from random import Random

from qorvexa.observability.health import CheckResult, HealthState


class ConnectorState(StrEnum):
    INIT = "init"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"  # will retry at ``next_attempt_at``
    FAILED = "failed"  # reconnect limit reached; needs an operator reset


@dataclass(frozen=True)
class BackoffPolicy:
    base_s: float = 1.0
    factor: float = 2.0
    max_s: float = 60.0
    max_attempts: int = 8
    jitter: float = 0.2  # +/- fraction

    def __post_init__(self) -> None:
        if self.base_s <= 0 or self.factor < 1 or self.max_s < self.base_s or self.max_attempts < 1:
            raise ValueError("invalid backoff policy")
        if not 0 <= self.jitter < 1:
            raise ValueError("jitter must be in [0, 1)")

    def delay(self, attempt: int, rng: Random) -> float:
        """Delay before reconnect attempt number ``attempt`` (1-based)."""
        raw = min(self.max_s, self.base_s * self.factor ** (attempt - 1))
        return raw * (1.0 + self.jitter * (2.0 * rng.random() - 1.0))


@dataclass(frozen=True)
class ConnectorHealth:
    connector_id: str
    protocol: str
    state: ConnectorState = ConnectorState.INIT
    last_connected_at: datetime | None = None
    last_message_at: datetime | None = None
    next_attempt_at: datetime | None = None
    last_error_category: str | None = None
    consecutive_failures: int = 0
    total_connections: int = 0
    messages_received: int = 0
    records_accepted: int = 0
    parse_errors: int = 0
    records_rejected: int = 0
    duplicates: int = 0
    synthetic: bool = False
    extra: dict[str, int] = field(default_factory=dict)

    def evolve(self, **changes: object) -> ConnectorHealth:
        return replace(self, **changes)  # type: ignore[arg-type]

    def as_check(self, now: datetime, stale_after_s: float = 120.0) -> CheckResult:
        """Map to the observability health model. Connectors are normally OPTIONAL dependencies."""
        if self.state is ConnectorState.FAILED:
            return CheckResult(
                HealthState.DOWN, f"reconnect limit reached ({self.last_error_category})"
            )
        if self.state is ConnectorState.CONNECTED:
            ref = self.last_message_at or self.last_connected_at
            if ref is not None and (now - ref).total_seconds() > stale_after_s:
                return CheckResult(HealthState.DEGRADED, "connected but no recent data")
            return CheckResult(HealthState.UP, "synthetic source" if self.synthetic else "")
        if self.state is ConnectorState.INIT:
            return CheckResult(HealthState.UNKNOWN, "not started")
        return CheckResult(HealthState.DEGRADED, f"disconnected ({self.last_error_category})")

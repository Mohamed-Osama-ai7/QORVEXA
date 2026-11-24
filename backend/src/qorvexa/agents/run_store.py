"""SQLite claims, terminal records, and metadata-only workflow journals."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import cast
from uuid import uuid4

from qorvexa.agents.contracts import WorkflowResult, WorkflowScope


class RunState(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"


class RunEventKind(StrEnum):
    CLAIMED = "claimed"
    ATTEMPT_STARTED = "attempt_started"
    ATTEMPT_RETURNED = "attempt_returned"
    ATTEMPT_FAILED = "attempt_failed"
    RETRY_SCHEDULED = "retry_scheduled"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"


class RunConflictError(ValueError):
    """An idempotency key was already bound to a different request."""


class RunIntegrityError(RuntimeError):
    """Stored run data is inconsistent."""


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    scope: WorkflowScope
    state: RunState
    result: WorkflowResult | None = None


@dataclass(frozen=True, slots=True)
class RunEvent:
    kind: RunEventKind
    recorded_at: float
    task_id: str | None
    attempt: int | None


class RunStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS advisory_workflow_runs (
                    run_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    scope_json TEXT NOT NULL,
                    request_key TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('running', 'completed', 'interrupted')),
                    result_json TEXT,
                    result_sha256 TEXT,
                    UNIQUE (tenant_id, scope_json, request_key)
                );
                CREATE TABLE IF NOT EXISTS advisory_workflow_events (
                    seq INTEGER PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES advisory_workflow_runs(run_id),
                    kind TEXT NOT NULL,
                    recorded_at REAL NOT NULL,
                    task_id TEXT,
                    attempt INTEGER
                );
                CREATE INDEX IF NOT EXISTS advisory_workflow_events_by_run
                    ON advisory_workflow_events (run_id, seq);
                """
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _lookup(
        connection: sqlite3.Connection, scope: WorkflowScope, request_key: str
    ) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            connection.execute(
                """
                SELECT * FROM advisory_workflow_runs
                  WHERE tenant_id = ? AND scope_json = ? AND request_key = ?
                """,
                (scope.tenant_id, scope.model_dump_json(), request_key),
            ).fetchone(),
        )

    @staticmethod
    def _record(row: sqlite3.Row, scope: WorkflowScope) -> RunRecord:
        state = RunState(row["state"])
        raw: str | None = row["result_json"]
        digest: str | None = row["result_sha256"]
        result = None
        if state is RunState.COMPLETED:
            if raw is None or hashlib.sha256(raw.encode()).hexdigest() != digest:
                raise RunIntegrityError("Stored workflow result digest does not match")
            try:
                result = WorkflowResult.model_validate_json(raw)
            except ValueError as exc:
                raise RunIntegrityError("Stored workflow result failed validation") from exc
            if result.scope != scope or any(
                task.artifact is not None and task.artifact.ref.scope != scope
                for task in result.tasks
            ):
                raise RunIntegrityError("Stored workflow result scope does not match")
        elif raw is not None or digest is not None:
            raise RunIntegrityError("Non-completed run contains a stored result")
        return RunRecord(row["run_id"], scope, state, result)

    @staticmethod
    def _event(connection: sqlite3.Connection, run_id: str, event: RunEvent) -> None:
        connection.execute(
            """
            INSERT INTO advisory_workflow_events (run_id, kind, recorded_at, task_id, attempt)
              VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, event.kind, event.recorded_at, event.task_id, event.attempt),
        )

    def claim(
        self,
        scope: WorkflowScope,
        request_key: str,
        request_sha256: str,
        *,
        now: float,
        expires_at: float,
    ) -> tuple[RunRecord, bool]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._lookup(connection, scope, request_key)
            if existing is not None:
                if existing["request_sha256"] != request_sha256:
                    raise RunConflictError(
                        "Idempotency key is bound to a different workflow request"
                    )
                return self._record(existing, scope), False
            run_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO advisory_workflow_runs
                  (run_id, tenant_id, scope_json, request_key, request_sha256, expires_at, state)
                  VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    scope.tenant_id,
                    scope.model_dump_json(),
                    request_key,
                    request_sha256,
                    expires_at,
                    RunState.RUNNING,
                ),
            )
            self._event(connection, run_id, RunEvent(RunEventKind.CLAIMED, now, None, None))
            return RunRecord(run_id, scope, RunState.RUNNING), True

    def append(self, run: RunRecord, event: RunEvent) -> bool:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                """
                SELECT 1 FROM advisory_workflow_runs
                  WHERE run_id = ? AND tenant_id = ? AND scope_json = ? AND state = 'running'
                """,
                (run.run_id, run.scope.tenant_id, run.scope.model_dump_json()),
            ).fetchone()
            if active is None:
                return False
            self._event(connection, run.run_id, event)
            return True

    def finalize(
        self, run: RunRecord, request_key: str, result: WorkflowResult, *, now: float
    ) -> RunRecord:
        if result.scope != run.scope:
            raise RunIntegrityError("Workflow result scope does not match its run")
        raw = result.model_dump_json()
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._lookup(connection, run.scope, request_key)
            if row is None or row["run_id"] != run.run_id:
                raise RunIntegrityError("Workflow result has no matching claim")
            if row["state"] != RunState.RUNNING:
                return self._record(row, run.scope)
            connection.execute(
                """
                UPDATE advisory_workflow_runs
                  SET state = ?, result_json = ?, result_sha256 = ? WHERE run_id = ?
                """,
                (RunState.COMPLETED, raw, digest, run.run_id),
            )
            self._event(connection, run.run_id, RunEvent(RunEventKind.COMPLETED, now, None, None))
            return RunRecord(run.run_id, run.scope, RunState.COMPLETED, result)

    def recover_expired(
        self, scope: WorkflowScope, request_key: str, *, now: float
    ) -> RunRecord | None:
        """Close an expired claim without invoking any workflow operation."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._lookup(connection, scope, request_key)
            if row is None:
                return None
            current = self._record(row, scope)
            if current.state is not RunState.RUNNING or row["expires_at"] > now:
                return current
            connection.execute(
                "UPDATE advisory_workflow_runs SET state = ? WHERE run_id = ?",
                (RunState.INTERRUPTED, current.run_id),
            )
            self._event(
                connection, current.run_id, RunEvent(RunEventKind.INTERRUPTED, now, None, None)
            )
            return RunRecord(current.run_id, scope, RunState.INTERRUPTED)

    def events(self, scope: WorkflowScope, request_key: str) -> tuple[RunEvent, ...]:
        with self._connection() as connection:
            row = self._lookup(connection, scope, request_key)
            if row is None:
                return ()
            return tuple(
                RunEvent(RunEventKind(kind), recorded_at, task_id, attempt)
                for kind, recorded_at, task_id, attempt in connection.execute(
                    """
                    SELECT kind, recorded_at, task_id, attempt
                      FROM advisory_workflow_events WHERE run_id = ? ORDER BY seq
                    """,
                    (row["run_id"],),
                )
            )

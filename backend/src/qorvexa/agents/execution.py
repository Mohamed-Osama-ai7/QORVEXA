"""Bounded execution, cooperative task control, and durable run identity."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from functools import partial
from threading import Event, Lock, local
from typing import Annotated, Final

from pydantic import BaseModel, Field, JsonValue, TypeAdapter, model_validator

from qorvexa.agents.capabilities import Capability, CapabilityRegistry, TaskContext
from qorvexa.agents.contracts import (
    MAX_CONCURRENCY,
    FrozenContract,
    Identifier,
    TaskFailure,
    WorkflowScope,
    WorkflowSpec,
    encode_task_payload,
)
from qorvexa.agents.resource_budgets import ResourceBudgetPolicy, _resource_attempt, _ResourceLedger
from qorvexa.agents.run_store import RunEvent, RunEventKind, RunRecord, RunStore
from qorvexa.agents.runtime import Supervisor, TaskInvocationError, WorkflowValidationError
from qorvexa.auth.principal import Permission

_KEY: Final[TypeAdapter[str]] = TypeAdapter(Identifier)


class ExecutionPolicy(FrozenContract):
    run_timeout_s: Annotated[float, Field(ge=0.001, le=3600, allow_inf_nan=False)] = 60.0
    task_timeout_s: Annotated[float, Field(ge=0.001, le=3600, allow_inf_nan=False)] = 15.0
    max_run_attempts: Annotated[int, Field(ge=1, le=256)] = 64
    max_task_attempts: Annotated[int, Field(ge=1, le=8)] = 1
    max_concurrency: Annotated[int, Field(ge=1, le=MAX_CONCURRENCY)] = 1
    initial_backoff_s: Annotated[float, Field(ge=0, le=60, allow_inf_nan=False)] = 0.1
    max_backoff_s: Annotated[float, Field(ge=0, le=60, allow_inf_nan=False)] = 1.0
    resource_budgets: ResourceBudgetPolicy | None = None

    @model_validator(mode="after")
    def validate_backoff(self) -> ExecutionPolicy:
        if self.initial_backoff_s > self.max_backoff_s:
            raise ValueError("Initial backoff exceeds maximum backoff")
        return self


_DEFAULT_POLICY: Final = ExecutionPolicy()


class CancellationToken:
    """Thread-safe cancellation signal for the caller and cooperative task handlers."""

    def __init__(self) -> None:
        self._cancelled = Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def wait(self, seconds: float) -> None:
        self._cancelled.wait(seconds)


class ReplaySafeTransientError(RuntimeError):
    """An adapter classified a recoverable failure before any effect could occur."""


class _TaskControl:
    def __init__(
        self,
        cancellation: CancellationToken,
        deadline: float,
        clock: Callable[[], float],
        check_resources: Callable[[], None] | None = None,
    ) -> None:
        self._cancellation = cancellation
        self._deadline = deadline
        self._clock = clock
        self._check_resources = check_resources

    def _time_remaining(self) -> float:
        if self._cancellation.is_cancelled():
            raise TaskInvocationError(TaskFailure.CANCELLED)
        remaining = self._deadline - self._clock()
        if remaining <= 0:
            raise TaskInvocationError(TaskFailure.TIMED_OUT)
        return remaining

    def remaining(self) -> float:
        remaining = self._time_remaining()
        if self._check_resources is not None:
            self._check_resources()
        return remaining


_ACTIVE_TASK: Final[ContextVar[_TaskControl | None]] = ContextVar("advisory_task", default=None)


def task_remaining_seconds() -> float | None:
    """Check cancellation and return time remaining for the active task, if controlled."""
    control = _ACTIVE_TASK.get()
    return control.remaining() if control is not None else None


def task_checkpoint() -> None:
    """Observe cancellation or timeout inside a cooperative capability handler."""
    task_remaining_seconds()


class _ExecutionInvoker:
    def __init__(
        self,
        store: RunStore,
        run: RunRecord,
        policy: ExecutionPolicy,
        cancellation: CancellationToken,
        deadline: float,
        clock: Callable[[], float],
        wall_clock: Callable[[], float],
        wait: Callable[[CancellationToken, float], None],
    ) -> None:
        self._store = store
        self._run = run
        self._policy = policy
        self._cancellation = cancellation
        self._deadline = deadline
        self._clock = clock
        self._wall_clock = wall_clock
        self._wait = wait
        self._lock = Lock()
        self._resources = (
            _ResourceLedger(policy.resource_budgets)
            if policy.resource_budgets is not None
            else None
        )
        self._attempts = 0
        self._journal_failed = False
        self._result_control = local()

    def accept_result(self) -> None:
        control: _TaskControl = self._result_control.value
        del self._result_control.value
        with self._lock:
            self._check_journal()
            control.remaining()

    def _check_journal(self) -> None:
        if self._journal_failed:
            raise TaskInvocationError(TaskFailure.JOURNAL_UNAVAILABLE)

    def _write_journal(self, kind: RunEventKind, task_id: str, attempt: int) -> None:
        self._check_journal()
        try:
            active = self._store.append(
                self._run, RunEvent(kind, self._wall_clock(), task_id, attempt)
            )
        except sqlite3.Error as exc:
            self._journal_failed = True
            raise TaskInvocationError(TaskFailure.JOURNAL_UNAVAILABLE) from exc
        if not active:
            self._journal_failed = True
            raise TaskInvocationError(TaskFailure.JOURNAL_UNAVAILABLE)

    def _journal(self, kind: RunEventKind, task_id: str, attempt: int) -> None:
        with self._lock:
            self._write_journal(kind, task_id, attempt)

    def _check_attempt_budget(self) -> None:
        if self._attempts >= self._policy.max_run_attempts:
            raise TaskInvocationError(TaskFailure.BUDGET_EXHAUSTED)

    def _start_attempt(self, control: _TaskControl, task_id: str, attempt: int) -> None:
        with self._lock:
            control.remaining()
            self._check_attempt_budget()
            self._write_journal(RunEventKind.ATTEMPT_STARTED, task_id, attempt)
            control.remaining()
            self._attempts += 1

    def __call__(
        self, capability: Capability, context: TaskContext, operation: Callable[[], BaseModel], /
    ) -> BaseModel:
        policy = self._policy
        control = _TaskControl(
            self._cancellation,
            min(self._deadline, self._clock() + policy.task_timeout_s),
            self._clock,
            partial(self._resources.check, context.task_id)
            if self._resources is not None
            else None,
        )
        attempt = 0
        backoff = policy.initial_backoff_s
        while True:
            attempt += 1
            self._start_attempt(control, context.task_id, attempt)
            active_token = _ACTIVE_TASK.set(control)
            try:
                with _resource_attempt(self._resources, context.task_id, control._time_remaining):
                    result = operation()
                control.remaining()
            except ReplaySafeTransientError as exc:
                self._journal(RunEventKind.ATTEMPT_FAILED, context.task_id, attempt)
                control.remaining()
                if not capability.replay_safe:
                    raise
                if attempt >= policy.max_task_attempts:
                    raise TaskInvocationError(TaskFailure.RETRIES_EXHAUSTED) from exc
                with self._lock:
                    self._check_attempt_budget()
            except Exception:
                self._journal(RunEventKind.ATTEMPT_FAILED, context.task_id, attempt)
                control.remaining()
                raise
            else:
                self._journal(RunEventKind.ATTEMPT_RETURNED, context.task_id, attempt)
                control.remaining()
                self._result_control.value = control
                return result
            finally:
                _ACTIVE_TASK.reset(active_token)
            self._journal(RunEventKind.RETRY_SCHEDULED, context.task_id, attempt)
            self._wait(self._cancellation, min(backoff, control.remaining()))
            backoff = min(backoff * 2, policy.max_backoff_s)


def _wait(cancellation: CancellationToken, seconds: float) -> None:
    cancellation.wait(seconds)


class ExecutionService:
    def __init__(
        self,
        registry: CapabilityRegistry,
        store: RunStore,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        wait: Callable[[CancellationToken, float], None] = _wait,
    ) -> None:
        self._registry = registry
        self._store = store
        self._clock = clock
        self._wall_clock = wall_clock
        self._wait = wait

    def run(
        self,
        workflow: WorkflowSpec,
        *,
        scope: WorkflowScope,
        idempotency_key: str,
        inputs: Mapping[str, Mapping[str, JsonValue]] | None = None,
        permissions: frozenset[Permission] = frozenset(),
        policy: ExecutionPolicy = _DEFAULT_POLICY,
        cancellation: CancellationToken | None = None,
    ) -> RunRecord:
        _KEY.validate_python(idempotency_key, strict=True)
        Supervisor(self._registry).validate(workflow)
        submitted = dict(inputs) if inputs is not None else {}
        if submitted.keys() - {task.task_id for task in workflow.tasks}:
            raise WorkflowValidationError("Input supplied for an unknown task")
        snapshots: dict[str, dict[str, JsonValue]] = {}
        try:
            for task_id, payload in submitted.items():
                snapshots[task_id] = json.loads(encode_task_payload(payload))
        except (TypeError, ValueError, RecursionError) as exc:
            raise WorkflowValidationError("Run input is not bounded JSON") from exc
        request_digest = _request_digest(workflow, scope, snapshots, permissions, policy)

        now = self._wall_clock()
        deadline = self._clock() + policy.run_timeout_s
        record, claimed = self._store.claim(
            scope, idempotency_key, request_digest, now=now, expires_at=now + policy.run_timeout_s
        )
        if not claimed:
            return record

        invoker = _ExecutionInvoker(
            self._store,
            record,
            policy,
            cancellation if cancellation is not None else CancellationToken(),
            deadline,
            self._clock,
            self._wall_clock,
            self._wait,
        )
        supervisor = Supervisor(
            self._registry,
            invoker=invoker,
            accept_result=invoker.accept_result,
            max_concurrency=policy.max_concurrency,
        )
        result = supervisor.run(workflow, scope=scope, inputs=snapshots, permissions=permissions)
        return self._store.finalize(record, idempotency_key, result, now=self._wall_clock())

    def recover_expired(self, scope: WorkflowScope, idempotency_key: str) -> RunRecord | None:
        _KEY.validate_python(idempotency_key, strict=True)
        return self._store.recover_expired(scope, idempotency_key, now=self._wall_clock())


def _request_digest(
    workflow: WorkflowSpec,
    scope: WorkflowScope,
    inputs: dict[str, dict[str, JsonValue]],
    permissions: frozenset[Permission],
    policy: ExecutionPolicy,
) -> str:
    graph = workflow.model_dump(mode="json")
    graph["tasks"] = [
        {**task.model_dump(mode="json"), "dependencies": sorted(task.dependencies)}
        for task in sorted(workflow.tasks, key=lambda task: task.task_id)
    ]
    policy_data = policy.model_dump(mode="json", exclude_none=True)
    if policy.max_concurrency == 1:
        # Preserve established keys for the original serial policy.
        policy_data.pop("max_concurrency")
    encoded = json.dumps(
        {
            "workflow": graph,
            "scope": scope.model_dump(mode="json"),
            "inputs": inputs,
            "permissions": sorted(permissions),
            "policy": policy_data,
        },
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode()).hexdigest()

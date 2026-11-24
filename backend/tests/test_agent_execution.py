"""Deterministic lifecycle, idempotency, and recovery tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from pydantic import BaseModel, field_serializer

from qorvexa.agents import (
    CancellationToken,
    Capability,
    CapabilityRegistry,
    ExecutionPolicy,
    ExecutionService,
    ReplaySafeTransientError,
    RunConflictError,
    RunEventKind,
    RunState,
    RunStore,
    TaskContext,
    TaskFailure,
    TaskSpec,
    TaskStatus,
    WorkflowScope,
    WorkflowSpec,
    task_checkpoint,
    task_remaining_seconds,
)
from qorvexa.auth.principal import Permission

SCOPE = WorkflowScope(tenant_id="tenant-a", factory_id="factory-a")


class Request(BaseModel):
    text: str = "fixture"


class Response(BaseModel):
    text: str


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.delays: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def wall(self) -> float:
        return 1000.0 + self.now

    def advance(self, _cancellation: CancellationToken, delay: float) -> None:
        self.delays.append(delay)
        self.now += delay


def _workflow(*task_ids: str, version: str = "1") -> WorkflowSpec:
    return WorkflowSpec(
        workflow_id="advisory",
        version=version,
        tasks=tuple(
            TaskSpec(task_id=task_id, capability_id="test", capability_version="1")
            for task_id in task_ids
        ),
    )


def _service(
    tmp_path: Path,
    handler: Callable[[Request, TaskContext], Response],
    *,
    replay_safe: bool = False,
    clock: Clock | None = None,
    output_model: type[Response] = Response,
) -> tuple[ExecutionService, RunStore]:
    store = RunStore(tmp_path / "advisory-runs.sqlite")
    registry = CapabilityRegistry(
        [
            Capability.register(
                "test",
                "1",
                input_model=Request,
                output_model=output_model,
                handler=handler,
                replay_safe=replay_safe,
            )
        ]
    )
    clock = Clock() if clock is None else clock
    return (
        ExecutionService(
            registry, store, clock=clock.monotonic, wall_clock=clock.wall, wait=clock.advance
        ),
        store,
    )


def test_completed_idempotency_binds_scope_input_version_and_permissions(tmp_path: Path) -> None:
    calls: list[WorkflowScope] = []

    def handler(request: Request, context: TaskContext) -> Response:
        calls.append(context.scope)
        return Response(text=request.text)

    service, store = _service(tmp_path, handler)
    first = service.run(
        _workflow("a", "b"), scope=SCOPE, idempotency_key="key", inputs={"a": {"text": "secret"}}
    )
    replay = service.run(
        _workflow("b", "a"), scope=SCOPE, idempotency_key="key", inputs={"a": {"text": "secret"}}
    )
    assert replay == first and first.state is RunState.COMPLETED
    assert calls == [SCOPE, SCOPE]
    assert "secret" not in repr(store.events(SCOPE, "key"))

    with pytest.raises(RunConflictError):
        service.run(_workflow("a", "b"), scope=SCOPE, idempotency_key="key")
    with pytest.raises(RunConflictError):
        service.run(
            _workflow("a", "b", version="2"),
            scope=SCOPE,
            idempotency_key="key",
            inputs={"a": {"text": "secret"}},
        )
    with pytest.raises(RunConflictError):
        service.run(
            _workflow("a", "b"),
            scope=SCOPE,
            idempotency_key="key",
            inputs={"a": {"text": "secret"}},
            permissions=frozenset({Permission.ASSET_READ}),
        )
    other = WorkflowScope(tenant_id="tenant-a", factory_id="factory-b")
    assert store.events(other, "key") == ()
    independent = service.run(_workflow("a"), scope=other, idempotency_key="key")
    assert independent.run_id != first.run_id
    assert calls[-1] == other

    asset = WorkflowScope(tenant_id=SCOPE.tenant_id, asset_id=SCOPE.factory_id)
    assert store.events(asset, "key") == ()
    asset_run = service.run(_workflow("a"), scope=asset, idempotency_key="key")
    assert asset_run.run_id not in {first.run_id, independent.run_id}
    assert calls[-1] == asset


def test_concurrent_duplicate_observes_running_claim_and_does_not_invoke(tmp_path: Path) -> None:
    entered = Event()
    release = Event()
    calls: list[str] = []

    def handler(request: Request, context: TaskContext) -> Response:
        calls.append(context.task_id)
        entered.set()
        assert release.wait(5)
        return Response(text=request.text)

    service, _store = _service(tmp_path, handler)
    workflow = _workflow("a")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.run, workflow, scope=SCOPE, idempotency_key="duplicate")
        try:
            assert entered.wait(5)
            pending = service.run(workflow, scope=SCOPE, idempotency_key="duplicate")
            assert pending.state is RunState.RUNNING and pending.result is None
        finally:
            release.set()
        completed = future.result(timeout=5)
    assert completed.run_id == pending.run_id
    assert completed.state is RunState.COMPLETED
    assert calls == ["a"]


def test_retry_is_bounded_by_replay_safety_and_run_attempt_budget(tmp_path: Path) -> None:
    clock = Clock()
    calls: list[str] = []

    def handler(request: Request, context: TaskContext) -> Response:
        calls.append(context.task_id)
        if len(calls) < 3:
            raise ReplaySafeTransientError("private transient message")
        return Response(text=request.text)

    service, store = _service(tmp_path, handler, replay_safe=True, clock=clock)
    finished = service.run(
        _workflow("a", "b"),
        scope=SCOPE,
        idempotency_key="retry",
        policy=ExecutionPolicy(max_run_attempts=3, max_task_attempts=4),
    )
    assert calls == ["a", "a", "a"]
    assert clock.delays == [0.1, 0.2]
    assert finished.result is not None
    assert finished.result.tasks[0].status is TaskStatus.SUCCEEDED
    assert finished.result.tasks[1].failure is TaskFailure.BUDGET_EXHAUSTED
    journal = store.events(SCOPE, "retry")
    assert [event.attempt for event in journal if event.kind is RunEventKind.ATTEMPT_STARTED] == [
        1,
        2,
        3,
    ]
    assert "private transient message" not in repr(journal)


@pytest.mark.parametrize(
    ("replay_safe", "failure", "expected_attempts"),
    [(False, TaskFailure.HANDLER_FAILED, 1), (True, TaskFailure.RETRIES_EXHAUSTED, 2)],
)
def test_task_retry_limit_and_replay_safety(
    tmp_path: Path, replay_safe: bool, failure: TaskFailure, expected_attempts: int
) -> None:
    calls = 0

    def handler(_request: Request, _context: TaskContext) -> Response:
        nonlocal calls
        calls += 1
        raise ReplaySafeTransientError

    service, _store = _service(tmp_path, handler, replay_safe=replay_safe)
    run = service.run(
        _workflow("a"),
        scope=SCOPE,
        idempotency_key="variant",
        policy=ExecutionPolicy(max_task_attempts=2),
    )
    assert run.result is not None
    assert run.result.tasks[0].failure is failure
    assert calls == expected_attempts


def test_cancel_discards_handler_result_and_prevents_future_invocations(tmp_path: Path) -> None:
    token = CancellationToken()
    calls: list[str] = []

    def handler(request: Request, context: TaskContext) -> Response:
        calls.append(context.task_id)
        assert task_remaining_seconds() == 15.0
        task_checkpoint()
        token.cancel()
        return Response(text=request.text)

    service, _store = _service(tmp_path, handler)
    run = service.run(
        _workflow("a", "b"), scope=SCOPE, idempotency_key="cancel", cancellation=token
    )
    assert run.result is not None
    assert calls == ["a"]
    assert all(t.failure is TaskFailure.CANCELLED and t.artifact is None for t in run.result.tasks)
    assert task_remaining_seconds() is None


def test_result_serialization_that_exceeds_deadline_cannot_publish(tmp_path: Path) -> None:
    clock = Clock()

    class SlowResponse(Response):
        @field_serializer("text")
        def slow_encode(self, value: str) -> str:
            clock.now = 1.0
            return value

    service, _store = _service(
        tmp_path,
        lambda request, _context: SlowResponse(text=request.text),
        clock=clock,
        output_model=SlowResponse,
    )
    run = service.run(
        _workflow("a", "b"),
        scope=SCOPE,
        idempotency_key="deadline",
        policy=ExecutionPolicy(run_timeout_s=0.1, task_timeout_s=0.1),
    )
    assert run.result is not None
    assert all(t.failure is TaskFailure.TIMED_OUT and t.artifact is None for t in run.result.tasks)


def test_expired_claim_is_interrupted_and_late_worker_cannot_finalize(tmp_path: Path) -> None:
    clock = Clock()
    entered = Event()
    release = Event()
    calls: list[str] = []

    def handler(request: Request, context: TaskContext) -> Response:
        calls.append(context.task_id)
        entered.set()
        assert release.wait(5)
        return Response(text=request.text)

    service, store = _service(tmp_path, handler, clock=clock)
    workflow = _workflow("task")
    policy = ExecutionPolicy(run_timeout_s=2.0)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            service.run, workflow, scope=SCOPE, idempotency_key="expired", policy=policy
        )
        try:
            assert entered.wait(5)
            active = service.recover_expired(SCOPE, "expired")
            assert active is not None and active.state is RunState.RUNNING
            clock.now = 3.0
            interrupted = service.recover_expired(SCOPE, "expired")
            assert interrupted is not None and interrupted.state is RunState.INTERRUPTED
        finally:
            release.set()
        assert future.result(timeout=5) == interrupted
    assert (
        service.run(workflow, scope=SCOPE, idempotency_key="expired", policy=policy) == interrupted
    )
    assert calls == ["task"]
    assert store.events(SCOPE, "expired")[-1].kind is RunEventKind.INTERRUPTED


def test_journal_failure_prevents_handlers_from_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def handler(request: Request, context: TaskContext) -> Response:
        calls.append(context.task_id)
        return Response(text=request.text)

    service, store = _service(tmp_path, handler)

    def broken(*_args: object) -> bool:
        raise sqlite3.OperationalError("journal storage failure")

    monkeypatch.setattr(store, "append", broken)
    run = service.run(_workflow("a", "b"), scope=SCOPE, idempotency_key="broken-journal")
    assert run.result is not None
    assert calls == []
    assert all(t.failure is TaskFailure.JOURNAL_UNAVAILABLE for t in run.result.tasks)

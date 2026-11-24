"""Parallel advisory batches preserve ordering, controls, and policy gates."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path
from threading import Barrier, Event, Lock, get_ident
from typing import Final

import pytest
from pydantic import BaseModel, field_serializer, field_validator

from qorvexa.agents import (
    CancellationToken,
    Capability,
    CapabilityRegistry,
    ExecutionPolicy,
    ExecutionService,
    RunConflictError,
    RunEventKind,
    RunStore,
    Supervisor,
    TaskContext,
    TaskFailure,
    TaskSpec,
    TaskStatus,
    WorkflowScope,
    WorkflowSpec,
    task_checkpoint,
    task_remaining_seconds,
)
from qorvexa.agents.contracts import MAX_CONCURRENCY
from qorvexa.agents.run_store import RunEvent, RunRecord
from qorvexa.auth.principal import Permission

SCOPE: Final = WorkflowScope(tenant_id="tenant", factory_id="factory")
_REQUEST_ID: Final[ContextVar[str]] = ContextVar("test_request_id", default="")


class Message(BaseModel):
    value: str = ""


def _task(identifier: str, *dependencies: str, capability_id: str = "parallel") -> TaskSpec:
    return TaskSpec(
        task_id=identifier,
        capability_id=capability_id,
        capability_version="1",
        dependencies=dependencies,
    )


def _workflow(*tasks: TaskSpec) -> WorkflowSpec:
    return WorkflowSpec(workflow_id="parallel-example", version="1", tasks=tasks)


def _capability(
    handler: Callable[[Message, TaskContext], Message],
    *,
    capability_id: str = "parallel",
    parallel_safe: bool = True,
    permissions: frozenset[Permission] = frozenset(),
) -> Capability:
    return Capability.register(
        capability_id,
        "1",
        input_model=Message,
        output_model=Message,
        handler=handler,
        parallel_safe=parallel_safe,
        required_permissions=permissions,
    )


def test_parallel_batches_enforce_worker_bound_and_serial_barrier() -> None:
    from_a = Event()
    finished_b = Event()
    pairs = Barrier(2, timeout=5)
    lock = Lock()
    active: set[str] = set()
    starts: dict[str, frozenset[str]] = {}
    finishes: list[str] = []

    def handler(_message: Message, context: TaskContext) -> Message:
        task_id = context.task_id
        assert _REQUEST_ID.get() == "outer-request"
        with lock:
            starts[task_id] = frozenset(finishes)
            active.add(task_id)
            assert len(active) <= 2
            if task_id in {"c", "z"}:
                assert active == {task_id}
        if task_id in {"a", "b", "d", "e"}:
            pairs.wait()
        if task_id == "a":
            from_a.set()
            assert finished_b.wait(5)
        if task_id == "b":
            assert from_a.wait(5)
        if task_id == "z":
            assert tuple(item.ref.task_id for item in context.dependencies) == ("d", "e")
            assert [item.read() for item in context.dependencies] == [
                {"value": "d"},
                {"value": "e"},
            ]
        with lock:
            active.remove(task_id)
            finishes.append(task_id)
        if task_id == "b":
            finished_b.set()
        return Message(value=task_id)

    registry = CapabilityRegistry(
        [_capability(handler), _capability(handler, capability_id="serial", parallel_safe=False)]
    )
    workflow = _workflow(
        _task("z", "e", "d"),
        _task("e"),
        _task("b"),
        _task("d"),
        _task("a"),
        _task("c", capability_id="serial"),
    )
    token = _REQUEST_ID.set("outer-request")
    try:
        result = Supervisor(registry, max_concurrency=2).run(workflow, scope=SCOPE)
    finally:
        _REQUEST_ID.reset(token)

    assert [task.task_id for task in result.tasks] == ["a", "b", "c", "d", "e", "z"]
    assert all(task.status is TaskStatus.SUCCEEDED for task in result.tasks)
    assert finishes[:3] == ["b", "a", "c"]
    assert starts["c"] == {"a", "b"}
    assert starts["d"] >= {"c"} and starts["e"] >= {"c"}
    assert starts["z"] == {"a", "b", "c", "d", "e"}


def test_parallel_batches_select_all_ready_siblings_before_descendants() -> None:
    siblings = Barrier(2, timeout=5)
    completed = Event()

    def handler(_message: Message, context: TaskContext) -> Message:
        if context.task_id in {"b", "z"}:
            assert context.dependency("a").read() == {"value": "a"}
            siblings.wait()
        if context.task_id == "z":
            completed.set()
        if context.task_id == "c":
            assert completed.is_set()
            assert context.dependency("b").read() == {"value": "b"}
        return Message(value=context.task_id)

    supervisor = Supervisor(CapabilityRegistry([_capability(handler)]), max_concurrency=2)
    result = supervisor.run(
        _workflow(_task("z", "a"), _task("c", "b"), _task("a"), _task("b", "a")), scope=SCOPE
    )
    assert [task.task_id for task in result.tasks] == ["a", "b", "c", "z"]
    assert all(task.status is TaskStatus.SUCCEEDED for task in result.tasks)


@pytest.mark.parametrize(("worker_count", "declared_safe"), [(None, True), (4, False)])
def test_default_and_serial_only_handlers_execute_on_calling_thread(
    worker_count: int | None, declared_safe: bool
) -> None:
    observed: list[tuple[str, int]] = []

    def handler(_message: Message, context: TaskContext) -> Message:
        observed.append((context.task_id, get_ident()))
        return Message()

    registry = CapabilityRegistry([_capability(handler, parallel_safe=declared_safe)])
    supervisor = (
        Supervisor(registry)
        if worker_count is None
        else Supervisor(registry, max_concurrency=worker_count)
    )
    supervisor.run(_workflow(_task("z"), _task("b", "a"), _task("a")), scope=SCOPE)
    assert observed == [(task_id, get_ident()) for task_id in ("a", "b", "z")]
    with pytest.raises(ValueError, match="Maximum concurrency"):
        Supervisor(registry, max_concurrency=MAX_CONCURRENCY + 1)


def test_parallel_batches_preserve_denial_failure_and_dependency_blocking() -> None:
    calls: set[str] = set()
    active = Barrier(2, timeout=5)
    lock = Lock()

    def handler(_message: Message, context: TaskContext) -> Message:
        with lock:
            calls.add(context.task_id)
        active.wait()
        if context.task_id == "c":
            raise ValueError("failed advisory task")
        return Message()

    registry = CapabilityRegistry(
        [
            _capability(handler),
            _capability(
                handler, capability_id="protected", permissions=frozenset({Permission.ASSET_READ})
            ),
        ]
    )
    result = Supervisor(registry, max_concurrency=3).run(
        _workflow(
            _task("x", "c", "b"),
            _task("a"),
            _task("b", capability_id="protected"),
            _task("y", "x"),
            _task("c"),
        ),
        scope=SCOPE,
    )

    assert calls == {"a", "c"}
    a, b, c, x, y = result.tasks
    assert a.status is TaskStatus.SUCCEEDED
    assert b.failure is TaskFailure.PERMISSION_DENIED
    assert c.failure is TaskFailure.HANDLER_FAILED
    assert x.blocked_by == ("b", "c")
    assert y.blocked_by == ("x",)


def test_parallel_attempt_budget_is_atomic_and_concurrency_is_idempotency_bound(
    tmp_path: Path,
) -> None:
    in_validation = Barrier(2, timeout=5)
    calls: list[str] = []

    class SimultaneousInput(Message):
        @field_validator("value")
        @classmethod
        def rendezvous(cls, value: str) -> str:
            in_validation.wait()
            return value

    def handler(_request: SimultaneousInput, context: TaskContext) -> Message:
        calls.append(context.task_id)
        return Message()

    cap = Capability.register(
        "parallel",
        "1",
        input_model=SimultaneousInput,
        output_model=Message,
        handler=handler,
        parallel_safe=True,
    )
    store = RunStore(tmp_path / "parallel.sqlite")
    service = ExecutionService(CapabilityRegistry([cap]), store)
    workflow = _workflow(_task("b"), _task("a"))
    policy = ExecutionPolicy(max_concurrency=2, max_run_attempts=1)
    inputs = {"a": {"value": "start"}, "b": {"value": "start"}}
    finished = service.run(
        workflow, scope=SCOPE, idempotency_key="same", policy=policy, inputs=inputs
    )

    assert len(calls) == 1
    assert finished.result is not None
    assert [task.task_id for task in finished.result.tasks] == ["a", "b"]
    assert {task.failure for task in finished.result.tasks} == {None, TaskFailure.BUDGET_EXHAUSTED}
    starts = [
        event for event in store.events(SCOPE, "same") if event.kind is RunEventKind.ATTEMPT_STARTED
    ]
    assert [event.task_id for event in starts] == calls
    assert (
        service.run(workflow, scope=SCOPE, idempotency_key="same", policy=policy, inputs=inputs)
        == finished
    )
    with pytest.raises(RunConflictError):
        service.run(
            workflow,
            scope=SCOPE,
            idempotency_key="same",
            policy=ExecutionPolicy(max_run_attempts=1),
            inputs=inputs,
        )


def test_default_serial_policy_can_reuse_a_legacy_claim(tmp_path: Path) -> None:
    def handler(_request: Message, _context: TaskContext) -> Message:
        pytest.fail("reusing a serial claim must not invoke its capability")

    workflow = _workflow(_task("a"))
    original_request = {
        "workflow": workflow.model_dump(mode="json"),
        "scope": SCOPE.model_dump(mode="json"),
        "inputs": {},
        "permissions": [],
        "policy": {
            "run_timeout_s": 60.0,
            "task_timeout_s": 15.0,
            "max_run_attempts": 64,
            "max_task_attempts": 1,
            "initial_backoff_s": 0.1,
            "max_backoff_s": 1.0,
        },
    }
    encoded = json.dumps(original_request, allow_nan=False, sort_keys=True, separators=(",", ":"))
    store = RunStore(tmp_path / "legacy.sqlite")
    legacy, claimed = store.claim(
        SCOPE, "original", hashlib.sha256(encoded.encode()).hexdigest(), now=0.0, expires_at=60.0
    )
    assert claimed

    service = ExecutionService(CapabilityRegistry([_capability(handler)]), store)
    assert service.run(workflow, scope=SCOPE, idempotency_key="original") == legacy


def test_parallel_result_acceptance_checks_each_workers_deadline(tmp_path: Path) -> None:
    a_started = Event()
    b_serializing = Event()
    lock = Lock()
    elapsed = 0.0

    def clock() -> float:
        with lock:
            return elapsed

    class StaggeredInput(Message):
        @field_validator("value")
        @classmethod
        def before_invocation(cls, value: str) -> str:
            nonlocal elapsed
            if value == "b":
                assert a_started.wait(5)
                with lock:
                    elapsed = 3.0
            return value

    class StaggeredOutput(Message):
        @field_serializer("value")
        def during_serialization(self, value: str) -> str:
            nonlocal elapsed
            if value == "a":
                assert b_serializing.wait(5)
                with lock:
                    elapsed = 6.0
            else:
                b_serializing.set()
            return value

    def handler(request: StaggeredInput, _context: TaskContext) -> StaggeredOutput:
        assert task_remaining_seconds() == 5.0
        task_checkpoint()
        if request.value == "a":
            a_started.set()
        return StaggeredOutput(value=request.value)

    cap = Capability.register(
        "parallel",
        "1",
        input_model=StaggeredInput,
        output_model=StaggeredOutput,
        handler=handler,
        parallel_safe=True,
    )
    service = ExecutionService(
        CapabilityRegistry([cap]), RunStore(tmp_path / "deadlines.sqlite"), clock=clock
    )
    finished = service.run(
        _workflow(_task("a"), _task("b")),
        scope=SCOPE,
        idempotency_key="deadline",
        policy=ExecutionPolicy(max_concurrency=2, task_timeout_s=5.0),
        inputs={"a": {"value": "a"}, "b": {"value": "b"}},
    )

    assert finished.result is not None
    a, b = finished.result.tasks
    assert a.failure is TaskFailure.TIMED_OUT and a.artifact is None
    assert b.status is TaskStatus.SUCCEEDED
    assert task_remaining_seconds() is None


def test_parallel_cancellation_propagates_to_peers_before_join(tmp_path: Path) -> None:
    token = CancellationToken()
    started = Barrier(2, timeout=5)
    cancelled = Event()

    def handler(_request: Message, context: TaskContext) -> Message:
        assert task_remaining_seconds() is not None
        started.wait()
        if context.task_id == "a":
            token.cancel()
            cancelled.set()
        else:
            assert cancelled.wait(5)
            task_checkpoint()
        return Message()

    service = ExecutionService(
        CapabilityRegistry([_capability(handler)]), RunStore(tmp_path / "cancel.sqlite")
    )
    finished = service.run(
        _workflow(_task("a"), _task("b"), _task("join", "a", "b")),
        scope=SCOPE,
        idempotency_key="cancel",
        policy=ExecutionPolicy(max_concurrency=2),
        cancellation=token,
    )

    assert finished.result is not None
    a, b, join = finished.result.tasks
    assert a.failure is b.failure is TaskFailure.CANCELLED
    assert a.artifact is b.artifact is None
    assert join.status is TaskStatus.BLOCKED and join.blocked_by == ("a", "b")


def test_parallel_journal_failure_poison_prevents_publication_and_later_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = Barrier(2, timeout=5)
    poison = Event()
    calls: set[str] = set()
    lock = Lock()

    def handler(_request: Message, context: TaskContext) -> Message:
        with lock:
            calls.add(context.task_id)
        started.wait()
        if context.task_id == "b":
            assert poison.wait(5)
        return Message()

    store = RunStore(tmp_path / "journal.sqlite")
    real_append = store.append

    def append(run: RunRecord, event: RunEvent) -> bool:
        if event.task_id == "a" and event.kind is RunEventKind.ATTEMPT_RETURNED:
            poison.set()
            raise sqlite3.OperationalError("test journal write failure")
        return real_append(run, event)

    monkeypatch.setattr(store, "append", append)
    service = ExecutionService(CapabilityRegistry([_capability(handler)]), store)
    finished = service.run(
        _workflow(_task("a"), _task("b"), _task("c")),
        scope=SCOPE,
        idempotency_key="journal",
        policy=ExecutionPolicy(max_concurrency=2),
    )

    assert finished.result is not None
    assert calls == {"a", "b"}
    assert all(
        task.failure is TaskFailure.JOURNAL_UNAVAILABLE and task.artifact is None
        for task in finished.result.tasks
    )

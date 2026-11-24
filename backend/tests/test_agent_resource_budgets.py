"""Advisory resource ceilings and reservations across execution attempts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import Context, copy_context
from pathlib import Path
from threading import Barrier, Event, Lock, current_thread

import pytest
from pydantic import ValidationError

from qorvexa.agents import (
    CancellationToken,
    Capability,
    CapabilityRegistry,
    ExecutionPolicy,
    ExecutionService,
    ReplaySafeTransientError,
    ResourceAmounts,
    ResourceBudgetPolicy,
    RunConflictError,
    RunStore,
    TaskContext,
    TaskFailure,
    TaskStatus,
    WorkflowScope,
    reserve_task_resources,
    resource_budgets,
)
from qorvexa.agents.runtime import TaskInvocationError
from qorvexa.knowledge.rag import HybridRagIndex, KnowledgeDocumentChunk
from tests.test_agent_execution import SCOPE, Clock, Request, Response, _service, _workflow


def _policy(run: ResourceAmounts, task: ResourceAmounts, *, attempts: int = 1) -> ExecutionPolicy:
    return ExecutionPolicy(
        resource_budgets=ResourceBudgetPolicy(run=run, task=task),
        max_task_attempts=attempts,
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"model_tokens": True},
        {"retrieval_calls": "1"},
        {"solver_units": 1.0},
        {"simulation_units": float("inf")},
        {"model_tokens": -1},
        {"retrieval_calls": 2_147_483_648},
        {"arbitrary_tool": 1},
    ],
)
def test_resource_vectors_require_bounded_integers(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ResourceAmounts.model_validate(payload)


def test_run_limit_stops_real_retrieval_before_the_adapter_call(tmp_path: Path) -> None:
    scope = WorkflowScope(tenant_id="tenant-a", asset_id="pump-a")
    index = HybridRagIndex(
        [
            KnowledgeDocumentChunk(
                chunk_id="pump-note",
                doc_id="pump-manual",
                title="Pump Manual",
                section="Maintenance",
                asset_id="pump-a",
                tenant_scope="tenant-a",
                source_path="manual.md",
                license="Internal",
                provenance_origin="observed",
                text="Cavitation causes reduced pump flow and impeller erosion.",
            )
        ]
    )
    searched: list[str] = []

    def handler(_request: Request, context: TaskContext) -> Response:
        reserve_task_resources(ResourceAmounts(retrieval_calls=1))
        searched.append(context.task_id)
        assert context.scope.asset_id is not None
        results = index.search(
            "pump cavitation",
            tenant_id=context.scope.tenant_id,
            allowed_asset_ids={context.scope.asset_id},
            top_k=1,
            mode="bm25_only",
        )
        return Response(text=results.items[0].chunk_id)

    service, _ = _service(tmp_path, handler)
    cap = ResourceAmounts(retrieval_calls=1)
    run = service.run(
        _workflow("a", "b"), scope=scope, idempotency_key="search", policy=_policy(cap, cap)
    )

    assert searched == ["a"]
    assert run.result is not None
    first, second = run.result.tasks
    assert first.artifact is not None and first.artifact.read() == {"text": "pump-note"}
    assert second.failure is TaskFailure.BUDGET_EXHAUSTED and second.artifact is None


def test_parallel_workflow_tasks_share_one_run_budget(tmp_path: Path) -> None:
    entered = Barrier(2)
    cap = ResourceAmounts(retrieval_calls=1)

    def handler(_request: Request, context: TaskContext) -> Response:
        entered.wait(timeout=5)
        reserve_task_resources(cap)
        return Response(text=context.task_id)

    capability = Capability.register(
        "test", "1", input_model=Request, output_model=Response, handler=handler, parallel_safe=True
    )
    service = ExecutionService(
        CapabilityRegistry((capability,)), RunStore(tmp_path / "parallel.db")
    )
    run = service.run(
        _workflow("a", "b"),
        scope=SCOPE,
        idempotency_key="parallel-limit",
        policy=ExecutionPolicy(
            max_concurrency=2, resource_budgets=ResourceBudgetPolicy(run=cap, task=cap)
        ),
    )

    assert run.result is not None
    assert [task.task_id for task in run.result.tasks] == ["a", "b"]
    [success] = [task for task in run.result.tasks if task.status is TaskStatus.SUCCEEDED]
    [failure] = [task for task in run.result.tasks if task.status is TaskStatus.FAILED]
    assert success.artifact is not None and success.artifact.read() == {"text": success.task_id}
    assert failure.failure is TaskFailure.BUDGET_EXHAUSTED and failure.artifact is None


def test_rejected_vector_neither_charges_other_dimensions_nor_publishes(tmp_path: Path) -> None:
    cap = ResourceAmounts(model_tokens=3, retrieval_calls=2, solver_units=1, simulation_units=4)
    successes: list[str] = []

    def handler(request: Request, context: TaskContext) -> Response:
        if context.task_id == "reject":
            with pytest.raises(TaskInvocationError) as exc:
                reserve_task_resources(cap.model_copy(update={"solver_units": 2}))
            assert exc.value.failure is TaskFailure.BUDGET_EXHAUSTED
            with pytest.raises(TaskInvocationError):
                reserve_task_resources(ResourceAmounts(retrieval_calls=1))
            return Response(text="unpublishable")
        reserve_task_resources(cap)
        successes.append(context.task_id)
        return Response(text=request.text)

    service, _ = _service(tmp_path, handler)
    workflow = _workflow("reject", "review", "separate")
    reject, review, separate = workflow.tasks
    workflow = workflow.model_copy(
        update={
            "tasks": (reject, review.model_copy(update={"dependencies": ("reject",)}), separate)
        }
    )
    run = service.run(workflow, scope=SCOPE, idempotency_key="atomic", policy=_policy(cap, cap))

    assert run.result is not None
    results = {task.task_id: task for task in run.result.tasks}
    assert results["reject"].failure is TaskFailure.BUDGET_EXHAUSTED
    assert results["reject"].artifact is None
    assert results["review"].status is TaskStatus.BLOCKED
    assert results["review"].blocked_by == ("reject",)
    assert results["separate"].status is TaskStatus.SUCCEEDED
    assert successes == ["separate"]


def test_reservation_revalidates_constructed_amounts(tmp_path: Path) -> None:
    def handler(request: Request, _context: TaskContext) -> Response:
        with pytest.raises(TaskInvocationError) as exc:
            reserve_task_resources(ResourceAmounts.model_construct(model_tokens=-1))
        assert exc.value.failure is TaskFailure.BUDGET_EXHAUSTED
        return Response(text=request.text)

    service, _ = _service(tmp_path, handler)
    cap = ResourceAmounts(model_tokens=1)
    run = service.run(
        _workflow("a"), scope=SCOPE, idempotency_key="typed", policy=_policy(cap, cap)
    )

    assert run.result is not None
    result = run.result.tasks[0]
    assert result.failure is TaskFailure.BUDGET_EXHAUSTED and result.artifact is None


def test_task_and_run_limits_accumulate_across_retries(tmp_path: Path) -> None:
    invocations: list[str] = []
    paid_work: list[str] = []

    def handler(request: Request, context: TaskContext) -> Response:
        task_id = context.task_id
        invocations.append(task_id)
        count = 3 if task_id == "b" else 2
        reserve_task_resources(ResourceAmounts(model_tokens=count))
        paid_work.append(task_id)
        if task_id == "a":
            raise ReplaySafeTransientError
        return Response(text=request.text)

    service, _ = _service(tmp_path, handler, replay_safe=True)
    policy = _policy(ResourceAmounts(model_tokens=6), ResourceAmounts(model_tokens=3), attempts=3)
    run = service.run(_workflow("a", "b", "c"), scope=SCOPE, idempotency_key="retry", policy=policy)

    assert invocations == ["a", "a", "b", "c"]
    assert paid_work == ["a", "b"]
    assert run.result is not None
    first, second, third = run.result.tasks
    assert first.failure is TaskFailure.BUDGET_EXHAUSTED
    assert second.status is TaskStatus.SUCCEEDED
    assert third.failure is TaskFailure.BUDGET_EXHAUSTED


def test_threaded_reservations_share_an_atomic_counter(tmp_path: Path) -> None:
    cap = ResourceAmounts(simulation_units=1)
    outcomes: list[bool] = []

    def handler(request: Request, _context: TaskContext) -> Response:
        start = Barrier(2)

        def work() -> bool:
            start.wait(timeout=5)
            try:
                reserve_task_resources(cap)
            except TaskInvocationError:
                return False
            return True

        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = [pool.submit(copy_context().run, work) for _ in range(2)]
            outcomes.extend(attempt.result(timeout=5) for attempt in attempts)
        return Response(text=request.text)

    service, _ = _service(tmp_path, handler)
    run = service.run(
        _workflow("a"), scope=SCOPE, idempotency_key="threads", policy=_policy(cap, cap)
    )

    assert sorted(outcomes) == [False, True]
    assert run.result is not None
    assert run.result.tasks[0].failure is TaskFailure.BUDGET_EXHAUSTED
    assert run.result.tasks[0].artifact is None


def test_reservation_context_from_a_prior_attempt_is_closed(tmp_path: Path) -> None:
    old_context: Context | None = None
    cap = ResourceAmounts(solver_units=1)

    def handler(request: Request, _context: TaskContext) -> Response:
        nonlocal old_context
        if old_context is None:
            old_context = copy_context()
            raise ReplaySafeTransientError
        with pytest.raises(TaskInvocationError) as exc:
            old_context.run(reserve_task_resources, cap)
        assert exc.value.failure is TaskFailure.BUDGET_EXHAUSTED
        reserve_task_resources(cap)
        return Response(text=request.text)

    service, _ = _service(tmp_path, handler, replay_safe=True)
    policy = _policy(cap, cap, attempts=2)
    run = service.run(_workflow("a"), scope=SCOPE, idempotency_key="context", policy=policy)

    assert run.result is not None
    assert run.result.tasks[0].status is TaskStatus.SUCCEEDED


@pytest.mark.parametrize("cancel", [True, False])
def test_reservation_observes_cancellation_and_deadline_after_entry(
    tmp_path: Path, cancel: bool
) -> None:
    clock = Clock()
    cancellation = CancellationToken()
    expected = TaskFailure.CANCELLED if cancel else TaskFailure.TIMED_OUT
    cap = ResourceAmounts(retrieval_calls=1)
    worked: list[bool] = []
    failures: list[TaskFailure] = []

    def handler(request: Request, _context: TaskContext) -> Response:
        if cancel:
            cancellation.cancel()
        else:
            clock.now += 20
        try:
            reserve_task_resources(cap)
            worked.append(True)
        except TaskInvocationError as exc:
            failures.append(exc.failure)
        return Response(text=request.text)

    service, _ = _service(tmp_path, handler, clock=clock)
    run = service.run(
        _workflow("a"),
        scope=SCOPE,
        idempotency_key="stopped",
        policy=_policy(cap, cap),
        cancellation=cancellation,
    )

    assert not worked and failures == [expected]
    assert run.result is not None
    assert run.result.tasks[0].failure is expected
    assert run.result.tasks[0].artifact is None


def test_waiting_reservation_rechecks_cancellation_inside_ledger_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ObservedLock:
        def __init__(self) -> None:
            self.lock = Lock()
            self.worker_waiting = Event()

        def __enter__(self) -> None:
            if current_thread().name.startswith("resource-worker"):
                self.worker_waiting.set()
            self.lock.acquire()

        def __exit__(self, *_exc: object) -> None:
            self.lock.release()

    ledger_lock = ObservedLock()
    monkeypatch.setattr(resource_budgets, "Lock", lambda: ledger_lock)
    cancellation = CancellationToken()
    cap = ResourceAmounts(retrieval_calls=1)
    failures: list[TaskFailure] = []

    def handler(request: Request, _context: TaskContext) -> Response:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="resource-worker") as pool:
            with ledger_lock:
                attempt = pool.submit(copy_context().run, reserve_task_resources, cap)
                assert ledger_lock.worker_waiting.wait(5)
                cancellation.cancel()
            try:
                attempt.result(timeout=5)
            except TaskInvocationError as exc:
                failures.append(exc.failure)
        return Response(text=request.text)

    service, _ = _service(tmp_path, handler)
    run = service.run(
        _workflow("a"),
        scope=SCOPE,
        idempotency_key="pending",
        policy=_policy(cap, cap),
        cancellation=cancellation,
    )

    assert failures == [TaskFailure.CANCELLED]
    assert run.result is not None
    assert run.result.tasks[0].failure is TaskFailure.CANCELLED


def test_budgets_are_opt_in_and_part_of_request_identity(tmp_path: Path) -> None:
    def handler(request: Request, _context: TaskContext) -> Response:
        reserve_task_resources(ResourceAmounts(model_tokens=1))
        return Response(text=request.text)

    service, _ = _service(tmp_path, handler)
    workflow = _workflow("a")
    zero = ResourceAmounts()
    unmetered = service.run(workflow, scope=SCOPE, idempotency_key="identity")
    assert unmetered.result is not None
    assert unmetered.result.tasks[0].status is TaskStatus.SUCCEEDED

    with pytest.raises(RunConflictError):
        service.run(workflow, scope=SCOPE, idempotency_key="identity", policy=_policy(zero, zero))
    metered = service.run(
        workflow, scope=SCOPE, idempotency_key="metered", policy=_policy(zero, zero)
    )
    replay = service.run(
        workflow, scope=SCOPE, idempotency_key="metered", policy=_policy(zero, zero)
    )
    assert metered == replay
    assert metered.result is not None
    assert metered.result.tasks[0].failure is TaskFailure.BUDGET_EXHAUSTED

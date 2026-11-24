"""Deterministic, in-process execution of a bounded advisory task graph."""

from __future__ import annotations

import hashlib
import heapq
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from contextvars import copy_context
from typing import Protocol

from pydantic import BaseModel, JsonValue

from qorvexa.agents.capabilities import Capability, CapabilityRegistry, TaskContext
from qorvexa.agents.contracts import (
    MAX_CONCURRENCY,
    ArtifactRef,
    TaskArtifact,
    TaskFailure,
    TaskResult,
    TaskSpec,
    TaskStatus,
    WorkflowResult,
    WorkflowScope,
    WorkflowSpec,
    encode_task_payload,
)
from qorvexa.auth.principal import Permission

type PlannedTask = tuple[TaskSpec, Capability]


class WorkflowValidationError(ValueError):
    """The graph or the run envelope cannot be executed as submitted."""


class TaskInvocationError(RuntimeError):
    """A trusted execution policy stopped a task with a stable failure code."""

    def __init__(self, failure: TaskFailure) -> None:
        super().__init__(failure.value)
        self.failure = failure


class TaskInvoker(Protocol):
    """Trusted execution policy boundary around a single validated task invocation."""

    def __call__(
        self,
        capability: Capability,
        context: TaskContext,
        operation: Callable[[], BaseModel],
        /,
    ) -> BaseModel: ...


def _invoke(
    capability: Capability,
    context: TaskContext,
    operation: Callable[[], BaseModel],
    /,
) -> BaseModel:
    return operation()


class Supervisor:
    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        invoker: TaskInvoker = _invoke,
        accept_result: Callable[[], None] | None = None,
        max_concurrency: int = 1,
    ) -> None:
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= MAX_CONCURRENCY:
            raise ValueError(f"Maximum concurrency must be between 1 and {MAX_CONCURRENCY}")
        self._registry = registry
        self._invoker = invoker
        self._accept_result = accept_result
        self._max_concurrency = max_concurrency

    def _plan(self, workflow: WorkflowSpec) -> tuple[PlannedTask, ...]:
        tasks: dict[str, PlannedTask] = {}
        for task in workflow.tasks:
            if task.task_id in tasks:
                raise WorkflowValidationError(f"Duplicate task ID: {task.task_id}")
            if len(set(task.dependencies)) != len(task.dependencies):
                raise WorkflowValidationError(f"Duplicate dependencies for task: {task.task_id}")
            capability = self._registry.get(task.capability_id, task.capability_version)
            if capability is None:
                raise WorkflowValidationError(f"Unknown capability for task: {task.task_id}")
            tasks[task.task_id] = (task, capability)

        successors: dict[str, list[str]] = {task_id: [] for task_id in tasks}
        remaining: dict[str, int] = {}
        for task, _ in tasks.values():
            remaining[task.task_id] = len(task.dependencies)
            for dependency in task.dependencies:
                if dependency not in tasks:
                    raise WorkflowValidationError(f"Unknown dependency for task: {task.task_id}")
                successors[dependency].append(task.task_id)

        ready = [task_id for task_id, count in remaining.items() if count == 0]
        heapq.heapify(ready)
        plan: list[PlannedTask] = []
        while ready:
            task_id = heapq.heappop(ready)
            plan.append(tasks[task_id])
            for successor in successors[task_id]:
                remaining[successor] -= 1
                if remaining[successor] == 0:
                    heapq.heappush(ready, successor)
        if len(plan) != len(tasks):
            raise WorkflowValidationError("Workflow dependencies contain a cycle")
        return tuple(plan)

    def _batches(self, plan: tuple[PlannedTask, ...]) -> Iterator[tuple[PlannedTask, ...]]:
        remaining = plan
        finished: set[str] = set()
        while remaining:
            batch: list[PlannedTask] = []
            for entry in remaining:
                task, capability = entry
                if not finished.issuperset(task.dependencies):
                    continue
                if batch and not capability.parallel_safe:
                    break
                batch.append(entry)
                if not capability.parallel_safe or len(batch) == self._max_concurrency:
                    break
            yield tuple(batch)
            completed = {task.task_id for task, _ in batch}
            finished.update(completed)
            remaining = tuple(entry for entry in remaining if entry[0].task_id not in completed)

    def validate(self, workflow: WorkflowSpec) -> None:
        """Validate the graph and all capability references before claiming a run."""
        self._plan(workflow)

    def run(
        self,
        workflow: WorkflowSpec,
        *,
        scope: WorkflowScope,
        inputs: Mapping[str, Mapping[str, JsonValue]] | None = None,
        permissions: frozenset[Permission] = frozenset(),
    ) -> WorkflowResult:
        """Run a trusted registry using scope and permissions from the calling service."""
        plan = self._plan(workflow)
        submitted = dict(inputs) if inputs is not None else {}
        if submitted.keys() - {task.task_id for task, _ in plan}:
            raise WorkflowValidationError("Input supplied for an unknown task")
        encoded: dict[str, str | None] = {}
        for task_id, payload in submitted.items():
            try:
                encoded[task_id] = encode_task_payload(payload)
            except (TypeError, ValueError, RecursionError):
                encoded[task_id] = None

        results: dict[str, TaskResult] = {}

        def run_task(entry: PlannedTask) -> TaskResult:
            task, capability = entry
            dependencies = tuple(results[task_id] for task_id in sorted(task.dependencies))
            blocked_by = tuple(d.task_id for d in dependencies if d.status != TaskStatus.SUCCEEDED)
            if blocked_by:
                return TaskResult(
                    task_id=task.task_id, status=TaskStatus.BLOCKED, blocked_by=blocked_by
                )
            context = TaskContext(
                scope=scope,
                workflow_id=workflow.workflow_id,
                workflow_version=workflow.version,
                task_id=task.task_id,
                dependencies=tuple(d.artifact for d in dependencies if d.artifact is not None),
            )
            return self._execute(capability, context, encoded.get(task.task_id, "{}"), permissions)

        executor = (
            ThreadPoolExecutor(max_workers=self._max_concurrency)
            if self._max_concurrency > 1
            else nullcontext()
        )
        with executor as pool:
            batches = self._batches(plan) if pool is not None else ((entry,) for entry in plan)
            for batch in batches:
                completed: tuple[TaskResult, ...]
                if pool is None or len(batch) == 1:
                    completed = (run_task(batch[0]),)
                else:
                    scheduled = tuple(
                        pool.submit(copy_context().run, run_task, entry) for entry in batch
                    )
                    completed = tuple(future.result() for future in scheduled)
                results.update((result.task_id, result) for result in completed)

        return WorkflowResult(
            scope=scope,
            workflow_id=workflow.workflow_id,
            version=workflow.version,
            tasks=tuple(results[task.task_id] for task, _ in plan),
        )

    def _execute(
        self,
        capability: Capability,
        context: TaskContext,
        payload: str | None,
        permissions: frozenset[Permission],
    ) -> TaskResult:
        def failure(code: TaskFailure) -> TaskResult:
            return TaskResult(task_id=context.task_id, status=TaskStatus.FAILED, failure=code)

        if not capability.required_permissions <= permissions:
            return failure(TaskFailure.PERMISSION_DENIED)
        if payload is None:
            return failure(TaskFailure.INPUT_INVALID)
        try:
            validated = capability.input_model.model_validate_json(
                payload, strict=True, extra="forbid"
            )
        except Exception:
            return failure(TaskFailure.INPUT_INVALID)

        try:
            output = self._invoker(
                capability,
                context,
                lambda: capability.handler(validated.model_copy(deep=True), context),
            )
        except TaskInvocationError as exc:
            return failure(exc.failure)
        except Exception:
            return failure(TaskFailure.HANDLER_FAILED)

        if type(output) is not capability.output_model:
            return failure(TaskFailure.OUTPUT_INVALID)
        try:
            wire = encode_task_payload(output.model_dump(mode="json", warnings="error"))
            checked = capability.output_model.model_validate_json(wire, strict=True, extra="forbid")
            serialized = encode_task_payload(checked.model_dump(mode="json", warnings="error"))
        except Exception:
            return failure(TaskFailure.OUTPUT_INVALID)

        artifact = TaskArtifact(
            ref=ArtifactRef(
                scope=context.scope,
                workflow_id=context.workflow_id,
                workflow_version=context.workflow_version,
                task_id=context.task_id,
                payload_sha256=hashlib.sha256(serialized.encode()).hexdigest(),
            ),
            payload_json=serialized,
        )
        if self._accept_result is not None:
            try:
                self._accept_result()
            except TaskInvocationError as exc:
                return failure(exc.failure)
        return TaskResult(task_id=context.task_id, status=TaskStatus.SUCCEEDED, artifact=artifact)

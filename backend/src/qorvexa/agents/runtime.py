"""Deterministic, in-process execution of a bounded advisory task graph."""

from __future__ import annotations

import hashlib
import heapq
from collections.abc import Callable, Mapping
from typing import Protocol

from pydantic import BaseModel, JsonValue

from qorvexa.agents.capabilities import Capability, CapabilityRegistry, TaskContext
from qorvexa.agents.contracts import (
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
    ) -> None:
        self._registry = registry
        self._invoker = invoker
        self._accept_result = accept_result

    def _plan(self, workflow: WorkflowSpec) -> tuple[tuple[TaskSpec, Capability], ...]:
        tasks: dict[str, tuple[TaskSpec, Capability]] = {}
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
        plan: list[tuple[TaskSpec, Capability]] = []
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
        for task, capability in plan:
            dependencies = tuple(results[task_id] for task_id in sorted(task.dependencies))
            blocked_by = tuple(d.task_id for d in dependencies if d.status != TaskStatus.SUCCEEDED)
            if blocked_by:
                results[task.task_id] = TaskResult(
                    task_id=task.task_id, status=TaskStatus.BLOCKED, blocked_by=blocked_by
                )
                continue
            context = TaskContext(
                scope=scope,
                workflow_id=workflow.workflow_id,
                workflow_version=workflow.version,
                task_id=task.task_id,
                dependencies=tuple(d.artifact for d in dependencies if d.artifact is not None),
            )
            results[task.task_id] = self._execute(
                capability, context, encoded.get(task.task_id, "{}"), permissions
            )

        return WorkflowResult(
            scope=scope,
            workflow_id=workflow.workflow_id,
            version=workflow.version,
            tasks=tuple(results.values()),
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

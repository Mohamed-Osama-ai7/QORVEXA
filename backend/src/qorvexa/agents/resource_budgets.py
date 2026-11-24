"""Cooperative resource reservations for trusted advisory capabilities."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Lock
from typing import Annotated, Final

from pydantic import ConfigDict, Field, ValidationError

from qorvexa.agents.contracts import FrozenContract, TaskFailure
from qorvexa.agents.runtime import TaskInvocationError

ResourceCount = Annotated[int, Field(ge=0, le=2_147_483_647)]
_Counts = tuple[int, int, int, int]


class ResourceAmounts(FrozenContract):
    """Integer quantities requested or limited in the supported dimensions."""

    model_config = ConfigDict(revalidate_instances="always")

    model_tokens: ResourceCount = 0
    retrieval_calls: ResourceCount = 0
    solver_units: ResourceCount = 0
    simulation_units: ResourceCount = 0

    def _counts(self) -> _Counts:
        return (self.model_tokens, self.retrieval_calls, self.solver_units, self.simulation_units)


class ResourceBudgetPolicy(FrozenContract):
    """Each configured run and logical task starts with an explicit resource ceiling."""

    run: ResourceAmounts
    task: ResourceAmounts


def _subtract(counts: _Counts, amounts: _Counts) -> _Counts:
    return (
        counts[0] - amounts[0],
        counts[1] - amounts[1],
        counts[2] - amounts[2],
        counts[3] - amounts[3],
    )


@dataclass(slots=True)
class _ResourceAttempt:
    ledger: _ResourceLedger
    task_id: str
    time_remaining: Callable[[], float]
    closed: bool = False


class _ResourceLedger:
    """One lock protects the run and every task counter, including rejected reservations."""

    def __init__(self, policy: ResourceBudgetPolicy) -> None:
        self._run_remaining = policy.run._counts()
        self._task_limit = policy.task._counts()
        self._task_remaining: dict[str, _Counts] = {}
        self._exhausted: set[str] = set()
        self._lock = Lock()

    def check(self, task_id: str) -> None:
        with self._lock:
            if task_id in self._exhausted:
                raise TaskInvocationError(TaskFailure.BUDGET_EXHAUSTED)

    def reserve(self, attempt: _ResourceAttempt, amounts: ResourceAmounts) -> None:
        with self._lock:
            task_id = attempt.task_id
            if attempt.closed or task_id in self._exhausted:
                raise TaskInvocationError(TaskFailure.BUDGET_EXHAUSTED)

            attempt.time_remaining()
            try:
                requested = ResourceAmounts.model_validate(amounts)._counts()
            except ValidationError as exc:
                self._exhausted.add(task_id)
                raise TaskInvocationError(TaskFailure.BUDGET_EXHAUSTED) from exc
            run_remaining = _subtract(self._run_remaining, requested)
            task_remaining = _subtract(
                self._task_remaining.get(task_id, self._task_limit), requested
            )
            if min(run_remaining) < 0 or min(task_remaining) < 0:
                self._exhausted.add(task_id)
                raise TaskInvocationError(TaskFailure.BUDGET_EXHAUSTED)

            self._run_remaining = run_remaining
            self._task_remaining[task_id] = task_remaining

    def close(self, attempt: _ResourceAttempt) -> None:
        with self._lock:
            attempt.closed = True


_ACTIVE_RESOURCE_ATTEMPT: Final[ContextVar[_ResourceAttempt | None]] = ContextVar(
    "advisory_resource_attempt", default=None
)


@contextmanager
def _resource_attempt(
    ledger: _ResourceLedger | None, task_id: str, time_remaining: Callable[[], float]
) -> Iterator[None]:
    attempt = _ResourceAttempt(ledger, task_id, time_remaining) if ledger is not None else None
    token = _ACTIVE_RESOURCE_ATTEMPT.set(attempt)
    try:
        yield
    finally:
        if attempt is not None:
            attempt.ledger.close(attempt)
        _ACTIVE_RESOURCE_ATTEMPT.reset(token)


def reserve_task_resources(amounts: ResourceAmounts) -> None:
    """Reserve all requested units before starting work in a budget-enabled task."""
    attempt = _ACTIVE_RESOURCE_ATTEMPT.get()
    if attempt is not None:
        attempt.ledger.reserve(attempt, amounts)

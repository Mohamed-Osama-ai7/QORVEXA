"""Actor-owned advisory runs for trusted, authorized application services."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Final

from pydantic import JsonValue, TypeAdapter

from qorvexa.agents.contracts import Identifier, WorkflowScope
from qorvexa.agents.execution import ExecutionPolicy, ExecutionService
from qorvexa.agents.routing import WorkflowRouter, WorkflowRoutingError
from qorvexa.agents.run_store import (
    RunEvent,
    RunIntegrityError,
    RunRecord,
    _ActorRunDeniedError,
    _ActorRunKey,
)
from qorvexa.agents.runtime import WorkflowValidationError
from qorvexa.auth.principal import Permission, Principal

_IDENTIFIER: Final[TypeAdapter[str]] = TypeAdapter(Identifier)
type ScopeGrant = Callable[[Principal, WorkflowScope], frozenset[Permission] | None]


class ActorRunUnavailableError(ValueError):
    """The actor cannot access this workflow run or submit the proposed request."""

    def __init__(self) -> None:
        super().__init__("Workflow run unavailable")


class ActorRunService:
    """Require fresh grants and actor ownership for every run operation."""

    def __init__(
        self,
        router: WorkflowRouter,
        execution: ExecutionService,
        grant: ScopeGrant,
        *,
        policy: ExecutionPolicy | None = None,
    ) -> None:
        self._router = router
        self._execution = execution
        self._grant = grant
        self._policy = policy if policy is not None else ExecutionPolicy()

    def _authorize(self, principal: Principal, scope: WorkflowScope) -> frozenset[Permission]:
        if (
            not isinstance(principal, Principal)
            or not isinstance(principal.actor_id, str)
            or not principal.actor_id.strip()
            or len(principal.actor_id) > 128
            or principal.tenant_id != scope.tenant_id
            or (scope.asset_id is not None and not principal.can_access_asset(scope.asset_id))
        ):
            raise ActorRunUnavailableError

        try:
            current = self._grant(principal, scope)
            if isinstance(current, frozenset) and all(isinstance(p, Permission) for p in current):
                return principal.permissions.intersection(current)
        except Exception:
            raise ActorRunUnavailableError from None
        raise ActorRunUnavailableError

    def _key(
        self,
        principal: Principal,
        scope: WorkflowScope,
        workflow_id: str,
        client_key: str,
    ) -> _ActorRunKey:
        if not isinstance(scope, WorkflowScope):
            raise ActorRunUnavailableError
        try:
            _IDENTIFIER.validate_python(workflow_id, strict=True)
            _IDENTIFIER.validate_python(client_key, strict=True)
            WorkflowScope.model_validate(scope.model_dump())
        except (AttributeError, TypeError, ValueError):
            raise ActorRunUnavailableError from None
        permissions = self._authorize(principal, scope)
        encoded = json.dumps(
            ["qorvexa/actor-run/1", principal.actor_id, scope.model_dump(), client_key],
            sort_keys=True,
            separators=(",", ":"),
        )
        return _ActorRunKey(hashlib.sha256(encoded.encode()).hexdigest(), workflow_id, permissions)

    def submit(
        self,
        principal: Principal,
        *,
        scope: WorkflowScope,
        workflow_id: str,
        client_key: str,
        arguments: Mapping[str, JsonValue],
    ) -> RunRecord:
        key = self._key(principal, scope, workflow_id, client_key)
        if not isinstance(arguments, Mapping):
            raise ActorRunUnavailableError
        try:
            prepared = self._router.prepare(
                workflow_id, scope=scope, arguments=arguments, permissions=key.permissions
            )
            current = self._authorize(principal, scope)
            if not current.issuperset(prepared.required_permissions):
                raise ActorRunUnavailableError
            execution_key = replace(
                key, permissions=current, required_permissions=prepared.required_permissions
            )
            record = self._execution.run(
                prepared.workflow,
                scope=prepared.scope,
                idempotency_key=execution_key,
                inputs=prepared.inputs(),
                permissions=prepared.required_permissions,
                policy=self._policy,
            )
        except (
            WorkflowRoutingError,
            WorkflowValidationError,
            _ActorRunDeniedError,
            RunIntegrityError,
        ):
            raise ActorRunUnavailableError from None
        if not self._authorize(principal, scope).issuperset(prepared.required_permissions):
            raise ActorRunUnavailableError
        return record

    def read(
        self, principal: Principal, *, scope: WorkflowScope, workflow_id: str, client_key: str
    ) -> RunRecord:
        key = self._key(principal, scope, workflow_id, client_key)
        try:
            record = self._execution.get(scope, key)
        except (_ActorRunDeniedError, RunIntegrityError):
            raise ActorRunUnavailableError from None
        if record is None:
            raise ActorRunUnavailableError
        return record

    def events(
        self, principal: Principal, *, scope: WorkflowScope, workflow_id: str, client_key: str
    ) -> tuple[RunEvent, ...]:
        key = self._key(principal, scope, workflow_id, client_key)
        try:
            events = self._execution.events(scope, key)
        except (_ActorRunDeniedError, RunIntegrityError):
            raise ActorRunUnavailableError from None
        if not events:
            raise ActorRunUnavailableError
        return events

    def recover_expired(
        self, principal: Principal, *, scope: WorkflowScope, workflow_id: str, client_key: str
    ) -> RunRecord:
        key = self._key(principal, scope, workflow_id, client_key)
        try:
            record = self._execution.recover_expired(scope, key)
        except (_ActorRunDeniedError, RunIntegrityError):
            raise ActorRunUnavailableError from None
        if record is None:
            raise ActorRunUnavailableError
        return record

"""Synthetic acceptance case for governed intent, actor ownership, and real planning."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from qorvexa.agents import (
    CapabilityRegistry,
    ExecutionPolicy,
    ExecutionService,
    ResourceAmounts,
    ResourceBudgetPolicy,
    RunState,
    RunStore,
    TaskStatus,
    WorkflowScope,
)
from qorvexa.agents.actor_runs import ActorRunService, ActorRunUnavailableError
from qorvexa.agents.planning_adapter import (
    VERIFIED_PLANNING_ROUTE,
    PlanningCase,
    PlanningRequest,
    VerifiedPlanningEvidence,
    verified_planning_capability,
)
from qorvexa.agents.planning_intent import prepare_planning_intent
from qorvexa.agents.routing import PreparedWorkflow, WorkflowRouter
from qorvexa.auth.principal import Permission, Principal, Role
from qorvexa.industrial.co_orchestration import CoOrchestrator
from qorvexa.solver_fabric import LPOrchestrator
from qorvexa.solver_fabric.highs_adapter import HighsAdapter
from tests.test_agent_planning_adapter import case as case


def test_planning_intent_produces_actor_owned_verified_review_evidence(
    case: PlanningCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    specialist = CoOrchestrator(LPOrchestrator(HighsAdapter()))
    run_osco = Mock(wraps=specialist.run)
    monkeypatch.setattr(specialist, "run", run_osco)
    registry = CapabilityRegistry(
        (
            verified_planning_capability(
                specialist,
                lambda scope, case_id: (
                    case if scope == case.scope and case_id == "synthetic-shift" else None
                ),
            ),
        )
    )
    router = WorkflowRouter(registry, (VERIFIED_PLANNING_ROUTE,))
    requester = Principal(
        "planner-one", case.scope.tenant_id, frozenset({Role.ENGINEER}), frozenset()
    )
    other = Principal("planner-two", case.scope.tenant_id, frozenset({Role.ENGINEER}), frozenset())
    active_grants = {requester.actor_id, other.actor_id}
    allowed = frozenset({Permission.ASSET_READ})

    def grant(principal: Principal, scope: WorkflowScope) -> frozenset[Permission] | None:
        return allowed if scope == case.scope and principal.actor_id in active_grants else None

    caps = ResourceAmounts(
        solver_units=case.config.budget.max_candidates,
        simulation_units=case.config.budget.max_candidates * case.config.simulation.replications,
    )
    service = ActorRunService(
        router,
        ExecutionService(registry, RunStore(tmp_path / "synthetic-runs.sqlite")),
        grant,
        policy=ExecutionPolicy(
            run_timeout_s=90,
            task_timeout_s=80,
            resource_budgets=ResourceBudgetPolicy(run=caps, task=caps),
        ),
    )
    workflow = VERIFIED_PLANNING_ROUTE.workflow
    prepared = prepare_planning_intent(
        router,
        json.dumps(
            {
                "candidates": [
                    {
                        "workflow_id": workflow.workflow_id,
                        "workflow_version": workflow.version,
                        "arguments": {"selection_key": "reviewed-case"},
                    }
                ]
            }
        ),
        scope=case.scope,
        permissions=allowed,
        case_bindings={
            (case.scope, "reviewed-case"): PlanningRequest(
                case_id="synthetic-shift", case_sha256=case.case_sha256
            )
        },
    )
    assert isinstance(prepared, PreparedWorkflow)
    args = prepared.inputs()[prepared.intake_task_id]
    key = "synthetic-shift-acceptance"

    first = service.submit(
        requester,
        scope=case.scope,
        workflow_id=workflow.workflow_id,
        client_key=key,
        arguments=args,
    )
    replay = service.submit(
        requester,
        scope=case.scope,
        workflow_id=workflow.workflow_id,
        client_key=key,
        arguments=args,
    )
    assert replay == first and first.state is RunState.COMPLETED and first.result is not None
    run_osco.assert_called_once()
    (task,) = first.result.tasks
    assert task.status is TaskStatus.SUCCEEDED and task.artifact is not None
    evidence = VerifiedPlanningEvidence.model_validate_json(task.artifact.payload_json)
    assert evidence.case_sha256 == case.case_sha256
    assert evidence.human_approval_required and not evidence.approved
    assert evidence.math_model_sha256 and evidence.simulation_content_sha256

    with pytest.raises(ActorRunUnavailableError):
        service.read(other, scope=case.scope, workflow_id=workflow.workflow_id, client_key=key)
    active_grants.remove(requester.actor_id)
    with pytest.raises(ActorRunUnavailableError):
        service.read(requester, scope=case.scope, workflow_id=workflow.workflow_id, client_key=key)

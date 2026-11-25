"""Run Copilot incident RCA through its fixed, asset-scoped advisory workflow."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Final, cast

import pandas as pd
from pydantic import Field

from qorvexa.agents import (
    Capability,
    CapabilityRegistry,
    TaskContext,
    TaskSpec,
    TaskStatus,
    WorkflowScope,
    WorkflowSpec,
)
from qorvexa.agents.contracts import FrozenContract
from qorvexa.agents.routing import RouteScope, WorkflowRoute, WorkflowRouter
from qorvexa.anomaly.evidence_package import build_event_evidence_package
from qorvexa.anomaly.service import AnomalyService, DataUnavailableError, ModelUnavailableError
from qorvexa.auth.principal import Permission, Principal
from qorvexa.contracts.events import (
    EventEvidencePackage,
    EvidenceQualityStatus,
    HumanObservationCategory,
    HumanObservationOut,
)
from qorvexa.contracts.rca import (
    RcaAssessment,
    RcaConfidenceBand,
    RcaHypothesisCategory,
    RelativeSupport,
)
from qorvexa.db.models import HumanObservation, Incident

_TASK: Final = "incident-rca"
_WORKFLOW: Final = WorkflowSpec(
    workflow_id="copilot-incident-rca",
    version="1",
    tasks=(TaskSpec(task_id=_TASK, capability_id=_TASK, capability_version="1"),),
)
_INCIDENT_ROUTE: Final = WorkflowRoute(_WORKFLOW, RouteScope.ASSET, _TASK)


class _IncidentLookup(FrozenContract):
    incident_id: str = Field(min_length=1, max_length=32)


class _HypothesisSummary(FrozenContract):
    category: RcaHypothesisCategory
    relative_support: RelativeSupport
    support_score: float = Field(ge=0.0, le=1.0)
    confidence_band: RcaConfidenceBand


class IncidentRcaEvidence(FrozenContract):
    """The assessment identities and evidence summary consumed by Copilot."""

    rca_id: str
    incident_id: str
    asset_id: str
    package_id: str
    abstained: bool
    abstention_reason: str | None
    primary_category: RcaHypothesisCategory
    primary_title: str
    summary: str
    uncertainty_note: str
    quality_status: EvidenceQualityStatus
    ranked_hypotheses: tuple[_HypothesisSummary, ...]


class _IncidentAssessment(FrozenContract):
    evidence: IncidentRcaEvidence | None = None


def _observation_out(observation: HumanObservation) -> HumanObservationOut:
    return HumanObservationOut(
        id=observation.id,
        incident_id=observation.incident_id,
        asset_id=observation.asset_id,
        actor_id=observation.actor_id,
        category=cast(HumanObservationCategory, observation.category),
        observed_at=observation.observed_at,
        note=observation.note,
        created_at=observation.created_at,
    )


def evaluate_incident_rca(
    principal: Principal,
    *,
    asset_id: str,
    incident: Incident,
    observations: Sequence[HumanObservation],
    resolve_service: Callable[[str], AnomalyService | None],
    rca_fn: Callable[[EventEvidencePackage], RcaAssessment],
) -> IncidentRcaEvidence | None:
    """Evaluate the repository-selected incident with the caller's verified scope."""
    if (
        not principal.can(Permission.INCIDENT_READ)
        or not principal.can_access_asset(asset_id)
        or incident.tenant_id != principal.tenant_id
        or incident.asset_id != asset_id
    ):
        return None

    def assess(request: _IncidentLookup, context: TaskContext) -> _IncidentAssessment:
        if (
            request.incident_id != incident.id
            or context.scope.tenant_id != incident.tenant_id
            or context.scope.asset_id != incident.asset_id
            or any(
                (obs.tenant_id, obs.asset_id, obs.incident_id)
                != (incident.tenant_id, incident.asset_id, incident.id)
                for obs in observations
            )
        ):
            return _IncidentAssessment()

        service = resolve_service(incident.asset_id)
        if service is None or incident.episode_end <= incident.episode_start:
            return _IncidentAssessment()
        observations_out = [_observation_out(obs) for obs in observations]
        try:
            package = build_event_evidence_package(
                service,
                asset_id=incident.asset_id,
                event_start=pd.Timestamp(incident.episode_start),
                event_end=pd.Timestamp(incident.episode_end),
                raised_at=pd.Timestamp(incident.raised_at),
                incident_id=incident.id,
                human_observations=observations_out,
            )
        except (DataUnavailableError, ModelUnavailableError, ValueError):
            return _IncidentAssessment()
        if (
            not package.package_id
            or package.incident_id != incident.id
            or package.asset_id != incident.asset_id
            or package.event_start != incident.episode_start
            or package.event_end != incident.episode_end
            or package.raised_at != incident.raised_at
        ):
            return _IncidentAssessment()

        package_id = package.package_id
        assessment = rca_fn(package)
        if (
            assessment.incident_id != incident.id
            or assessment.asset_id != incident.asset_id
            or assessment.package_id != package_id
            or assessment.event_start != incident.episode_start
            or assessment.event_end != incident.episode_end
        ):
            return _IncidentAssessment()

        return _IncidentAssessment(
            evidence=IncidentRcaEvidence(
                rca_id=assessment.rca_id,
                incident_id=incident.id,
                asset_id=incident.asset_id,
                package_id=package_id,
                abstained=assessment.abstained,
                abstention_reason=assessment.abstention_reason,
                primary_category=assessment.primary_category,
                primary_title=assessment.primary_title,
                summary=assessment.summary,
                uncertainty_note=assessment.uncertainty_note,
                quality_status=assessment.quality_status,
                ranked_hypotheses=tuple(
                    _HypothesisSummary(
                        category=hypothesis.category,
                        relative_support=hypothesis.relative_support,
                        support_score=hypothesis.support_score,
                        confidence_band=hypothesis.confidence_band,
                    )
                    for hypothesis in assessment.ranked_hypotheses
                ),
            )
        )

    capability = Capability.register(
        _TASK,
        "1",
        input_model=_IncidentLookup,
        output_model=_IncidentAssessment,
        handler=assess,
        required_permissions=frozenset({Permission.INCIDENT_READ}),
    )
    router = WorkflowRouter(CapabilityRegistry((capability,)), (_INCIDENT_ROUTE,))
    run = router.run(
        _WORKFLOW.workflow_id,
        scope=WorkflowScope(tenant_id=principal.tenant_id, asset_id=asset_id),
        arguments={"incident_id": incident.id},
        permissions=principal.permissions,
    )
    task = run.tasks[0]
    if task.status is not TaskStatus.SUCCEEDED or task.artifact is None:
        raise RuntimeError("Copilot incident RCA workflow failed")
    return _IncidentAssessment.model_validate_json(task.artifact.payload_json, strict=True).evidence

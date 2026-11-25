"""The Copilot only uses RCA evidence from the authorized incident assessment."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import JsonValue

from qorvexa.agents import TaskStatus, WorkflowResult, WorkflowScope
from qorvexa.agents.routing import WorkflowRouter
from qorvexa.anomaly.rca import analyze_event_evidence
from qorvexa.anomaly.service import AnomalyService
from qorvexa.auth.principal import Permission, Principal, Role
from qorvexa.contracts.copilot import CopilotQueryRequest, CopilotQueryResponse
from qorvexa.contracts.events import EventEvidencePackage
from qorvexa.contracts.rca import RcaAssessment
from qorvexa.copilot import incident_workflow
from qorvexa.copilot.service import ControlledCopilot
from qorvexa.db.session import Database
from qorvexa.knowledge.rag import HybridRagIndex
from tests.auth_helpers import ASSET
from tests.test_copilot import _seed_incident, _test_twin
from tests.test_evidence_foundation import _make_wp_a_service

ENGINEER = Principal("engineer", "acme", frozenset({Role.ENGINEER}), frozenset({ASSET}))


@pytest.fixture(scope="module")
def real_anomaly(tmp_path_factory: pytest.TempPathFactory) -> AnomalyService:
    service, _, _ = _make_wp_a_service(tmp_path_factory.mktemp("copilot-scoped-rca"))
    return service


@pytest.fixture
def incident(tmp_path: Path) -> tuple[Database, CopilotQueryRequest]:
    db = Database(f"sqlite:///{(tmp_path / 'incident.sqlite3').as_posix()}")
    incident_id = _seed_incident(db, ENGINEER, datetime(2020, 1, 6, 6), datetime(2020, 1, 6, 9))
    return db, CopilotQueryRequest(
        question="What caused this incident?",
        asset_id=ASSET,
        incident_id=incident_id,
    )


@pytest.fixture
def rca_runs(monkeypatch: pytest.MonkeyPatch) -> list[WorkflowResult]:
    runs: list[WorkflowResult] = []
    run = WorkflowRouter.run

    def record(
        router: WorkflowRouter,
        workflow_id: str,
        *,
        scope: WorkflowScope,
        arguments: Mapping[str, JsonValue],
        permissions: frozenset[Permission],
    ) -> WorkflowResult:
        result = run(router, workflow_id, scope=scope, arguments=arguments, permissions=permissions)
        if workflow_id == "copilot-incident-rca":
            runs.append(result)
        return result

    monkeypatch.setattr(WorkflowRouter, "run", record)
    return runs


def _assert_rca_unavailable(response: CopilotQueryResponse) -> None:
    assert response.abstained
    assert response.abstention_reason == "rca_telemetry_unavailable_insufficient_evidence"
    rca_invocation = next(t for t in response.tools_executed if t.tool == "get_rca_hypotheses")
    assert rca_invocation.permitted and rca_invocation.status == "unavailable"
    assert rca_invocation.record_count == 0
    assert any(c.source_type == "incident_evidence" for c in response.citations)
    assert all(c.source_type != "rca" for c in response.citations)
    optimizer = next(c for c in response.citations if c.source_type == "maintenance_optimizer")
    assert "dominant_hypothesis=insufficient_evidence" in optimizer.detail


def test_real_incident_rca_remains_grounded_in_the_normal_copilot_path(
    real_anomaly: AnomalyService,
    incident: tuple[Database, CopilotQueryRequest],
    rca_runs: list[WorkflowResult],
) -> None:
    db, request = incident
    specialist = Mock(wraps=analyze_event_evidence)
    copilot = ControlledCopilot(
        knowledge_index=HybridRagIndex([]),
        twin=_test_twin(),
        anomaly_service=real_anomaly,
        rca_fn=specialist,
    )

    result = copilot.run_query(request, principal=ENGINEER, session_factory=db.session)

    specialist.assert_called_once()
    package = specialist.call_args.args[0]
    assert package.asset_id == request.asset_id and package.incident_id == request.incident_id
    (run,) = rca_runs
    (task,) = run.tasks
    assert task.status is TaskStatus.SUCCEEDED and task.artifact is not None
    assert task.artifact.ref.scope == WorkflowScope(tenant_id=ENGINEER.tenant_id, asset_id=ASSET)
    evidence = task.artifact.read()["evidence"]
    assert isinstance(evidence, dict)
    assert evidence["incident_id"] == request.incident_id
    assert not result.abstained
    rca_tool = next(t for t in result.tools_executed if t.tool == "get_rca_hypotheses")
    assert rca_tool.status == "executed" and rca_tool.record_count > 0
    rca = next(c for c in result.citations if c.source_type == "rca")
    assert "air_leakage" in rca.title
    assert f"[{rca.citation_id}]" in result.answer
    optimizer = next(c for c in result.citations if c.source_type == "maintenance_optimizer")
    assert "dominant_hypothesis=air_leakage" in optimizer.detail


@pytest.mark.parametrize(
    ("field", "foreign_value"),
    [("asset_id", "another-asset"), ("incident_id", "f" * 32), ("package_id", "another-package")],
)
def test_report_for_a_different_incident_asset_or_package_cannot_ground_an_answer(
    real_anomaly: AnomalyService,
    incident: tuple[Database, CopilotQueryRequest],
    field: str,
    foreign_value: str,
    rca_runs: list[WorkflowResult],
) -> None:
    db, request = incident

    def wrong_report(package: EventEvidencePackage) -> RcaAssessment:
        report = analyze_event_evidence(package)
        assert not report.abstained
        if field == "package_id":
            package.package_id = foreign_value
        return report.model_copy(
            update={field: foreign_value, "summary": "Foreign evidence should never appear here."}
        )

    specialist = Mock(wraps=wrong_report)
    copilot = ControlledCopilot(
        knowledge_index=HybridRagIndex([]),
        twin=_test_twin(),
        anomaly_service=real_anomaly,
        rca_fn=specialist,
    )

    result = copilot.run_query(request, principal=ENGINEER, session_factory=db.session)

    specialist.assert_called_once()
    (run,) = rca_runs
    (task,) = run.tasks
    assert task.status is TaskStatus.SUCCEEDED and task.artifact is not None
    assert task.artifact.read() == {"evidence": None}
    _assert_rca_unavailable(result)
    assert "Foreign evidence should never appear here." not in result.model_dump_json()


@pytest.mark.parametrize(
    "principal",
    [
        Principal("no-asset", "acme", frozenset({Role.ENGINEER}), frozenset({"other-asset"})),
        Principal("no-role", "acme", frozenset(), frozenset({ASSET})),
    ],
)
def test_unauthorized_grants_do_not_load_the_incident_or_call_the_specialist(
    incident: tuple[Database, CopilotQueryRequest], principal: Principal
) -> None:
    db, request = incident
    session_factory = Mock(wraps=db.session)
    resolve_service = Mock()
    specialist = Mock()
    copilot = ControlledCopilot(
        knowledge_index=HybridRagIndex([]),
        anomaly_service_factory=resolve_service,
        rca_fn=specialist,
    )

    result = copilot.run_query(request, principal=principal, session_factory=session_factory)

    assert result.abstained and result.citations == []
    incident_tools = [t for t in result.tools_executed if t.tool.startswith("get_")]
    assert {t.tool for t in incident_tools} == {"get_event_evidence", "get_rca_hypotheses"}
    assert all(not t.permitted and t.status == "denied_permission" for t in incident_tools)
    session_factory.assert_not_called()
    resolve_service.assert_not_called()
    specialist.assert_not_called()


def test_missing_telemetry_preserves_existing_abstention(
    incident: tuple[Database, CopilotQueryRequest],
) -> None:
    db, request = incident
    resolve_service = Mock(return_value=None)
    specialist = Mock()
    copilot = ControlledCopilot(
        knowledge_index=HybridRagIndex([]),
        twin=_test_twin(),
        anomaly_service_factory=resolve_service,
        rca_fn=specialist,
    )

    result = copilot.run_query(request, principal=ENGINEER, session_factory=db.session)

    resolve_service.assert_called_once_with(ASSET)
    specialist.assert_not_called()
    _assert_rca_unavailable(result)


def test_event_package_for_another_window_never_reaches_the_specialist(
    real_anomaly: AnomalyService,
    incident: tuple[Database, CopilotQueryRequest],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, request = incident
    build = incident_workflow.build_event_evidence_package

    def mismatched_window(*args, **kwargs) -> EventEvidencePackage:
        return build(*args, **kwargs).model_copy(update={"event_start": datetime(2020, 1, 6, 5)})

    monkeypatch.setattr(incident_workflow, "build_event_evidence_package", mismatched_window)
    specialist = Mock(wraps=analyze_event_evidence)
    copilot = ControlledCopilot(
        knowledge_index=HybridRagIndex([]),
        twin=_test_twin(),
        anomaly_service=real_anomaly,
        rca_fn=specialist,
    )

    result = copilot.run_query(request, principal=ENGINEER, session_factory=db.session)

    specialist.assert_not_called()
    _assert_rca_unavailable(result)


def test_unexpected_specialist_failure_does_not_echo_exception_details(
    real_anomaly: AnomalyService, incident: tuple[Database, CopilotQueryRequest]
) -> None:
    db, request = incident
    specialist = Mock(side_effect=ValueError("private diagnostic input"))
    copilot = ControlledCopilot(
        knowledge_index=HybridRagIndex([]),
        anomaly_service=real_anomaly,
        rca_fn=specialist,
    )

    with pytest.raises(RuntimeError, match=r"^Copilot incident RCA workflow failed$"):
        copilot.run_query(request, principal=ENGINEER, session_factory=db.session)
    specialist.assert_called_once()

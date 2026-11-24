"""Exercise the verified planning capability through routing and durable execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import BaseModel, JsonValue

from qorvexa.agents import (
    CancellationToken,
    Capability,
    CapabilityRegistry,
    ExecutionPolicy,
    ExecutionService,
    ResourceAmounts,
    ResourceBudgetPolicy,
    RunEventKind,
    RunRecord,
    RunState,
    RunStore,
    TaskArtifact,
    TaskContext,
    TaskFailure,
    TaskSpec,
    TaskStatus,
    WorkflowScope,
)
from qorvexa.agents.planning_adapter import (
    VERIFIED_PLANNING_ROUTE,
    PlanningCase,
    VerifiedPlanningEvidence,
    verified_planning_capability,
)
from qorvexa.agents.routing import (
    RoutingFailure,
    WorkflowRouter,
    WorkflowRoutingError,
)
from qorvexa.auth.principal import Permission
from qorvexa.industrial.co_orchestration import (
    Budget,
    CandidateStatus,
    CoOrchestrator,
    DecisionPackage,
    ExperimentConfig,
    SimulationConfig,
    verify_package,
)
from qorvexa.industrial.co_orchestration import evaluate as osco_evaluate
from qorvexa.industrial.digital_twin import FactoryModel
from qorvexa.industrial.digital_twin.kernel import CancelToken
from qorvexa.industrial.production_planning import PlanningProblem
from qorvexa.solver_fabric import LPOrchestrator
from qorvexa.solver_fabric.highs_adapter import HighsAdapter
from tests.test_agent_admission_integration import MismatchedDigestSolver
from tests.test_agent_execution import Clock
from tests.test_optimization_simulation_orchestration import CLOCK, config, problem, twin

SCOPE = WorkflowScope(tenant_id="synthetic-tenant", factory_id="F1")
CASE_ID = "synthetic-shift"
WORKFLOW_ID = VERIFIED_PLANNING_ROUTE.workflow.workflow_id
PLANNING_TASK = VERIFIED_PLANNING_ROUTE.intake_task_id
PERMISSIONS = frozenset({Permission.ASSET_READ})
EXACT_COST = ResourceAmounts(solver_units=3, simulation_units=6)


class Empty(BaseModel):
    pass


class ReviewReceipt(BaseModel):
    evidence_sha256: str


@dataclass(slots=True)
class Harness:
    router: WorkflowRouter
    execution: ExecutionService
    store: RunStore
    reviewed: list[TaskArtifact]

    def submit(
        self,
        case: PlanningCase,
        *,
        scope: WorkflowScope = SCOPE,
        budget: ResourceAmounts = EXACT_COST,
        cancellation: CancellationToken | None = None,
        case_sha256: str | None = None,
    ) -> RunRecord:
        prepared = self.router.prepare(
            WORKFLOW_ID,
            scope=scope,
            arguments={"case_id": CASE_ID, "case_sha256": case_sha256 or case.case_sha256},
            permissions=PERMISSIONS,
        )
        return self.execution.run(
            prepared.workflow,
            scope=prepared.scope,
            inputs=prepared.inputs(),
            permissions=prepared.permissions,
            idempotency_key="verified-plan",
            policy=ExecutionPolicy(
                run_timeout_s=40,
                task_timeout_s=30,
                resource_budgets=ResourceBudgetPolicy(run=budget, task=budget),
            ),
            cancellation=cancellation,
        )


@pytest.fixture
def case() -> PlanningCase:
    return PlanningCase(
        scope=SCOPE,
        problem=problem(weld_h="7.5"),
        twin=twin(),
        config=config(
            budget=Budget(max_candidates=3, max_workers=2, wall_clock_s=120),
            simulation=SimulationConfig(replications=2, timeout_s=60),
            refine_on_bottleneck=False,
        ),
    )


@pytest.fixture
def specialist() -> CoOrchestrator:
    return CoOrchestrator(LPOrchestrator(HighsAdapter()), clock=CLOCK)


def _application(
    tmp_path: Path,
    specialist: CoOrchestrator,
    resolve_case: Callable[[WorkflowScope, str], PlanningCase | None],
) -> Harness:
    reviewed: list[TaskArtifact] = []

    def review(_request: Empty, context: TaskContext) -> ReviewReceipt:
        artifact = context.dependency(PLANNING_TASK)
        assert artifact.ref.scope == context.scope
        VerifiedPlanningEvidence.model_validate(artifact.read(), strict=True, extra="forbid")
        reviewed.append(artifact)
        return ReviewReceipt(evidence_sha256=artifact.ref.payload_sha256)

    planning = verified_planning_capability(specialist, resolve_case)
    registry = CapabilityRegistry(
        (
            planning,
            Capability.register(
                "review-verified-evidence",
                "1",
                input_model=Empty,
                output_model=ReviewReceipt,
                handler=review,
            ),
        )
    )
    template = VERIFIED_PLANNING_ROUTE.workflow
    review_task = TaskSpec(
        task_id="review",
        capability_id="review-verified-evidence",
        capability_version="1",
        dependencies=(PLANNING_TASK,),
    )
    route = replace(
        VERIFIED_PLANNING_ROUTE,
        workflow=template.model_copy(update={"tasks": (*template.tasks, review_task)}),
    )
    store = RunStore(tmp_path / "verified-planning.sqlite")
    clock = Clock()
    return Harness(
        WorkflowRouter(registry, (route,)),
        ExecutionService(registry, store, clock=clock.monotonic, wall_clock=clock.wall),
        store,
        reviewed,
    )


def _assert_denied(run: RunRecord, failure: TaskFailure) -> None:
    assert run.state is RunState.COMPLETED and run.result is not None
    planning, review = run.result.tasks
    assert planning.status is TaskStatus.FAILED and planning.failure is failure
    assert planning.artifact is None
    assert review.status is TaskStatus.BLOCKED and review.blocked_by == (PLANNING_TASK,)
    assert review.artifact is None


def test_real_highs_and_des_publish_reviewable_evidence_from_the_approved_route(
    case: PlanningCase,
    specialist: CoOrchestrator,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolution: list[tuple[WorkflowScope, str]] = []
    packages: list[DecisionPackage] = []
    run_osco = specialist.run

    def resolve(scope: WorkflowScope, case_id: str) -> PlanningCase:
        resolution.append((scope, case_id))
        return case

    def record_package(
        base_problem: PlanningProblem,
        model: FactoryModel,
        cfg: ExperimentConfig,
        *,
        cancel: CancelToken | None = None,
    ) -> DecisionPackage:
        assert model.factory_id == SCOPE.factory_id
        assert cfg.budget.max_workers == 1
        assert cfg.budget.wall_clock_s <= 30
        assert cfg.simulation.timeout_s <= 30
        assert cancel is not None and not cancel.is_set()
        package = run_osco(base_problem, model, cfg, cancel=cancel)
        packages.append(package)
        return package

    monkeypatch.setattr(specialist, "run", record_package)
    harness = _application(tmp_path, specialist, resolve)
    run = harness.submit(case)
    assert resolution == [(SCOPE, CASE_ID)]
    assert run.result is not None and run.result.scope == SCOPE
    assert run.result.workflow_id == WORKFLOW_ID
    planning, review = run.result.tasks
    assert planning.status is review.status is TaskStatus.SUCCEEDED
    assert planning.artifact is not None and review.artifact is not None
    assert harness.reviewed == [planning.artifact]
    assert review.artifact.read() == {"evidence_sha256": planning.artifact.ref.payload_sha256}

    (package,) = packages
    assert verify_package(package)
    candidate = next(
        c for c in package.candidates if c.candidate_id == package.recommendation.candidate_id
    )
    assert candidate.math is not None and candidate.math.verified
    assert candidate.math.admission_status == "ADMITTED_UNITS_VERIFIED"
    assert candidate.sim is not None and candidate.sim.replications == 2
    evidence = VerifiedPlanningEvidence.model_validate_json(
        planning.artifact.payload_json, strict=True, extra="forbid"
    )
    assert evidence.human_approval_required and not evidence.approved
    assert evidence.case_id == CASE_ID and evidence.case_sha256 == case.case_sha256
    assert evidence.experiment_id == package.experiment_id
    assert evidence.package_sha256 == package.package_sha256
    assert evidence.effective_config_sha256 == package.config_sha256
    assert evidence.candidate_id == candidate.candidate_id
    assert evidence.problem_sha256 == candidate.problem_sha256
    assert evidence.twin_model_sha256 == candidate.twin_model_sha256
    assert evidence.math_model_sha256 == candidate.math.model_sha256
    assert evidence.admission_sha256 == candidate.math.admission_sha256
    assert evidence.unit_report_sha256 == candidate.math.unit_report_sha256
    assert evidence.simulation_id == candidate.sim.simulation_id
    assert evidence.simulation_report_sha256 == candidate.sim.report_sha256
    assert evidence.simulation_content_sha256 == candidate.sim.content_sha256
    assert evidence.simulation_replications == 2
    assert evidence.simulation_kind == candidate.sim.kind == "DETERMINISTIC_SIMULATION"


@pytest.mark.parametrize(
    "budget",
    [
        ResourceAmounts(solver_units=2, simulation_units=6),
        ResourceAmounts(solver_units=3, simulation_units=5),
    ],
)
def test_conservative_reservation_fails_before_calling_osco(
    case: PlanningCase,
    specialist: CoOrchestrator,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    budget: ResourceAmounts,
) -> None:
    run_osco = Mock(wraps=specialist.run)
    monkeypatch.setattr(specialist, "run", run_osco)
    harness = _application(tmp_path, specialist, lambda _scope, _id: case)

    _assert_denied(harness.submit(case, budget=budget), TaskFailure.BUDGET_EXHAUSTED)
    run_osco.assert_not_called()
    assert harness.reviewed == []


@pytest.mark.parametrize(
    ("scope", "digest"),
    [
        (WorkflowScope(tenant_id="other-tenant", factory_id="F1"), None),
        (WorkflowScope(tenant_id="synthetic-tenant", factory_id="other-factory"), None),
        (SCOPE, "0" * 64),
    ],
)
def test_resolved_case_must_match_trusted_factory_scope_and_reviewed_digest(
    case: PlanningCase,
    specialist: CoOrchestrator,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scope: WorkflowScope,
    digest: str | None,
) -> None:
    run_osco = Mock(wraps=specialist.run)
    monkeypatch.setattr(specialist, "run", run_osco)
    harness = _application(tmp_path, specialist, lambda _scope, _id: case)

    _assert_denied(
        harness.submit(case, scope=scope, case_sha256=digest), TaskFailure.HANDLER_FAILED
    )
    run_osco.assert_not_called()
    assert harness.reviewed == []


def test_route_rejects_request_owned_scope_and_unauthorized_callers(
    case: PlanningCase,
    specialist: CoOrchestrator,
    tmp_path: Path,
) -> None:
    resolve = Mock(return_value=case)
    harness = _application(tmp_path, specialist, resolve)
    arguments: dict[str, JsonValue] = {"case_id": CASE_ID, "case_sha256": case.case_sha256}

    cases = (
        (SCOPE, arguments, frozenset(), RoutingFailure.PERMISSION_DENIED),
        (
            WorkflowScope(tenant_id=SCOPE.tenant_id, asset_id="compressor"),
            arguments,
            PERMISSIONS,
            RoutingFailure.WRONG_SCOPE,
        ),
        (SCOPE, {**arguments, "factory_id": "other"}, PERMISSIONS, RoutingFailure.INVALID_INTAKE),
    )
    for scope, intake, permissions, failure in cases:
        with pytest.raises(WorkflowRoutingError) as error:
            harness.router.prepare(
                WORKFLOW_ID, scope=scope, arguments=intake, permissions=permissions
            )
        assert error.value.failure is failure
    resolve.assert_not_called()


@pytest.mark.parametrize("altered_experiment", [False, True])
def test_unverifiable_package_blocks_review_and_has_no_audited_artifact(
    case: PlanningCase,
    specialist: CoOrchestrator,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    altered_experiment: bool,
) -> None:
    run_osco = specialist.run

    def tamper(
        base_problem: PlanningProblem,
        model: FactoryModel,
        cfg: ExperimentConfig,
        *,
        cancel: CancelToken | None = None,
    ) -> DecisionPackage:
        if altered_experiment:
            cfg = cfg.model_copy(update={"experiment_label": "another experiment"})
        package = run_osco(base_problem, model, cfg, cancel=cancel)
        assert verify_package(package)
        if altered_experiment:
            return package
        return package.model_copy(update={"package_sha256": "0" * 64})

    monkeypatch.setattr(specialist, "run", tamper)
    harness = _application(tmp_path, specialist, lambda _scope, _id: case)
    result = harness.submit(case)

    _assert_denied(result, TaskFailure.HANDLER_FAILED)
    assert harness.reviewed == []
    events = harness.store.events(SCOPE, "verified-plan")
    assert [event.kind for event in events] == [
        RunEventKind.CLAIMED,
        RunEventKind.ATTEMPT_STARTED,
        RunEventKind.ATTEMPT_FAILED,
        RunEventKind.COMPLETED,
    ]


@pytest.mark.parametrize(
    ("unverified_math", "expected_status"),
    [
        (True, CandidateStatus.NOT_VERIFIED),
        (False, CandidateStatus.SIMULATION_INCOMPLETE),
    ],
)
def test_real_lp_and_des_failures_never_publish_evidence(
    case: PlanningCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unverified_math: bool,
    expected_status: CandidateStatus,
) -> None:
    solver = (
        MismatchedDigestSolver(HighsAdapter())
        if unverified_math
        else LPOrchestrator(HighsAdapter())
    )
    specialist = CoOrchestrator(solver, clock=CLOCK)
    run_osco = specialist.run
    packages: list[DecisionPackage] = []
    simulate = Mock(wraps=osco_evaluate.simulate)
    monkeypatch.setattr(osco_evaluate, "simulate", simulate)
    if not unverified_math:
        simulation = case.config.simulation.model_copy(update={"max_events": 1})
        case = replace(case, config=case.config.model_copy(update={"simulation": simulation}))

    def capture(
        base_problem: PlanningProblem,
        model: FactoryModel,
        cfg: ExperimentConfig,
        *,
        cancel: CancelToken | None = None,
    ) -> DecisionPackage:
        package = run_osco(base_problem, model, cfg, cancel=cancel)
        packages.append(package)
        return package

    monkeypatch.setattr(specialist, "run", capture)
    harness = _application(tmp_path, specialist, lambda _scope, _id: case)

    _assert_denied(harness.submit(case), TaskFailure.HANDLER_FAILED)
    assert harness.reviewed == []
    (package,) = packages
    (candidate,) = package.candidates
    assert verify_package(package)
    assert candidate.status is expected_status and candidate.sim is None
    if unverified_math:
        simulate.assert_not_called()
    else:
        assert candidate.math is not None and candidate.math.verified
        simulate.assert_called_once()


@pytest.mark.parametrize("cancel_during_resolution", [True, False])
def test_cancellation_propagates_to_the_specialist(
    case: PlanningCase,
    specialist: CoOrchestrator,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cancel_during_resolution: bool,
) -> None:
    cancellation = CancellationToken()
    real_run = specialist.run

    def stop_at_entry(
        base_problem: PlanningProblem,
        model: FactoryModel,
        cfg: ExperimentConfig,
        *,
        cancel: CancelToken | None = None,
    ) -> DecisionPackage:
        assert cancel is not None and not cancel.is_set()
        cancellation.cancel()
        assert cancel.is_set()
        return real_run(base_problem, model, cfg, cancel=cancel)

    run_osco = Mock(wraps=stop_at_entry)
    monkeypatch.setattr(specialist, "run", run_osco)

    def resolve(_scope: WorkflowScope, _case_id: str) -> PlanningCase:
        if cancel_during_resolution:
            cancellation.cancel()
        return case

    harness = _application(tmp_path, specialist, resolve)
    _assert_denied(harness.submit(case, cancellation=cancellation), TaskFailure.CANCELLED)
    if cancel_during_resolution:
        run_osco.assert_not_called()
    else:
        run_osco.assert_called_once()
    assert harness.reviewed == []

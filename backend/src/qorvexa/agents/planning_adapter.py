"""Factory-scoped production-planning evidence for trusted advisory workflows."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal

from pydantic import TypeAdapter

from qorvexa.agents.capabilities import Capability, TaskContext
from qorvexa.agents.contracts import (
    Digest,
    FrozenContract,
    Identifier,
    TaskFailure,
    TaskSpec,
    WorkflowScope,
    WorkflowSpec,
)
from qorvexa.agents.execution import task_checkpoint, task_remaining_seconds
from qorvexa.agents.resource_budgets import ResourceAmounts, reserve_task_resources
from qorvexa.agents.routing import RouteScope, WorkflowRoute
from qorvexa.agents.runtime import TaskInvocationError
from qorvexa.auth.principal import Permission
from qorvexa.industrial.co_orchestration.contracts import (
    ENGINE_VERSION,
    OSCO_SCHEMA,
    CandidateStatus,
    DecisionPackage,
    ExperimentConfig,
    RecommendationStatus,
    StopReason,
)
from qorvexa.industrial.co_orchestration.engine import CoOrchestrator, experiment_id
from qorvexa.industrial.co_orchestration.evaluate import twin_for
from qorvexa.industrial.co_orchestration.optimize import derive_problem, problem_sha256
from qorvexa.industrial.co_orchestration.store import verify_package
from qorvexa.industrial.digital_twin import FactoryModel, model_sha256
from qorvexa.industrial.digital_twin.report import ReportStatus as SimulationStatus
from qorvexa.industrial.production_planning import PlanningProblem
from qorvexa.industrial.production_planning.admission import AdmissionStatus
from qorvexa.industrial.production_planning.report import ReportStatus as MathStatus

_CAPABILITY: Final = "verified-production-planning"
_VERSION: Final = "1"
_TASK: Final = "verified-planning"
SimulationKind = Literal["DETERMINISTIC_SIMULATION", "STOCHASTIC_ESTIMATE"]
_SIMULATION_KIND: Final[TypeAdapter[SimulationKind]] = TypeAdapter(SimulationKind)

VERIFIED_PLANNING_ROUTE: Final = WorkflowRoute(
    workflow=WorkflowSpec(
        workflow_id=_CAPABILITY,
        version=_VERSION,
        tasks=(TaskSpec(task_id=_TASK, capability_id=_CAPABILITY, capability_version=_VERSION),),
    ),
    scope=RouteScope.FACTORY,
    intake_task_id=_TASK,
)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PlanningCase:
    """A server-owned planning problem, twin, and experiment configuration."""

    scope: WorkflowScope
    problem: PlanningProblem
    twin: FactoryModel
    config: ExperimentConfig

    @property
    def case_sha256(self) -> str:
        return _digest(
            [
                self.scope.model_dump(mode="json"),
                problem_sha256(self.problem),
                model_sha256(self.twin),
                self.config.model_dump(mode="json"),
            ]
        )


class PlanningRequest(FrozenContract):
    case_id: Identifier
    case_sha256: Digest


class VerifiedPlanningEvidence(FrozenContract):
    case_id: Identifier
    case_sha256: Digest
    experiment_id: Identifier
    package_sha256: Digest
    effective_config_sha256: Digest
    candidate_id: Identifier
    problem_sha256: Digest
    twin_model_sha256: Digest
    math_model_sha256: Digest
    admission_sha256: Digest
    unit_report_sha256: Digest
    simulation_id: Identifier
    simulation_report_sha256: Digest
    simulation_content_sha256: Digest
    simulation_kind: SimulationKind
    simulation_replications: int
    human_approval_required: Literal[True] = True
    approved: Literal[False] = False


class PlanningEvidenceError(ValueError):
    """An OSCO experiment could not supply reviewable evidence for the pinned case."""


@dataclass(frozen=True, slots=True)
class _TaskCancellation:
    deadline: float

    def checkpoint(self) -> None:
        task_checkpoint()
        if time.monotonic() >= self.deadline:
            raise TaskInvocationError(TaskFailure.TIMED_OUT)

    def is_set(self) -> bool:
        try:
            self.checkpoint()
        except TaskInvocationError:
            return True
        return False


def _bounded_config(config: ExperimentConfig, remaining: float | None) -> ExperimentConfig:
    wall = config.budget.wall_clock_s
    if remaining is not None:
        wall = min(wall, remaining)
    return config.model_copy(
        update={
            "budget": config.budget.model_copy(update={"wall_clock_s": wall, "max_workers": 1}),
            "simulation": config.simulation.model_copy(
                update={"timeout_s": min(config.simulation.timeout_s, wall)}
            ),
        }
    )


def _evidence(
    request: PlanningRequest, case: PlanningCase, config: ExperimentConfig, package: DecisionPackage
) -> VerifiedPlanningEvidence:
    if (
        not verify_package(package)
        or package.schema_version != OSCO_SCHEMA
        or package.engine_version != ENGINE_VERSION
        or package.experiment_id != experiment_id(case.problem, case.twin, config)
        or package.config_sha256 != _digest(config.model_dump(mode="json"))
        or package.baseline_problem_sha256 != problem_sha256(case.problem)
        or package.twin_baseline_sha256 != model_sha256(case.twin)
    ):
        raise PlanningEvidenceError("Experiment package does not match the scoped planning case")

    recommendation = package.recommendation
    if (
        package.stop_reason is not StopReason.CONVERGED
        or recommendation.status is not RecommendationStatus.RECOMMENDED_FOR_HUMAN_REVIEW
        or not recommendation.human_approval_required
        or recommendation.approved
        or recommendation.candidate_id is None
    ):
        raise PlanningEvidenceError(
            "Experiment did not produce a completed advisory recommendation"
        )

    matches = [c for c in package.candidates if c.candidate_id == recommendation.candidate_id]
    if len(matches) != 1:
        raise PlanningEvidenceError("Recommended candidate is missing or ambiguous")
    selected = matches[0]
    math, sim = selected.math, selected.sim
    if (
        selected.status is not CandidateStatus.OPERATIONALLY_FEASIBLE
        or selected.contradictions
        or selected.violations
        or math is None
        or not math.verified
        or math.report_status != MathStatus.VERIFIED_NUMERICAL_SOLUTION
        or math.admission_status != AdmissionStatus.ADMITTED_UNITS_VERIFIED
        or sim is None
        or sim.status != SimulationStatus.COMPLETED
        or sim.seed != config.simulation.seed
        or sim.replications != config.simulation.replications
        or sim.planned_pieces < 1
    ):
        raise PlanningEvidenceError("Candidate lacks strictly admitted math or complete simulation")

    problem_digest = problem_sha256(derive_problem(case.problem, selected.spec.planning))
    twin_digest = model_sha256(twin_for(case.twin, selected.spec.twin_changes, selected.spec.label))
    if selected.problem_sha256 != problem_digest or math.input_sha256 != problem_digest:
        raise PlanningEvidenceError("Selected math belongs to a different planning problem")
    if selected.twin_model_sha256 != twin_digest:
        raise PlanningEvidenceError("Selected simulation belongs to a different twin scenario")

    return VerifiedPlanningEvidence(
        case_id=request.case_id,
        case_sha256=request.case_sha256,
        experiment_id=package.experiment_id,
        package_sha256=package.package_sha256,
        effective_config_sha256=package.config_sha256,
        candidate_id=selected.candidate_id,
        problem_sha256=problem_digest,
        twin_model_sha256=twin_digest,
        math_model_sha256=math.model_sha256,
        admission_sha256=math.admission_sha256,
        unit_report_sha256=math.unit_report_sha256,
        simulation_id=sim.simulation_id,
        simulation_report_sha256=sim.report_sha256,
        simulation_content_sha256=sim.content_sha256,
        simulation_kind=_SIMULATION_KIND.validate_python(sim.kind, strict=True),
        simulation_replications=sim.replications,
    )


def verified_planning_capability(
    orchestrator: CoOrchestrator,
    resolve_case: Callable[[WorkflowScope, str], PlanningCase | None],
) -> Capability:
    """Bind the read-only specialist to trusted scoped inputs and an uncached OSCO engine."""
    if orchestrator.store is not None:
        raise ValueError("The planning specialist requires an uncached OSCO orchestrator")

    def run(request: PlanningRequest, context: TaskContext) -> VerifiedPlanningEvidence:
        task_checkpoint()
        if context.scope.factory_id is None or context.scope.asset_id is not None:
            raise PlanningEvidenceError("Production planning requires an authorized factory scope")
        case = resolve_case(context.scope, request.case_id)
        task_checkpoint()
        if (
            case is None
            or case.scope != context.scope
            or case.twin.factory_id != context.scope.factory_id
        ):
            raise PlanningEvidenceError("Planning case is unavailable in the current scope")

        case = PlanningCase(
            case.scope,
            case.problem.model_copy(deep=True),
            case.twin.model_copy(deep=True),
            ExperimentConfig.model_validate_json(case.config.model_dump_json(warnings="error")),
        )
        if case.config.schema_version != OSCO_SCHEMA:
            raise PlanningEvidenceError("Unsupported experiment configuration schema")
        if case.case_sha256 != request.case_sha256:
            raise PlanningEvidenceError("Planning case is unavailable at the requested revision")

        max_candidates = case.config.budget.max_candidates
        reserve_task_resources(
            ResourceAmounts(
                solver_units=max_candidates,
                simulation_units=max_candidates * case.config.simulation.replications,
            )
        )
        config = _bounded_config(case.config, task_remaining_seconds())
        cancel = _TaskCancellation(time.monotonic() + config.budget.wall_clock_s)
        package = orchestrator.run(case.problem, case.twin, config, cancel=cancel)
        cancel.checkpoint()
        evidence = _evidence(request, case, config, package)
        cancel.checkpoint()
        return evidence

    return Capability.register(
        _CAPABILITY,
        _VERSION,
        input_model=PlanningRequest,
        output_model=VerifiedPlanningEvidence,
        handler=run,
        required_permissions=frozenset({Permission.ASSET_READ}),
    )

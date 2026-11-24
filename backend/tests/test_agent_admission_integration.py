"""Cross-check strict OSCO evidence at an advisory supervisor handoff."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from unittest.mock import Mock

import pytest
from pydantic import BaseModel

from qorvexa.agents import (
    Capability,
    CapabilityRegistry,
    Supervisor,
    TaskContext,
    TaskFailure,
    TaskSpec,
    TaskStatus,
    WorkflowResult,
    WorkflowScope,
    WorkflowSpec,
)
from qorvexa.auth.principal import Permission
from qorvexa.industrial.co_orchestration import (
    CandidateOrigin,
    CandidateSpec,
    CandidateStatus,
    CoOrchestrator,
    DecisionPackage,
    RecommendationStatus,
    verify_package,
)
from qorvexa.industrial.co_orchestration import engine as osco_engine
from qorvexa.industrial.production_planning.scenario import DemandBoundsChange, ScenarioSpec
from qorvexa.solver_fabric import LPOrchestrator
from qorvexa.solver_fabric.highs_adapter import HighsAdapter
from qorvexa.solver_fabric.orchestrator import OrchestrationOutcome
from tests.test_optimization_simulation_orchestration import CLOCK, config, problem, twin

SCOPE = WorkflowScope(tenant_id="synthetic-tenant", factory_id="F1")
WORKFLOW = WorkflowSpec(
    workflow_id="review-production-scenario",
    version="1",
    tasks=(
        TaskSpec(task_id="scenario", capability_id="run-osco", capability_version="1"),
        TaskSpec(
            task_id="review",
            capability_id="review-osco",
            capability_version="1",
            dependencies=("scenario",),
        ),
    ),
)


class NoInput(BaseModel):
    pass


class VerifiedEvidence(BaseModel):
    candidate_id: str
    package_sha256: str
    model_sha256: str
    admission_sha256: str
    simulation_content_sha256: str


class ReviewReceipt(BaseModel):
    artifact_sha256: str


class MismatchedDigestSolver(LPOrchestrator):
    """Return a genuine solve under a model digest that fails independent admission."""

    def solve_json(self, raw: bytes | str, job_id: str | None = None) -> OrchestrationOutcome:
        return replace(super().solve_json(raw, job_id=job_id), model_sha256="0" * 64)


def run_advisory(
    solver: LPOrchestrator,
    *,
    portfolio: tuple[CandidateSpec, ...] = (),
) -> tuple[tuple[DecisionPackage, ...], tuple[VerifiedEvidence, ...], WorkflowResult]:
    packages: list[DecisionPackage] = []
    reviewed: list[VerifiedEvidence] = []
    model = twin()
    specialist = CoOrchestrator(solver, clock=CLOCK)

    def run_scenario(_input: NoInput, context: TaskContext) -> VerifiedEvidence:
        if context.scope.factory_id != model.factory_id:
            raise ValueError("Scenario outside the authorized factory")

        package = specialist.run(
            problem(weld_h="7.5"), model, config(portfolio=portfolio, refine_on_bottleneck=False)
        )
        packages.append(package)
        if not verify_package(package):
            raise ValueError("Decision package digest could not be verified")

        recommendation = package.recommendation
        if recommendation.status is not RecommendationStatus.RECOMMENDED_FOR_HUMAN_REVIEW:
            raise ValueError("No recommendable scenario")
        candidate = next(
            c for c in package.candidates if c.candidate_id == recommendation.candidate_id
        )
        if (
            candidate.status is not CandidateStatus.OPERATIONALLY_FEASIBLE
            or candidate.math is None
            or not candidate.math.verified
            or candidate.math.admission_status != "ADMITTED_UNITS_VERIFIED"
            or candidate.sim is None
        ):
            raise ValueError("No verified and simulated recommendation")
        return VerifiedEvidence(
            candidate_id=candidate.candidate_id,
            package_sha256=package.package_sha256,
            model_sha256=candidate.math.model_sha256,
            admission_sha256=candidate.math.admission_sha256,
            simulation_content_sha256=candidate.sim.content_sha256,
        )

    def review_scenario(_input: NoInput, context: TaskContext) -> ReviewReceipt:
        assert len(context.dependencies) == 1
        artifact = context.dependency("scenario")
        assert artifact.ref.scope == SCOPE
        reviewed.append(
            VerifiedEvidence.model_validate(artifact.read(), strict=True, extra="forbid")
        )
        return ReviewReceipt(artifact_sha256=artifact.ref.payload_sha256)

    registry = CapabilityRegistry(
        [
            Capability.register(
                "run-osco",
                "1",
                input_model=NoInput,
                output_model=VerifiedEvidence,
                handler=run_scenario,
                required_permissions=frozenset({Permission.ASSET_READ}),
            ),
            Capability.register(
                "review-osco",
                "1",
                input_model=NoInput,
                output_model=ReviewReceipt,
                handler=review_scenario,
            ),
        ]
    )
    result = Supervisor(registry).run(
        WORKFLOW, scope=SCOPE, permissions=frozenset({Permission.ASSET_READ})
    )
    return tuple(packages), tuple(reviewed), result


def test_supervisor_passes_only_verified_osco_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    evaluate = Mock(wraps=osco_engine.evaluate)
    monkeypatch.setattr(osco_engine, "evaluate", evaluate)
    infeasible = CandidateSpec(
        label="infeasible demand",
        origin=CandidateOrigin.PORTFOLIO,
        planning=ScenarioSpec(
            scenario_id="forced-170",
            changes=(DemandBoundsChange(product_id="B", min_quantity=Decimal(170)),),
        ),
    )

    (package,), (evidence,), result = run_advisory(
        LPOrchestrator(HighsAdapter()), portfolio=(infeasible,)
    )

    scenario_task, review_task = result.tasks
    assert scenario_task.status is review_task.status is TaskStatus.SUCCEEDED
    assert scenario_task.artifact is not None and review_task.artifact is not None
    assert review_task.artifact.read() == {
        "artifact_sha256": scenario_task.artifact.ref.payload_sha256
    }

    baseline, rejected = package.candidates
    assert baseline.math is not None and baseline.sim is not None
    assert baseline.math.verified and baseline.math.admission_status == "ADMITTED_UNITS_VERIFIED"
    assert rejected.status is CandidateStatus.MATH_INFEASIBLE and rejected.sim is None
    assert package.recommendation.candidate_id == baseline.candidate_id
    assert package.recommendation.human_approval_required and not package.recommendation.approved
    evaluate.assert_called_once()
    admitted, _, _, _, label, _, _ = evaluate.call_args.args
    assert admitted.solved and label == "baseline"
    assert evidence.model_dump() == {
        "candidate_id": baseline.candidate_id,
        "package_sha256": package.package_sha256,
        "model_sha256": baseline.math.model_sha256,
        "admission_sha256": baseline.math.admission_sha256,
        "simulation_content_sha256": baseline.sim.content_sha256,
    }


def test_supervisor_blocks_review_when_solver_evidence_is_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluate = Mock(wraps=osco_engine.evaluate)
    monkeypatch.setattr(osco_engine, "evaluate", evaluate)

    (package,), reviewed, result = run_advisory(MismatchedDigestSolver(HighsAdapter()))

    scenario_task, review_task = result.tasks
    assert package.candidates[0].status is CandidateStatus.NOT_VERIFIED
    assert package.candidates[0].sim is None
    assert package.recommendation.status is RecommendationStatus.BASELINE_NOT_VERIFIED
    evaluate.assert_not_called()
    assert not reviewed
    assert scenario_task.status is TaskStatus.FAILED
    assert scenario_task.failure is TaskFailure.HANDLER_FAILED
    assert scenario_task.artifact is None
    assert review_task.status is TaskStatus.BLOCKED
    assert review_task.blocked_by == ("scenario",)

"""Optimization stage: STRICT admission -> real Solver Fabric solve -> independent verification.

Reuses only public contracts: Units Engine evidence, ``compile_unit_gated``, Production Planning
scenarios, ``decision_intelligence.solve_run`` (STRICT by default) and ``trust.admit``. There is no
mock solver and no fallback: whatever the orchestrator returns is verified or rejected.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from qorvexa.industrial.co_orchestration.contracts import CandidateStatus, MathEvidence
from qorvexa.industrial.decision_intelligence import solve_run
from qorvexa.industrial.decision_intelligence.models import Admitted, Rejection
from qorvexa.industrial.decision_intelligence.trust import admit
from qorvexa.industrial.production_planning import PlanningInputError, PlanningProblem
from qorvexa.industrial.production_planning.admission import AdmissionError, AdmissionPolicy
from qorvexa.industrial.production_planning.compiler import canonical_input_json
from qorvexa.industrial.production_planning.report import ReportStatus
from qorvexa.industrial.production_planning.scenario import ScenarioSpec, apply_scenario
from qorvexa.industrial.production_planning.unit_gate import compile_unit_gated
from qorvexa.industrial.units import planning_units_from_problem, validate_planning_units
from qorvexa.industrial.units.evidence import UnitEvidence
from qorvexa.solver_fabric import LPOrchestrator


def problem_sha256(problem: PlanningProblem) -> str:
    return hashlib.sha256(canonical_input_json(problem).encode("utf-8")).hexdigest()


def unit_evidence_for(problem: PlanningProblem) -> UnitEvidence:
    """Fresh, recomputable unit evidence; the admission gate recomputes it again before solving."""
    inp = planning_units_from_problem(problem)
    return UnitEvidence(inputs=inp, report_sha256=validate_planning_units(inp).sha256)


def derive_problem(base: PlanningProblem, spec: ScenarioSpec | None) -> PlanningProblem:
    """ScenarioSpec refuses ambiguous (duplicate) changes; apply_scenario refuses unknown refs."""
    if spec is None:
        return base
    problem, _log = apply_scenario(base, spec)
    return problem


@dataclass(frozen=True)
class SolveOutcome:
    status: CandidateStatus | None  # None = verified and solved
    reasons: tuple[str, ...]
    evidence: MathEvidence | None
    admitted: Admitted | None


def solve_and_verify(
    orchestrator: LPOrchestrator, candidate_id: str, problem: PlanningProblem
) -> SolveOutcome:
    """Always STRICT. A rejected admission never reaches the solver; an unverifiable result never
    becomes optimization evidence."""
    try:
        ev = unit_evidence_for(problem)
        formulation = compile_unit_gated(problem, ev, problem_id=problem.metadata.problem_id)
        run = solve_run(
            orchestrator,
            candidate_id,
            problem,
            formulation,
            units=ev,
            policy=AdmissionPolicy.STRICT,
        )
    except AdmissionError as exc:
        return SolveOutcome(
            CandidateStatus.ADMISSION_REJECTED, (f"{exc.code}: {exc.message}",), None, None
        )
    except PlanningInputError as exc:
        return SolveOutcome(
            CandidateStatus.ADMISSION_REJECTED, (f"{exc.code}: {exc.message}",), None, None
        )
    except Exception as exc:  # unit engine refusal etc.: never solved
        return SolveOutcome(
            CandidateStatus.ADMISSION_REJECTED, (f"{type(exc).__name__}: {exc}",), None, None
        )
    verdict = admit(run, require_units=True)
    if isinstance(verdict, Rejection):
        return SolveOutcome(
            CandidateStatus.NOT_VERIFIED, (f"{verdict.code}: {verdict.reason}",), None, None
        )
    rep = verdict.report
    evidence = MathEvidence(
        report_status=rep.status.value,
        objective=str(rep.objective_value) if rep.objective_value is not None else None,
        quantities=tuple((q.product_id, str(q.quantity), q.unit) for q in rep.quantities),
        model_sha256=verdict.model_sha256,
        input_sha256=verdict.input_sha256,
        admission_status=verdict.admission_status,
        admission_sha256=verdict.admission_sha256,
        unit_report_sha256=verdict.unit_report_sha256,
        verified=rep.status is ReportStatus.VERIFIED_NUMERICAL_SOLUTION
        and verdict.admission_status == "ADMITTED_UNITS_VERIFIED",
    )
    if rep.status is ReportStatus.INFEASIBLE_VERIFIED:
        return SolveOutcome(CandidateStatus.MATH_INFEASIBLE, (rep.reason,), evidence, verdict)
    if rep.status is ReportStatus.UNBOUNDED_VERIFIED:
        return SolveOutcome(CandidateStatus.MATH_UNBOUNDED, (rep.reason,), evidence, verdict)
    if not evidence.verified:
        return SolveOutcome(
            CandidateStatus.NOT_VERIFIED, (rep.reason or "not verified",), evidence, verdict
        )
    return SolveOutcome(None, (), evidence, verdict)

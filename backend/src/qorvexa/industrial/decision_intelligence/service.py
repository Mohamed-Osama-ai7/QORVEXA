"""Decision Intelligence service: trusted scenario runs in, deterministic decision brief out."""

from __future__ import annotations

from collections.abc import Sequence

from qorvexa.industrial.decision_intelligence import explanation as ex
from qorvexa.industrial.decision_intelligence.comparison import ComparisonResult, compare
from qorvexa.industrial.decision_intelligence.models import (
    Admitted,
    DecisionConfig,
    DecisionInputError,
    RankingMode,
    Rejection,
    ScenarioRun,
)
from qorvexa.industrial.decision_intelligence.pareto import ParetoResult, pareto
from qorvexa.industrial.decision_intelligence.ranking import RankingResult, rank_scenarios
from qorvexa.industrial.decision_intelligence.recommendation import (
    UNTRUSTED_CODES,
    coverage_of,
    recommend,
)
from qorvexa.industrial.decision_intelligence.tradeoffs import TradeOff, analyze
from qorvexa.industrial.decision_intelligence.trust import admit
from qorvexa.industrial.production_planning.admission import (
    AdmissionPolicy,
    AdmissionProvenance,
    AdmissionRecord,
    admit_for_solve,
    require_strict,
    verify_admission,
)
from qorvexa.industrial.production_planning.compiler import CompiledFormulation
from qorvexa.industrial.production_planning.model import (
    ObjectiveMode,
    PlanningProblem,
)
from qorvexa.industrial.units.evidence import VALIDATED, UnitEvidence
from qorvexa.solver_fabric import LPOrchestrator, OrchestrationOutcome


def run_from_outcome(
    scenario_id: str,
    problem: PlanningProblem,
    formulation: CompiledFormulation,
    outcome: OrchestrationOutcome,
    units: UnitEvidence | None = None,
    admission: AdmissionRecord | None = None,
) -> ScenarioRun:
    """Wrap a real OrchestrationOutcome as a ScenarioRun (no trust is granted here)."""
    return ScenarioRun(
        scenario_id, problem, formulation, outcome.result, outcome.model_sha256, units, admission
    )


def solve_run(
    orchestrator: LPOrchestrator,
    scenario_id: str,
    problem: PlanningProblem,
    formulation: CompiledFormulation,
    job_id: str | None = None,
    units: UnitEvidence | None = None,
    require_units: bool = False,
    *,
    policy: AdmissionPolicy = AdmissionPolicy.STRICT,
    admission: AdmissionRecord | None = None,
    provenance: AdmissionProvenance | None = None,
    expected_model_sha256: str | None = None,
) -> ScenarioRun:
    """Solve one formulation with the real orchestrator (thin wrapper; no parallel pipeline).

    Unit gate: supplied unit evidence is recomputed and bound to the problem BEFORE the solver
    is called; invalid/forged/mismatched evidence raises and nothing is solved.
    """
    eff = AdmissionPolicy.STRICT if require_units else policy
    if admission is None:
        admission = admit_for_solve(
            problem,
            formulation,
            policy=eff,
            units=units,
            provenance=provenance,
            expected_model_sha256=expected_model_sha256,
        )
    else:
        verify_admission(admission, problem, formulation, units)
        if eff is AdmissionPolicy.STRICT:
            require_strict(admission)
    outcome = orchestrator.solve_json(formulation.spec_json, job_id=job_id or scenario_id)
    return run_from_outcome(scenario_id, problem, formulation, outcome, units, admission)


def _unit_consistent(
    base: Admitted | Rejection, other: Admitted | Rejection
) -> Admitted | Rejection:
    """A scenario must share the baseline's unit evidence (same validated report, same status)."""
    if not isinstance(base, Admitted) or not isinstance(other, Admitted):
        return other
    if (base.units_status, base.unit_report_sha256) == (
        other.units_status,
        other.unit_report_sha256,
    ):
        return other
    code = "UNIT_EVIDENCE_MISSING" if other.units_status != VALIDATED else "UNIT_EVIDENCE_MISMATCH"
    return Rejection(
        scenario_id=other.run.scenario_id,
        code=code,
        reason="scenario unit evidence differs from the baseline's",
    )


def _units_line(base: Admitted | Rejection) -> str:
    if not isinstance(base, Admitted):
        return "Units: not evaluated (baseline rejected)."
    if base.admission_status == "ADMITTED_LEGACY_UNVERIFIED":
        return (
            "Units: LEGACY_UNVERIFIED admission (explicit legacy policy) - dimensional "
            "consistency was NOT verified; results are limited evidence."
        )
    if base.admission_status == "NOT_ADMITTED":
        return (
            "Admission: NOT_ADMITTED - this result did not pass the production-planning "
            "admission gate; it is NOT verified industrial evidence."
        )
    if base.units_status == VALIDATED:
        return (
            f"Units: DIMENSIONALLY_VALIDATED (unit report {base.unit_report_sha256}); "
            "differently scaled units were converted only by recorded human approval."
        )
    return "Units: LEGACY_UNITS_UNVERIFIED - dimensional consistency was NOT verified."


def _objective_text(base: Admitted) -> str:
    p = base.run.problem
    verb = (
        "maximize contribution margin"
        if p.mode is ObjectiveMode.MAXIMIZE_MARGIN
        else "minimize variable production cost"
    )
    return f"{verb} ({p.currency})"


def decide(
    baseline: ScenarioRun, scenarios: Sequence[ScenarioRun], config: DecisionConfig | None = None
) -> ex.DecisionBrief:
    cfg = config or DecisionConfig()
    ids = [baseline.scenario_id, *(s.scenario_id for s in scenarios)]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise DecisionInputError("DUPLICATE_SCENARIO_ID", f"duplicate scenario id(s): {dup}")

    req = cfg.strict
    base_adm = admit(baseline, req)
    others = [_unit_consistent(base_adm, admit(s, req)) for s in scenarios]

    comparison: ComparisonResult | None = None
    trade: dict[str, tuple[TradeOff, ...]] = {}
    solved: dict[str, Admitted] = {}
    ranking: RankingResult | None = None
    obj_first: RankingResult | None = None
    par: ParetoResult = pareto({}, cfg.pareto_objectives)
    admitted_all: list[Admitted] = []

    if isinstance(base_adm, Admitted):
        admitted_all = [base_adm, *(o for o in others if isinstance(o, Admitted))]
        comparison = compare(base_adm, others)
        mode = base_adm.run.problem.mode
        compared_ids = {c.scenario_id for c in comparison.compared}
        for c in comparison.compared:
            trade[c.scenario_id] = analyze(c, mode)
        for a in admitted_all:
            if a.solved and (a is base_adm or a.run.scenario_id in compared_ids):
                solved[a.run.scenario_id] = a
        cands = {sid: a.metrics for sid, a in solved.items()}
        obj_first = rank_scenarios(mode, RankingMode.OBJECTIVE_FIRST, cands, cfg.hard_constraints)
        ranking = (
            obj_first
            if cfg.ranking_mode is RankingMode.OBJECTIVE_FIRST
            else rank_scenarios(
                mode, cfg.ranking_mode, cands, cfg.hard_constraints, cfg.preferences
            )
        )
        par = pareto(cands, cfg.pareto_objectives)
        excluded = list(comparison.excluded)
    else:
        excluded = [o for o in others if isinstance(o, Rejection)]

    untrusted = [r for r in excluded if r.code in UNTRUSTED_CODES]
    coverage = coverage_of(len(ids), admitted_all, untrusted, comparison)
    rec = recommend(
        objective_text="unknown" if isinstance(base_adm, Rejection) else _objective_text(base_adm),
        baseline=base_adm,
        solved=solved,
        comparison=comparison,
        ranking=ranking,
        trade_offs=dict(sorted(trade.items())),
        config=cfg,
        coverage=coverage,
        untrusted=untrusted,
    )
    rejected = ([base_adm] if isinstance(base_adm, Rejection) else []) + excluded
    base_ok = isinstance(base_adm, Admitted)
    return ex.seal(
        schema_version=ex.BRIEF_SCHEMA_VERSION,
        executive_summary=ex.summary_lines(base_adm, comparison, rec),
        baseline_id=baseline.scenario_id,
        objective_mode=base_adm.run.problem.mode.value if isinstance(base_adm, Admitted) else None,
        currency=base_adm.run.problem.currency if isinstance(base_adm, Admitted) else None,
        baseline_status=(
            base_adm.report.status.value
            if isinstance(base_adm, Admitted)
            else f"REJECTED:{base_adm.code}"
        ),
        comparison=comparison,
        rejected_scenarios=tuple(sorted(rejected, key=lambda r: (r.scenario_id, r.code))),
        trade_offs=dict(sorted(trade.items())),
        unavailable_analyses=ex.UNAVAILABLE,
        pareto=par,
        ranking=ranking,
        objective_first_ranking=obj_first,
        observed_sensitivity=ex.observed(comparison) if comparison else (),
        recommendation=rec,
        assumptions=ex.ASSUMPTIONS,
        limitations=(*ex.LIMITATIONS, _units_line(base_adm)),
        evidence=tuple(
            sorted((ex.evidence_ref(a) for a in admitted_all), key=lambda e: e.scenario_id)
        )
        if base_ok
        else (),
    )

"""Decision Intelligence over REAL Production Planning scenarios solved by real HiGHS.

No mocks: every ScenarioRun below comes from LPOrchestrator(HighsAdapter()).
"""

from __future__ import annotations

import json
import math
from decimal import Decimal

import pytest

from qorvexa.industrial.decision_intelligence import (
    DecisionConfig,
    MetricDirection,
    MetricObjective,
    RecommendationStatus,
    ScenarioRun,
    brief_json,
    decide,
)
from qorvexa.industrial.decision_intelligence import (
    solve_run as _strict_solve_run,
)
from qorvexa.industrial.production_planning import (
    CapacityChange,
    DemandBoundsChange,
    ScenarioSpec,
    compile_problem,
    compile_scenario,
    validate_planning_dict,
)
from qorvexa.industrial.production_planning.admission import AdmissionPolicy
from qorvexa.solver_fabric import LPOrchestrator
from qorvexa.solver_fabric.highs_adapter import HighsAdapter
from tests.test_production_planning import example_min_cost, example_product_mix


def solve_run(*a: object, **k: object) -> ScenarioRun:
    """These e2e tests use unit-less legacy models, so legacy is selected EXPLICITLY."""
    return _strict_solve_run(*a, policy=AdmissionPolicy.LEGACY_UNVERIFIED, **k)  # type: ignore[arg-type]


ACK = frozenset({"CAPACITY_CHANGE_COST_NOT_MODELED", "UNIT_VALIDATION_NOT_PERFORMED"})


def _solve(base_problem: object, sid: str, *changes: object) -> ScenarioRun:
    orch = LPOrchestrator(HighsAdapter())
    s = compile_scenario(base_problem, ScenarioSpec(scenario_id=sid, changes=tuple(changes)))  # type: ignore[arg-type]
    return solve_run(orch, sid, s.problem, s.formulation)


@pytest.fixture(scope="module")
def mix() -> dict[str, ScenarioRun]:
    bp = validate_planning_dict(example_product_mix())
    orch = LPOrchestrator(HighsAdapter())
    runs = {"BASE": solve_run(orch, "BASE", bp, compile_problem(bp))}
    runs["MACH120"] = _solve(bp, "MACH120", CapacityChange(resource_id="MACHINE", capacity="120"))
    runs["MACH80"] = _solve(bp, "MACH80", CapacityChange(resource_id="MACHINE", capacity="80"))
    runs["B90"] = _solve(bp, "B90", DemandBoundsChange(product_id="B", min_quantity="90"))
    return runs


CFG = DecisionConfig(
    pareto_objectives=(
        MetricObjective(metric="objective", direction=MetricDirection.MAXIMIZE),
        MetricObjective(metric="total_volume", direction=MetricDirection.MAXIMIZE),
    ),
)


def test_real_scenarios_are_compared_and_ranked(mix: dict[str, ScenarioRun]) -> None:
    brief = decide(mix["BASE"], [mix["MACH120"], mix["MACH80"], mix["B90"]], CFG)
    assert brief.baseline_status == "VERIFIED_NUMERICAL_SOLUTION"
    assert brief.comparison is not None and brief.rejected_scenarios == ()
    by = {c.scenario_id: c for c in brief.comparison.compared}
    for sid, delta in (("MACH120", 200.0), ("MACH80", -200.0)):
        o = by[sid].objective
        assert o is not None and math.isclose(float(o.absolute_change), delta, abs_tol=1e-6)
    assert by["B90"].status == "INFEASIBLE_VERIFIED" and by["B90"].objective is None
    assert brief.objective_first_ranking is not None
    order = [e.scenario_id for e in brief.objective_first_ranking.entries]
    assert order == ["MACH120", "BASE", "MACH80"]  # infeasible scenario is not ranked
    assert brief.pareto.non_dominated == ("MACH120",)
    assert {d.scenario_id for d in brief.pareto.dominated} == {"BASE", "MACH80"}


def test_real_recommendation_policy(mix: dict[str, ScenarioRun]) -> None:
    scenarios = [mix["MACH120"], mix["MACH80"], mix["B90"]]
    cond = decide(mix["BASE"], scenarios, CFG).recommendation
    assert cond.status == RecommendationStatus.CONDITIONALLY_RECOMMENDED
    assert cond.recommended_scenario_id == "MACH120"
    assert cond.unresolved_assumptions == (
        "CAPACITY_CHANGE_COST_NOT_MODELED",
        "UNIT_VALIDATION_NOT_PERFORMED",
    )
    ok = decide(mix["BASE"], scenarios, DecisionConfig(acknowledged_assumptions=ACK)).recommendation
    assert ok.status == RecommendationStatus.RECOMMENDED and ok.human_approval_required
    assert math.isclose(float(ok.supporting_metrics["objective"]), 2800.0, abs_tol=1e-6)
    assert ok.coverage.verification_class == "NUMERICAL_GAP_VERIFIED"
    assert ok.coverage.exact_optimality_proof is False


def test_real_trade_offs_and_infeasibility_explanation(mix: dict[str, ScenarioRun]) -> None:
    brief = decide(mix["BASE"], [mix["MACH120"], mix["B90"]], CFG)
    cap = {t.trade_off_id: t for t in brief.trade_offs["MACH120"]}["CAPACITY_CHANGE_VS_OBJECTIVE"]
    assert cap.magnitude is not None and math.isclose(float(cap.magnitude), 10.0, abs_tol=1e-6)
    assert "not a shadow price" in cap.summary
    assert brief.trade_offs["B90"][0].trade_off_id == "FEASIBILITY_LOST"
    behaviors = {o.scenario_id: o for o in brief.observed_sensitivity}
    assert behaviors["B90"].feasibility == "INFEASIBLE_VERIFIED"
    assert behaviors["MACH120"].mathematical_sensitivity.startswith("UNAVAILABLE")


def test_result_reuse_between_real_scenarios_is_rejected(mix: dict[str, ScenarioRun]) -> None:
    s, b = mix["MACH120"], mix["BASE"]
    reused = ScenarioRun("MACH120", s.problem, s.formulation, b.result, b.model_sha256)
    brief = decide(b, [reused], CFG)
    assert [r.code for r in brief.rejected_scenarios] == ["MODEL_HASH_MISMATCH"]
    assert brief.comparison is not None and brief.comparison.compared == ()


def test_real_brief_is_deterministic_and_json(mix: dict[str, ScenarioRun]) -> None:
    a = decide(mix["BASE"], [mix["MACH120"], mix["MACH80"]], CFG)
    b = decide(mix["BASE"], [mix["MACH80"], mix["MACH120"]], CFG)
    assert a.brief_sha256 == b.brief_sha256
    data = json.loads(brief_json(a))
    assert data["recommendation"]["human_approval_required"] is True
    assert any("GAP_NUMERICAL" in line for line in data["executive_summary"])
    refs = {e["scenario_id"]: e for e in data["evidence"]}
    assert refs["BASE"]["model_sha256"] != refs["MACH120"]["model_sha256"]
    assert refs["MACH120"]["optimality_tier"] == "GAP_NUMERICAL"


def test_real_minimization_scenarios() -> None:
    bp = validate_planning_dict(example_min_cost())
    orch = LPOrchestrator(HighsAdapter())
    base = solve_run(orch, "BASE", bp, compile_problem(bp))
    b4 = _solve(bp, "B4", DemandBoundsChange(product_id="B", min_quantity="4"))
    brief = decide(base, [b4], DecisionConfig())
    assert brief.objective_mode == "MINIMIZE_COST"
    o = brief.comparison.compared[0].objective  # type: ignore[union-attr]
    assert o is not None and o.effect.value == "IMPROVED"
    assert math.isclose(float(o.baseline), 35.0, abs_tol=1e-6)
    assert math.isclose(float(o.scenario), 32.0, abs_tol=1e-6)
    assert math.isclose(float(o.absolute_change), -3.0, abs_tol=1e-6)
    assert brief.objective_first_ranking is not None
    assert brief.objective_first_ranking.entries[0].scenario_id == "B4"
    assert o.relative_change is not None and abs(
        o.relative_change - Decimal(-3) / Decimal(35)
    ) < Decimal("1e-6")

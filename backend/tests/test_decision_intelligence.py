"""Decision Intelligence unit tests.

MOCKED EVIDENCE NOTICE: ``mk_run`` builds *synthetic* SolverRuntimeResult records (hand-derived
exactly-feasible/optimal points wrapped in valid evidence DTOs) so trust gates and edge cases can
be exercised deterministically. They are NOT solver output. Real HiGHS runs are in
test_decision_intelligence_e2e.py.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from qorvexa.contracts.solver_runtime import (
    FarkasEvidence,
    MathOutcome,
    SolverRuntimeResult,
    SolverTermination,
    VerificationClaim,
)
from qorvexa.contracts.solver_runtime import VerificationClaimKind as K
from qorvexa.contracts.solver_runtime import VerificationLevel as L
from qorvexa.contracts.solver_runtime import VerificationStatus as S
from qorvexa.industrial.decision_intelligence import (
    DecisionConfig,
    DecisionInputError,
    HardConstraint,
    MetricDirection,
    MetricObjective,
    Preference,
    RankingMode,
    RecommendationStatus,
    ScenarioRun,
    brief_json,
    decide,
)
from qorvexa.industrial.decision_intelligence.models import Admitted, Rejection
from qorvexa.industrial.decision_intelligence.numeric import close, fmt, relative_change
from qorvexa.industrial.decision_intelligence.pareto import ParetoStatus, pareto
from qorvexa.industrial.decision_intelligence.ranking import RankingStatus, rank_scenarios
from qorvexa.industrial.decision_intelligence.trust import admit
from qorvexa.industrial.production_planning import (
    CapacityChange,
    CompiledFormulation,
    DemandBoundsChange,
    ScenarioSpec,
    ValueChange,
    compile_problem,
    compile_scenario,
    validate_planning_dict,
)
from qorvexa.industrial.production_planning.model import ObjectiveMode
from qorvexa.solver_fabric import build_model_from_derived, derive_problem_forms
from qorvexa.solver_fabric.verifier import find_interval_conflict
from tests.test_production_planning import (
    doc,
    evidence,
    example_min_cost,
    example_product_mix,
    solved_dto,
)

D = Decimal
TOL = D("1e-9")


# ------------------------------------------------------------------ synthetic run builders (MOCKED)


def model_of(formulation: Any) -> Any:
    return build_model_from_derived(derive_problem_forms(formulation.to_spec()), None)


def mk_run(
    sid: str,
    problem: Any,
    formulation: Any,
    x: tuple[float, ...] | None = None,
    obj: float | None = None,
    *,
    kind: str = "solved",
    model_sha: str | None = None,
    ids: tuple[str, ...] | None = None,
) -> ScenarioRun:
    m = model_of(formulation)
    sha = model_sha or m.model_sha256
    result: SolverRuntimeResult | None
    low, lclaim = evidence(m)
    if kind == "solved":
        assert x is not None and obj is not None
        result = solved_dto(m, x, ids or m.variable_ids, obj)
    elif kind == "infeasible":
        v = find_interval_conflict(m)
        assert v is not None and v.certificate_value is not None
        result = SolverRuntimeResult(
            outcome=MathOutcome.INFEASIBLE,
            solver_termination=SolverTermination.INFEASIBLE,
            claims=(
                lclaim,
                VerificationClaim(
                    claim_kind=K.INFEASIBILITY,
                    status=S.VERIFIED,
                    level=L.EXACT_RATIONAL,
                    exactness_scope="canonicalized_input_values",
                ),
            ),
            lowering_evidence=low,
            infeasibility_evidence=FarkasEvidence(
                model_sha256=m.model_sha256,
                multipliers={r: str(y) for r, y in zip(m.row_ids, v.multipliers, strict=True)},
                certificate_value=str(v.certificate_value),
            ),
        )
    elif kind == "unknown":
        result = SolverRuntimeResult(
            outcome=MathOutcome.UNKNOWN,
            solver_termination=SolverTermination.OPTIMAL,
            lowering_evidence=low,
            claims=(lclaim,),
            primal_solution=dict(zip(m.variable_ids, x or (0.0,) * m.num_variables, strict=True)),
        )
    elif kind == "unknown_no_lowering":
        result = SolverRuntimeResult(
            outcome=MathOutcome.UNKNOWN, solver_termination=SolverTermination.OPTIMAL
        )
    elif kind == "failed":
        result = SolverRuntimeResult(
            outcome=MathOutcome.FAILED,
            solver_termination=SolverTermination.CRASHED,
            failure_details="worker crashed",
            lowering_evidence=low,
            claims=(lclaim,),
        )
    else:
        result = None
    return ScenarioRun(sid, problem, formulation, result, sha)


def code_of(r: Any) -> str:
    assert isinstance(r, Rejection), "expected a rejection"
    return str(r.code)


def base_problem() -> Any:
    return validate_planning_dict(example_product_mix())


def scen(base: Any, sid: str, *changes: Any) -> Any:
    return compile_scenario(base, ScenarioSpec(scenario_id=sid, changes=tuple(changes)))


def mix_runs() -> tuple[Any, ScenarioRun, dict[str, ScenarioRun]]:
    """Baseline 2600 at (20,60); MACH120 -> 2800 at (40,40); MACH125 -> 2800; MACH80 -> 2400."""
    bp = base_problem()
    bf = compile_problem(bp)
    base = mk_run("BASE", bp, bf, (20.0, 60.0), 2600.0)
    out: dict[str, ScenarioRun] = {}
    for sid, cap, x, obj in (
        ("MACH120", "120", (40.0, 40.0), 2800.0),
        ("MACH125", "125", (40.0, 40.0), 2800.0),
        ("MACH80", "80", (0.0, 80.0), 2400.0),
    ):
        s = scen(bp, sid, CapacityChange(resource_id="MACHINE", capacity=cap))
        out[sid] = mk_run(sid, s.problem, s.formulation, x, obj)
    return bp, base, out


# ------------------------------------------------------------------ trust


class TestTrust:
    def test_unknown_result_is_rejected_and_raw_candidate_not_promoted(self) -> None:
        bp = base_problem()
        r = admit(mk_run("BASE", bp, compile_problem(bp), (20.0, 60.0), kind="unknown"))
        assert code_of(r) == "RESULT_UNKNOWN"
        brief = decide(mk_run("BASE", bp, compile_problem(bp), (20.0, 60.0), kind="unknown"), [])
        assert brief.recommendation.status == RecommendationStatus.NO_VERIFIED_RECOMMENDATION
        assert brief.recommendation.supporting_metrics == {}
        assert "20" not in " ".join(brief.executive_summary)  # the candidate never surfaces

    def test_failed_result_is_rejected(self) -> None:
        bp = base_problem()
        assert (
            code_of(admit(mk_run("B", bp, compile_problem(bp), kind="failed"))) == "RESULT_FAILED"
        )

    def test_missing_lowering_evidence_is_rejected(self) -> None:
        bp = base_problem()
        r = admit(mk_run("B", bp, compile_problem(bp), kind="unknown_no_lowering"))
        assert code_of(r) == "MISSING_LOWERING_EVIDENCE"

    def test_no_result_is_rejected(self) -> None:
        bp = base_problem()
        assert code_of(admit(mk_run("B", bp, compile_problem(bp), kind="none"))) == "NO_RESULT"

    def test_wrong_model_hash_is_rejected(self) -> None:
        bp = base_problem()
        r = admit(mk_run("B", bp, compile_problem(bp), (20.0, 60.0), 2600.0, model_sha="e" * 64))
        assert code_of(r) == "MODEL_HASH_MISMATCH"

    def test_wrong_variable_mapping_is_rejected(self) -> None:
        bp = base_problem()
        r = admit(
            mk_run("B", bp, compile_problem(bp), (20.0, 60.0), 2600.0, ids=("VAR-0001", "VAR-0099"))
        )
        assert code_of(r) == "VARIABLE_MAPPING_MISMATCH"

    def test_problem_manifest_mismatch_is_rejected(self) -> None:
        bp, _base, runs = mix_runs()
        other = runs["MACH120"]
        forged = ScenarioRun("X", bp, other.formulation, other.result, other.model_sha256)
        assert code_of(admit(forged)) == "PROBLEM_MANIFEST_MISMATCH"

    def test_cross_scenario_result_reuse_is_rejected(self) -> None:
        _bp, base, runs = mix_runs()
        s = runs["MACH120"]
        reused = ScenarioRun("MACH120", s.problem, s.formulation, base.result, base.model_sha256)
        assert code_of(admit(reused)) == "MODEL_HASH_MISMATCH"
        brief = decide(base, [reused])
        assert [r.code for r in brief.rejected_scenarios] == ["MODEL_HASH_MISMATCH"]
        assert brief.comparison is not None and brief.comparison.compared == ()

    def test_valid_run_is_admitted(self) -> None:
        _bp, base, _ = mix_runs()
        a = admit(base)
        assert isinstance(a, Admitted) and a.solved


# ------------------------------------------------------------------ comparison


class TestComparison:
    def test_maximization_improvement_and_deterioration(self) -> None:
        _bp, base, runs = mix_runs()
        brief = decide(base, [runs["MACH120"], runs["MACH80"]])
        assert brief.comparison is not None
        by = {c.scenario_id: c for c in brief.comparison.compared}
        up, down = by["MACH120"].objective, by["MACH80"].objective
        assert up is not None and down is not None
        assert up.absolute_change == D("200") and up.relative_change == relative_change(
            D("2600"), D("2800")
        )
        assert up.effect.value == "IMPROVED"
        assert down.absolute_change == D("-200") and down.effect.value == "DETERIORATED"
        assert up.mode is ObjectiveMode.MAXIMIZE_MARGIN

    def test_minimization_direction(self) -> None:
        bp = validate_planning_dict(example_min_cost())
        bf = compile_problem(bp)
        base = mk_run("BASE", bp, bf, (10.0, 5.0), 35.0)
        better = scen(bp, "B4", DemandBoundsChange(product_id="B", min_quantity="4"))
        worse = scen(bp, "A3", ValueChange(product_id="A", value_per_unit="3"))
        r1 = mk_run("B4", better.problem, better.formulation, (10.0, 4.0), 32.0)
        r2 = mk_run("A3", worse.problem, worse.formulation, (10.0, 5.0), 45.0)
        brief = decide(base, [r1, r2])
        assert brief.comparison is not None
        by = {c.scenario_id: c.objective for c in brief.comparison.compared}
        assert by["B4"] is not None and by["B4"].effect.value == "IMPROVED"  # lower cost
        assert by["A3"] is not None and by["A3"].effect.value == "DETERIORATED"
        assert brief.recommendation.recommended_scenario_id in ("B4", "BASE")
        assert brief.objective_first_ranking is not None
        assert brief.objective_first_ranking.entries[0].scenario_id == "B4"

    def test_product_resource_and_demand_changes(self) -> None:
        _bp, base, runs = mix_runs()
        brief = decide(base, [runs["MACH120"]])
        assert brief.comparison is not None
        c = brief.comparison.compared[0]
        q = {p.product_id: p for p in c.products}
        assert q["A"].absolute_change == D("20") and q["B"].absolute_change == D("-20")
        assert q["A"].relative_change == D("1")
        r = {x.resource_id: x for x in c.resources}
        assert r["MACHINE"].capacity_scenario == D("120") and r["MACHINE"].remaining_scenario == 0
        assert (
            r["MACHINE"].utilization_baseline == D("1")
            and r["MACHINE"].binding_change == "UNCHANGED"
        )
        assert r["LABOR"].binding_scenario is True
        d = {x.product_id: x for x in c.demand}
        assert d["A"].shortfall_scenario == 0 and d["A"].meets_minimum_scenario

    def test_became_binding_and_released_capacity(self) -> None:
        bp = base_problem()
        base = mk_run("BASE", bp, compile_problem(bp), (10.0, 10.0), 700.0)  # slack on both
        s = scen(bp, "MACH120", CapacityChange(resource_id="MACHINE", capacity="120"))
        run = mk_run("MACH120", s.problem, s.formulation, (40.0, 40.0), 2800.0)
        c = decide(base, [run]).comparison.compared[0]  # type: ignore[union-attr]
        kinds = {r.resource_id: r.binding_change for r in c.resources}
        assert kinds == {"LABOR": "BECAME_BINDING", "MACHINE": "BECAME_BINDING"}

    def test_infeasible_scenario_is_a_status_change_not_a_number(self) -> None:
        bp, base, _ = mix_runs()
        s = scen(bp, "B90", DemandBoundsChange(product_id="B", min_quantity="90"))
        run = mk_run("B90", s.problem, s.formulation, kind="infeasible")
        brief = decide(base, [run])
        assert brief.comparison is not None
        c = brief.comparison.compared[0]
        assert c.status == "INFEASIBLE_VERIFIED" and c.objective is None and c.products == ()
        assert (
            c.status_change
            == "baseline VERIFIED_NUMERICAL_SOLUTION -> scenario INFEASIBLE_VERIFIED"
        )
        assert brief.trade_offs["B90"][0].trade_off_id == "FEASIBILITY_LOST"
        assert brief.recommendation.recommended_scenario_id == "BASE"  # infeasible never wins
        assert brief.pareto.status == ParetoStatus.NOT_CONFIGURED

    def test_incompatible_currency_units_horizon_objective_are_excluded(self) -> None:
        _bp, base, _ = mix_runs()

        def variant(mutate: Any) -> ScenarioRun:
            raw = example_product_mix()
            mutate(raw)
            p = validate_planning_dict(raw)
            f = compile_problem(p)
            return mk_run("V", p, f, (20.0, 60.0), 2600.0)

        def cur(raw: dict[str, Any]) -> None:
            raw["currency"] = "EUR"
            for p in raw["products"]:
                p["value_currency"] = "EUR"

        def unit(raw: dict[str, Any]) -> None:
            for p in raw["products"]:
                p["unit"] = "kg"
            for c in raw["consumptions"]:
                c["product_unit"] = "kg"

        def horizon(raw: dict[str, Any]) -> None:
            raw["metadata"]["period_label"] = "2026-Q3"

        def mode(raw: dict[str, Any]) -> None:
            raw["mode"] = "MINIMIZE_COST"
            raw["products"][0]["min_quantity"] = "1"

        for mutate, code in (
            (cur, "INCOMPATIBLE_CURRENCY"),
            (unit, "INCOMPATIBLE_UNITS"),
            (horizon, "INCOMPATIBLE_HORIZON"),
            (mode, "INCOMPATIBLE_OBJECTIVE"),
        ):
            brief = decide(base, [variant(mutate)])
            assert brief.comparison is not None and brief.comparison.compared == ()
            assert [r.code for r in brief.rejected_scenarios] == [code]

    def test_duplicate_scenario_ids(self) -> None:
        bp, base, runs = mix_runs()
        with pytest.raises(DecisionInputError) as ei:
            decide(base, [runs["MACH120"], runs["MACH120"]])
        assert ei.value.code == "DUPLICATE_SCENARIO_ID"
        with pytest.raises(DecisionInputError):
            decide(base, [ScenarioRun("BASE", bp, runs["MACH120"].formulation, None, None)])

    def test_scenario_must_descend_from_this_baseline(self) -> None:
        _bp, base, _runs = mix_runs()
        other_bp = validate_planning_dict(example_min_cost())
        other = scen(other_bp, "MACH120", CapacityChange(resource_id="MACHINE", capacity="30"))
        r = mk_run("MACH120", other.problem, other.formulation, (10.0, 5.0), 35.0)
        brief = decide(base, [r])
        assert brief.rejected_scenarios[0].code in ("INCOMPATIBLE_OBJECTIVE", "INCOMPATIBLE_UNITS")


# ------------------------------------------------------------------ trade-offs (no invented costs)


class TestTradeOffs:
    def test_capacity_trade_off_is_observed_not_a_shadow_price(self) -> None:
        _bp, base, runs = mix_runs()
        brief = decide(base, [runs["MACH120"]])
        t = {x.trade_off_id: x for x in brief.trade_offs["MACH120"]}
        cap = t["CAPACITY_CHANGE_VS_OBJECTIVE"]
        assert cap.magnitude == D("10") and not cap.adverse and cap.state == "AVAILABLE"
        assert "not a shadow price" in cap.summary
        assert "The cost of changing this capacity is not modeled." in cap.assumptions
        assert any("Resource opportunity cost" in u for u in brief.unavailable_analyses)

    def test_new_bottleneck_trade_off_is_adverse(self) -> None:
        bp = base_problem()
        base = mk_run("BASE", bp, compile_problem(bp), (10.0, 10.0), 700.0)
        s = scen(bp, "MACH120", CapacityChange(resource_id="MACHINE", capacity="120"))
        run = mk_run("MACH120", s.problem, s.formulation, (40.0, 40.0), 2800.0)
        brief = decide(base, [run])
        ids = {x.trade_off_id: x for x in brief.trade_offs["MACH120"]}
        assert ids["OBJECTIVE_VS_NEW_BOTTLENECKS"].adverse
        assert brief.recommendation.status == RecommendationStatus.CONDITIONALLY_RECOMMENDED
        assert brief.recommendation.negative_trade_offs

    def test_cost_vs_volume_in_minimization(self) -> None:
        bp = validate_planning_dict(example_min_cost())
        base = mk_run("BASE", bp, compile_problem(bp), (12.0, 5.0), 39.0)
        s = scen(bp, "A10", DemandBoundsChange(product_id="A", max_quantity="10"))
        run = mk_run("A10", s.problem, s.formulation, (10.0, 5.0), 35.0)
        brief = decide(base, [run])
        assert "COST_VS_VOLUME" in {x.trade_off_id for x in brief.trade_offs["A10"]}


# ------------------------------------------------------------------ ranking (pure metrics)

M = ObjectiveMode


def cands(**rows: dict[str, str]) -> dict[str, dict[str, Decimal]]:
    return {k: {m: D(v) for m, v in r.items()} for k, r in rows.items()}


class TestRanking:
    def test_objective_first_max_and_min(self) -> None:
        c = cands(A={"objective": "10"}, B={"objective": "30"}, C={"objective": "20"})
        mx = rank_scenarios(M.MAXIMIZE_MARGIN, RankingMode.OBJECTIVE_FIRST, c)
        mn = rank_scenarios(M.MINIMIZE_COST, RankingMode.OBJECTIVE_FIRST, c)
        assert [e.scenario_id for e in mx.entries] == ["B", "C", "A"]
        assert [e.scenario_id for e in mn.entries] == ["A", "C", "B"]
        assert mx.entries[0].metrics["objective"] == D(30)  # raw metrics stay visible

    def test_ties_share_a_rank(self) -> None:
        c = cands(A={"objective": "30"}, B={"objective": "30.0000000001"}, C={"objective": "20"})
        r = rank_scenarios(M.MAXIMIZE_MARGIN, RankingMode.OBJECTIVE_FIRST, c)
        assert {e.scenario_id: e.rank for e in r.entries} == {"A": 1, "B": 1, "C": 3}
        assert set(r.winners) == {"A", "B"}

    def test_constraint_first_needs_configuration(self) -> None:
        c = cands(A={"objective": "1"})
        r = rank_scenarios(M.MAXIMIZE_MARGIN, RankingMode.CONSTRAINT_FIRST, c)
        assert r.status == RankingStatus.MISSING_CONFIGURATION

    def test_hard_constraints_reject_and_missing_metric_rejects(self) -> None:
        c = cands(
            A={"objective": "30", "utilization:M": "0.95"},
            B={"objective": "20", "utilization:M": "0.80"},
            C={"objective": "40"},
        )
        hc = (HardConstraint(metric="utilization:M", op="<=", threshold="0.9"),)
        r = rank_scenarios(M.MAXIMIZE_MARGIN, RankingMode.CONSTRAINT_FIRST, c, hc)
        assert [e.scenario_id for e in r.entries] == ["B"]
        rej = {x.scenario_id: x.reasons for x in r.rejected}
        assert "metric unavailable" in rej["C"][0] and "value 0.95" in rej["A"][0]
        assert r.entries[0].constraint_checks[0].slack == D("0.10")

    def test_no_scenario_meets_hard_requirements(self) -> None:
        c = cands(A={"objective": "30", "total_shortfall": "5"})
        hc = (HardConstraint(metric="total_shortfall", op="<=", threshold="0"),)
        r = rank_scenarios(M.MAXIMIZE_MARGIN, RankingMode.OBJECTIVE_FIRST, c, hc)
        assert r.status == RankingStatus.NO_ELIGIBLE_SCENARIOS and r.entries == ()

    def prefs(self, **w: str) -> tuple[Preference, ...]:
        dirs = {"objective": MetricDirection.MAXIMIZE, "vol": MetricDirection.MAXIMIZE}
        return tuple(
            Preference(metric=k, direction=dirs.get(k, MetricDirection.MINIMIZE), weight=v)
            for k, v in w.items()
        )

    def test_weighted_exact_scores_and_contributions(self) -> None:
        c = cands(
            A={"objective": "100", "vol": "10"},
            B={"objective": "200", "vol": "5"},
            C={"objective": "150", "vol": "20"},
        )
        r = rank_scenarios(
            M.MAXIMIZE_MARGIN,
            RankingMode.WEIGHTED_PREFERENCE,
            c,
            preferences=self.prefs(objective="3", vol="1"),
        )
        assert r.status == RankingStatus.OK
        score = {e.scenario_id: e.score or D(0) for e in r.entries}
        assert abs(score["A"] - D(1) / D(12)) < D("1e-40")
        assert abs(score["B"] - D("0.75")) < D("1e-40") and abs(score["C"] - D("0.625")) < D(
            "1e-40"
        )
        assert [e.scenario_id for e in r.entries] == ["B", "C", "A"]
        top = r.entries[0]
        assert sum(x.contribution for x in top.contributions) == top.score
        assert top.contributions[0].raw == D(200) and r.weights[0] == (
            "objective",
            MetricDirection.MAXIMIZE,
            D(3),
        )
        assert "min-max" in (r.normalization or "")

    def test_weights_are_never_defaulted_or_invalid(self) -> None:
        c = cands(A={"objective": "1"})
        assert (
            rank_scenarios(M.MAXIMIZE_MARGIN, RankingMode.WEIGHTED_PREFERENCE, c).status
            == RankingStatus.MISSING_CONFIGURATION
        )
        for bad in ("0", "-1"):
            r = rank_scenarios(
                M.MAXIMIZE_MARGIN,
                RankingMode.WEIGHTED_PREFERENCE,
                c,
                preferences=self.prefs(objective=bad),
            )
            assert r.status == RankingStatus.INVALID_CONFIGURATION
        dup = (*self.prefs(objective="1"), *self.prefs(objective="2"))
        assert (
            rank_scenarios(
                M.MAXIMIZE_MARGIN, RankingMode.WEIGHTED_PREFERENCE, c, preferences=dup
            ).status
            == RankingStatus.INVALID_CONFIGURATION
        )
        with pytest.raises(ValidationError):  # binary floats are refused as weights
            Preference(metric="objective", direction=MetricDirection.MAXIMIZE, weight=0.5)

    def test_zero_range_metric_is_non_discriminating(self) -> None:
        c = cands(A={"objective": "100", "vol": "7"}, B={"objective": "200", "vol": "7"})
        r = rank_scenarios(
            M.MAXIMIZE_MARGIN,
            RankingMode.WEIGHTED_PREFERENCE,
            c,
            preferences=self.prefs(objective="1", vol="1"),
        )
        assert [e.scenario_id for e in r.entries] == ["B", "A"]
        flat = next(x for x in r.entries[0].contributions if x.metric == "vol")
        assert flat.normalized is None and flat.contribution == 0 and r.entries[0].score == D("0.5")

    def test_all_equal_scenarios_tie(self) -> None:
        c = cands(A={"objective": "5", "vol": "7"}, B={"objective": "5", "vol": "7"})
        r = rank_scenarios(
            M.MAXIMIZE_MARGIN,
            RankingMode.WEIGHTED_PREFERENCE,
            c,
            preferences=self.prefs(objective="1", vol="2"),
        )
        assert r.winners == ("A", "B") and all(e.score == 0 for e in r.entries)

    def test_hard_constraint_overrides_a_better_weighted_score(self) -> None:
        c = cands(
            A={"objective": "100", "vol": "10", "shortfall": "0"},
            B={"objective": "900", "vol": "50", "shortfall": "3"},
        )
        hc = (HardConstraint(metric="shortfall", op="<=", threshold="0"),)
        r = rank_scenarios(
            M.MAXIMIZE_MARGIN,
            RankingMode.WEIGHTED_PREFERENCE,
            c,
            hc,
            self.prefs(objective="10", vol="1"),
        )
        assert [e.scenario_id for e in r.entries] == ["A"]
        assert r.rejected[0].scenario_id == "B"

    def test_missing_metric_excludes_instead_of_zero(self) -> None:
        c = cands(A={"objective": "1", "vol": "5"}, B={"objective": "9"})
        r = rank_scenarios(
            M.MAXIMIZE_MARGIN,
            RankingMode.WEIGHTED_PREFERENCE,
            c,
            preferences=self.prefs(objective="1", vol="1"),
        )
        assert [e.scenario_id for e in r.entries] == ["A"]
        assert r.rejected[0].reasons == ("metric unavailable: vol",)


# ------------------------------------------------------------------ Pareto


class TestPareto:
    OBJ = (
        MetricObjective(metric="profit", direction=MetricDirection.MAXIMIZE),
        MetricObjective(metric="cost", direction=MetricDirection.MINIMIZE),
    )

    def test_dominated_and_non_dominated_with_mixed_directions(self) -> None:
        c = cands(
            A={"profit": "10", "cost": "5"},
            B={"profit": "12", "cost": "5"},  # dominates A
            C={"profit": "15", "cost": "9"},  # trades profit for cost: non-dominated
        )
        p = pareto(c, self.OBJ)
        assert p.non_dominated == ("B", "C")
        (d,) = p.dominated
        assert (
            d.scenario_id == "A" and d.dominated_by == ("B",) and "strictly better" in d.explanation
        )
        assert "not universally best" in p.note

    def test_equal_scenarios_are_both_non_dominated(self) -> None:
        c = cands(A={"profit": "10", "cost": "5"}, B={"profit": "10.0000000001", "cost": "5"})
        p = pareto(c, self.OBJ)
        assert (
            p.non_dominated == ("A", "B") and p.equal_groups == (("A", "B"),) and p.dominated == ()
        )

    def test_missing_metric_is_excluded_not_zero(self) -> None:
        c = cands(A={"profit": "10", "cost": "5"}, B={"profit": "99"})
        p = pareto(c, self.OBJ)
        assert p.non_dominated == ("A",) and p.excluded == (("B", "metric unavailable: cost"),)

    def test_configuration_states(self) -> None:
        c = cands(A={"profit": "1"})
        assert pareto(c, ()).status == ParetoStatus.NOT_CONFIGURED
        dup = (self.OBJ[0], self.OBJ[0])
        assert pareto(c, dup).status == ParetoStatus.INVALID_CONFIGURATION
        assert pareto(c, self.OBJ).status == ParetoStatus.NO_ELIGIBLE_SCENARIOS

    def test_incompatible_scenarios_never_reach_pareto(self) -> None:
        _bp, base, runs = mix_runs()
        raw = example_product_mix()
        for p in raw["products"]:
            p["unit"] = "kg"
        for cns in raw["consumptions"]:
            cns["product_unit"] = "kg"
        p2 = validate_planning_dict(raw)
        odd = mk_run("ODD", p2, compile_problem(p2), (20.0, 60.0), 2600.0)
        cfg = DecisionConfig(
            pareto_objectives=(
                MetricObjective(metric="objective", direction=MetricDirection.MAXIMIZE),
            )
        )
        brief = decide(base, [runs["MACH120"], odd], cfg)
        assert "ODD" not in brief.pareto.non_dominated and "ODD" not in {
            d.scenario_id for d in brief.pareto.dominated
        }

    def test_pareto_through_the_engine(self) -> None:
        _bp, base, runs = mix_runs()
        cfg = DecisionConfig(
            pareto_objectives=(
                MetricObjective(metric="objective", direction=MetricDirection.MAXIMIZE),
                MetricObjective(metric="total_volume", direction=MetricDirection.MAXIMIZE),
            )
        )
        brief = decide(base, [runs["MACH120"], runs["MACH80"]], cfg)
        assert brief.pareto.non_dominated == ("MACH120",)
        assert {d.scenario_id for d in brief.pareto.dominated} == {"BASE", "MACH80"}


# ------------------------------------------------------------------ recommendations


ACK = frozenset({"CAPACITY_CHANGE_COST_NOT_MODELED", "UNIT_VALIDATION_NOT_PERFORMED"})


class TestRecommendation:
    def test_clear_winner_when_assumptions_are_acknowledged(self) -> None:
        _bp, base, runs = mix_runs()
        brief = decide(
            base, [runs["MACH120"], runs["MACH80"]], DecisionConfig(acknowledged_assumptions=ACK)
        )
        r = brief.recommendation
        assert (
            r.status == RecommendationStatus.RECOMMENDED and r.recommended_scenario_id == "MACH120"
        )
        assert r.supporting_metrics["objective"] == D("2800.0")
        assert r.binding_resources == ("LABOR", "MACHINE") and r.human_approval_required is True
        assert r.coverage.exact_optimality_proof is False
        assert r.coverage.verification_class == "NUMERICAL_GAP_VERIFIED"

    def test_conditional_when_assumptions_are_unresolved(self) -> None:
        _bp, base, runs = mix_runs()
        r = decide(base, [runs["MACH120"]]).recommendation
        assert r.status == RecommendationStatus.CONDITIONALLY_RECOMMENDED
        assert r.unresolved_assumptions == (
            "CAPACITY_CHANGE_COST_NOT_MODELED",
            "UNIT_VALIDATION_NOT_PERFORMED",
        )
        assert r.conditions and r.evidence_limitations

    def test_no_clear_winner_on_tie(self) -> None:
        _bp, base, runs = mix_runs()
        r = decide(base, [runs["MACH120"], runs["MACH125"]]).recommendation
        assert (
            r.status == RecommendationStatus.NO_CLEAR_WINNER and r.recommended_scenario_id is None
        )
        assert "MACH120" in r.reasons[0] and "MACH125" in r.reasons[0]

    def test_no_verified_recommendation(self) -> None:
        bp = base_problem()
        bad = mk_run("BASE", bp, compile_problem(bp), kind="none")
        r = decide(bad, []).recommendation
        assert r.status == RecommendationStatus.NO_VERIFIED_RECOMMENDATION
        assert r.recommended_scenario_id is None and r.human_approval_required

    def test_baseline_wins_when_no_scenario_is_better(self) -> None:
        _bp, base, runs = mix_runs()
        plain = decide(base, [runs["MACH80"]]).recommendation  # NOT_ADMITTED run => conditional
        assert plain.status == RecommendationStatus.CONDITIONALLY_RECOMMENDED
        assert "UNIT_VALIDATION_NOT_PERFORMED" in plain.unresolved_assumptions
        cfg = DecisionConfig(acknowledged_assumptions=ACK)
        r = decide(base, [runs["MACH80"]], cfg).recommendation
        assert r.status == RecommendationStatus.RECOMMENDED and r.recommended_scenario_id == "BASE"
        assert "no tested scenario outperforms" in r.key_improvements[0]

    def test_untrusted_scenarios_make_it_conditional(self) -> None:
        _bp, base, runs = mix_runs()
        s = runs["MACH125"]
        unknown = mk_run("MACH125", s.problem, s.formulation, (40.0, 40.0), kind="unknown")
        r = decide(
            base, [runs["MACH120"], unknown], DecisionConfig(acknowledged_assumptions=ACK)
        ).recommendation
        assert r.status == RecommendationStatus.CONDITIONALLY_RECOMMENDED
        assert any("lacked verified results" in c for c in r.conditions)
        assert r.coverage.rejected_untrusted == 1

    def test_human_review_when_preferences_are_missing(self) -> None:
        _bp, base, runs = mix_runs()
        cfg = DecisionConfig(ranking_mode=RankingMode.WEIGHTED_PREFERENCE)
        r = decide(base, [runs["MACH120"]], cfg).recommendation
        assert (
            r.status == RecommendationStatus.REQUIRES_HUMAN_REVIEW
            and "MISSING_CONFIGURATION" in r.reasons[0]
        )

    def test_human_review_when_hard_constraints_cannot_be_met(self) -> None:
        _bp, base, runs = mix_runs()
        cfg = DecisionConfig(
            hard_constraints=(HardConstraint(metric="bottleneck_count", op="<=", threshold="0"),)
        )
        r = decide(base, [runs["MACH120"]], cfg).recommendation
        assert r.status == RecommendationStatus.REQUIRES_HUMAN_REVIEW

    def test_hard_constraint_redirects_the_winner(self) -> None:
        _bp, base, runs = mix_runs()
        cfg = DecisionConfig(
            ranking_mode=RankingMode.CONSTRAINT_FIRST,
            hard_constraints=(HardConstraint(metric="objective", op="<=", threshold="2700"),),
            acknowledged_assumptions=ACK,
        )
        r = decide(base, [runs["MACH120"], runs["MACH80"]], cfg).recommendation
        assert r.recommended_scenario_id == "BASE"  # MACH120 violates the explicit requirement

    def test_weighted_ranking_through_the_engine(self) -> None:
        _bp, base, runs = mix_runs()
        cfg = DecisionConfig(
            ranking_mode=RankingMode.WEIGHTED_PREFERENCE,
            preferences=(
                Preference(metric="objective", direction=MetricDirection.MAXIMIZE, weight="1"),
                Preference(
                    metric="mean_utilization", direction=MetricDirection.MINIMIZE, weight="1"
                ),
            ),
            acknowledged_assumptions=ACK,
        )
        brief = decide(base, [runs["MACH120"], runs["MACH80"]], cfg)
        assert brief.ranking is not None and brief.ranking.method == RankingMode.WEIGHTED_PREFERENCE
        assert brief.ranking.entries[0].contributions  # explanation retained
        assert brief.objective_first_ranking is not None


# ------------------------------------------------------------------ brief


class TestBrief:
    def test_deterministic_and_machine_readable(self) -> None:
        _bp, base, runs = mix_runs()
        a = decide(base, [runs["MACH120"], runs["MACH80"]])
        b = decide(base, [runs["MACH80"], runs["MACH120"]])  # input order must not matter
        assert a.brief_sha256 == b.brief_sha256 and brief_json(a) == brief_json(b)
        doc_ = json.loads(brief_json(a))
        for key in (
            "executive_summary",
            "comparison",
            "trade_offs",
            "pareto",
            "ranking",
            "recommendation",
            "assumptions",
            "limitations",
            "evidence",
            "observed_sensitivity",
        ):
            assert key in doc_
        assert doc_["recommendation"]["human_approval_required"] is True
        c = decide(base, [runs["MACH120"]], DecisionConfig(acknowledged_assumptions=ACK))
        assert c.brief_sha256 != a.brief_sha256

    def test_evidence_and_lineage_references(self) -> None:
        _bp, base, runs = mix_runs()
        brief = decide(base, [runs["MACH120"]])
        ref = {e.scenario_id: e for e in brief.evidence}
        assert ref["MACH120"].model_sha256 != ref["BASE"].model_sha256
        assert "scenario:MACH120" in ref["MACH120"].lineage
        assert ref["BASE"].optimality_tier == "GAP_NUMERICAL"
        assert any(v.startswith("DUAL_GAP:NUMERICAL") for v in ref["BASE"].verification_levels)
        assert not any(
            "EXACT_RATIONAL:VERIFIED" in v and v.startswith("DUAL_GAP")
            for v in ref["BASE"].verification_levels
        )

    def test_summary_numbers_come_from_trusted_records_and_label_gap_numerical(self) -> None:
        _bp, base, runs = mix_runs()
        text = " ".join(decide(base, [runs["MACH120"]]).executive_summary)
        assert "2600" in text and "2800" in text and "GAP_NUMERICAL" in text
        assert "Human approval is required" in text

    def test_observed_sensitivity_is_labeled_and_math_sensitivity_unavailable(self) -> None:
        _bp, base, runs = mix_runs()
        brief = decide(base, [runs["MACH120"]])
        (o,) = brief.observed_sensitivity
        assert o.classification == "OBSERVED_SCENARIO_BEHAVIOR"
        assert o.changed_inputs == ("capacity:MACHINE:100->120",)
        assert o.mathematical_sensitivity.startswith("UNAVAILABLE")
        assert o.objective_absolute_change == D("200.0")


# ------------------------------------------------------------------ numerical correctness


class TestNumerics:
    def test_relative_change_cases(self) -> None:
        assert relative_change(D("0.1"), D("0.3")) == D(2)  # decimal precision
        assert relative_change(D(0), D(5)) is None  # zero denominator
        assert relative_change(D(-100), D(-50)) == D("0.5")  # negative baseline uses |base|
        assert relative_change(D(-100), D(-150)) == D("-0.5")
        assert relative_change(D("1e12"), D("1e12") + 1) == D(1) / D("1e12")  # large values
        assert relative_change(D("1e-30"), D("2e-30")) == D(1)

    def test_float_noise_is_tolerated_but_real_differences_are_not(self) -> None:
        assert close(D(repr(2.0000000000000004)), D(2))
        assert not close(D(2), D("2.001"))
        assert close(D("1e12"), D("1e12") + D("1e5"))  # relative scale
        assert not close(D(0), D("1e-3"))

    def test_zero_baseline_objective_has_no_fake_percentage(self) -> None:
        bp = base_problem()
        base = mk_run("BASE", bp, compile_problem(bp), (0.0, 0.0), 0.0)  # feasible, objective 0
        s = scen(bp, "MACH120", CapacityChange(resource_id="MACHINE", capacity="120"))
        run = mk_run("MACH120", s.problem, s.formulation, (40.0, 40.0), 2800.0)
        o = decide(base, [run]).comparison.compared[0].objective  # type: ignore[union-attr]
        assert o is not None and o.relative_change is None and "zero" in o.relative_note
        assert o.absolute_change == D("2800.0")
        assert any(
            "n/a" in line or "2800" in line for line in decide(base, [run]).executive_summary
        )

    def test_negative_objective_improvement(self) -> None:
        raw = doc("MAXIMIZE_MARGIN", [("A", "-2", "0", "10")], [("R", "100")], {("R", "A"): "1"})
        bp = validate_planning_dict(raw)
        base = mk_run("BASE", bp, compile_problem(bp), (5.0,), -10.0)
        s = scen(bp, "A2", DemandBoundsChange(product_id="A", max_quantity="2"))
        run = mk_run("A2", s.problem, s.formulation, (2.0,), -4.0)  # still feasible, less negative
        c = decide(base, [run]).comparison.compared[0]  # type: ignore[union-attr]
        assert c.objective is not None
        assert c.objective.absolute_change == D("6.0") and c.objective.relative_change == D("0.6")
        assert c.objective.effect.value == "IMPROVED"

    def test_fmt(self) -> None:
        assert (
            fmt(D("2800.000000")) == "2800" and fmt(D("-0.0000001")) == "0" and fmt(None) == "n/a"
        )
        assert fmt(D("0.12345678"), 4) == "0.1235"


# ------------------------------------------------------------------ final-audit regressions


class TestAuditRegressions:
    def test_spec_manifest_swap_with_a_consistent_result_is_rejected(self) -> None:
        # spec + result + hash of MACH120, but the BASE problem and BASE manifest: before the
        # recompilation check this passed every hash test and produced a mislabeled report.
        bp, base, runs = mix_runs()
        s = runs["MACH120"]
        mixed = CompiledFormulation(s.formulation.spec_json, base.formulation.manifest)
        forged = ScenarioRun("BASE", bp, mixed, s.result, s.model_sha256)
        assert code_of(admit(forged)) == "FORMULATION_TAMPERED"

    def test_lying_runtime_objective_is_rejected(self) -> None:
        bp = base_problem()
        f = compile_problem(bp)
        m = model_of(f)
        lie = ScenarioRun(
            "B", bp, f, solved_dto(m, (20.0, 60.0), m.variable_ids, 1.0), m.model_sha256
        )
        assert code_of(admit(lie)) == "OBJECTIVE_MISMATCH"
        brief = decide(lie, [])
        assert brief.recommendation.status == RecommendationStatus.NO_VERIFIED_RECOMMENDATION

    def test_different_consumption_data_is_not_a_what_if_even_with_forged_lineage(self) -> None:
        _bp, base, _ = mix_runs()
        raw = example_product_mix()
        raw["consumptions"][0]["amount_per_unit"] = "3"
        p2 = validate_planning_dict(raw)
        forged_lineage = (
            f"base_input_sha256:{admit(base).input_sha256}",  # type: ignore[union-attr]
            "scenario:ODD",
        )
        f2 = compile_problem(p2, problem_id="PP-TEST::ODD", lineage=forged_lineage)
        odd = mk_run("ODD", p2, f2, (20.0, 60.0), 2600.0)
        brief = decide(base, [odd])
        assert [r.code for r in brief.rejected_scenarios] == ["INCOMPATIBLE_PROBLEM"]

    def test_forged_lineage_alone_cannot_admit_an_identical_problem(self) -> None:
        bp, base, _ = mix_runs()
        lineage = (f"base_input_sha256:{admit(base).input_sha256}", "scenario:CLONE")  # type: ignore[union-attr]
        f = compile_problem(bp, problem_id="PP-TEST::CLONE", lineage=lineage)
        clone = mk_run("CLONE", bp, f, (20.0, 60.0), 2600.0)
        assert [r.code for r in decide(base, [clone]).rejected_scenarios] == ["LINEAGE_MISMATCH"]

    def test_rejection_codes_count_as_untrusted_evidence(self) -> None:
        _bp, base, runs = mix_runs()
        s = runs["MACH120"]
        lie = ScenarioRun(
            "MACH120",
            s.problem,
            s.formulation,
            solved_dto(
                model_of(s.formulation), (40.0, 40.0), model_of(s.formulation).variable_ids, 5.0
            ),
            s.model_sha256,
        )
        r = decide(base, [lie], DecisionConfig(acknowledged_assumptions=ACK)).recommendation
        assert r.coverage.rejected_untrusted == 1 and r.recommended_scenario_id == "BASE"
        assert r.status == RecommendationStatus.CONDITIONALLY_RECOMMENDED

    def test_fmt_survives_values_beyond_the_context_precision(self) -> None:
        assert fmt(D("1e60")).startswith("1000000")
        assert fmt(D("-1e60")).startswith("-1000000")

    def test_brief_hash_changes_when_any_trusted_number_changes(self) -> None:
        _bp, base, runs = mix_runs()
        a = decide(base, [runs["MACH120"]])
        s = runs["MACH120"]
        other = mk_run("MACH120", s.problem, s.formulation, (40.0, 40.0 - 1e-3), 2800.0 - 0.03)
        assert other.result is not None
        b = decide(base, [other])
        assert not b.rejected_scenarios and b.brief_sha256 != a.brief_sha256

# ruff: noqa: E501, E731, N817
"""Optimization-Simulation Co-Orchestration (OSCO).

Real HiGHS solves through the STRICT admission gate; real Digital Twin simulations. All factory data
is SYNTHETIC. Expected values of the hand-checked journey are computed independently: the LP optimum
by exhaustive vertex enumeration with exact fractions, and simulated completion times from the shift
calendar arithmetic (not from the engine's own output).
"""

from __future__ import annotations

import itertools
import sqlite3
import threading
from decimal import Decimal as D
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from qorvexa.industrial.co_orchestration import (
    Budget,
    CandidateOrigin,
    CandidateSpec,
    CandidateStatus,
    CoOrchestrator,
    DecisionSpec,
    ExperimentConfig,
    InMemoryExperimentStore,
    OperationalRequirements,
    OscoStoreError,
    PlanConversion,
    RecommendationStatus,
    ResourceBinding,
    SensitivitySpec,
    SimulationConfig,
    SqliteExperimentStore,
    StopReason,
    contradictions,
    productive_seconds,
    verify_package,
)
from qorvexa.industrial.co_orchestration import optimize as osco_opt
from qorvexa.industrial.decision_intelligence.models import (
    HardConstraint,
    MetricDirection,
    MetricObjective,
    RankingMode,
)
from qorvexa.industrial.digital_twin import (
    AddBreakdown,
    AddMachine,
    AddPlannedDowntime,
    Breakdown,
    Buffer,
    Calendar,
    Dist,
    DistKind,
    FactoryModel,
    FailureModel,
    Interval,
    Machine,
    Operation,
    Qty,
    QuantityPolicy,
    Routing,
    SetBufferCapacity,
    SetProcessingTime,
    Shift,
    fixed,
    import_plan,
    simulate,
)
from qorvexa.industrial.digital_twin import (
    Product as TwinProduct,
)
from qorvexa.industrial.production_planning import (
    Consumption,
    ObjectiveMode,
    PlanningMetadata,
    PlanningProblem,
    Product,
    Resource,
    ResourceKind,
)
from qorvexa.industrial.production_planning.scenario import (
    CapacityChange,
    DemandBoundsChange,
    ScenarioSpec,
)
from qorvexa.solver_fabric import LPOrchestrator
from qorvexa.solver_fabric.highs_adapter import HighsAdapter

CLOCK = lambda: "2026-01-01T00:00:00+00:00"
DUE = 16 * 3600


def q(v: object, u: str = "s") -> Qty:
    return Qty(value=str(v), unit=u)


# ============================================================================ synthetic factory
def problem(weld_h: str = "8", cut_h: str = "8", weld_b: str = "0.05") -> PlanningProblem:
    return PlanningProblem(
        metadata=PlanningMetadata(problem_id="PP-SYN", title="synthetic two-product line"),
        mode=ObjectiveMode.MAXIMIZE_MARGIN, currency="EGP",
        products=(Product(id="A", name="A", unit="piece", value_per_unit=D(10), value_currency="EGP", max_quantity=D(100)),
                  Product(id="B", name="B", unit="piece", value_per_unit=D(6), value_currency="EGP", max_quantity=D(200))),
        resources=(Resource(id="WELD_H", name="welding hours", kind=ResourceKind.MACHINE, unit="h", capacity=D(weld_h)),
                   Resource(id="CUT_H", name="cutting hours", kind=ResourceKind.MACHINE, unit="h", capacity=D(cut_h))),
        consumptions=(Consumption(resource_id="WELD_H", product_id="A", amount_per_unit=D("0.1"), resource_unit="h", product_unit="piece"),
                      Consumption(resource_id="WELD_H", product_id="B", amount_per_unit=D(weld_b), resource_unit="h", product_unit="piece"),
                      Consumption(resource_id="CUT_H", product_id="A", amount_per_unit=D("0.05"), resource_unit="h", product_unit="piece"),
                      Consumption(resource_id="CUT_H", product_id="B", amount_per_unit=D("0.025"), resource_unit="h", product_unit="piece")),
    )  # fmt: skip


def twin(weld_b_s: str = "180", **kw: Any) -> FactoryModel:
    cut_cal = Calendar(
        calendar_id="cut",
        shifts=(Shift(name="day", start="07:00", end="16:00", breaks=(("12:00", "12:30"),)),),
    )
    weld_cal = Calendar(
        calendar_id="weld",
        shifts=(Shift(name="day", start="08:00", end="16:00", breaks=(("12:00", "12:30"),)),),
    )
    data: dict[str, Any] = dict(
        model_id="osco-line", factory_id="F1", horizon=q(1, "day"),
        machines=(Machine(machine_id="CUT", calendar_id="cut"), Machine(machine_id="WELD", calendar_id="weld")),
        buffers=(Buffer(buffer_id="B0", capacity="UNBOUNDED"), Buffer(buffer_id="B1", capacity=20)),
        calendars=(cut_cal, weld_cal),
        routings=(
            Routing(routing_id="RA", operations=(Operation(op_id="CUT_A", input_buffer="B0", machines=("CUT",), processing=fixed("180", "s")),
                                                  Operation(op_id="WELD_A", input_buffer="B1", machines=("WELD",), processing=fixed("360", "s")))),
            Routing(routing_id="RB", operations=(Operation(op_id="CUT_B", input_buffer="B0", machines=("CUT",), processing=fixed("90", "s")),
                                                  Operation(op_id="WELD_B", input_buffer="B1", machines=("WELD",), processing=fixed(weld_b_s, "s")))),
        ),
        products=(TwinProduct(product_id="A", routing_id="RA"), TwinProduct(product_id="B", routing_id="RB")),
        orders=(),
    )  # fmt: skip
    data.update(kw)
    return FactoryModel(**data)


def config(**kw: Any) -> ExperimentConfig:
    data: dict[str, Any] = dict(
        experiment_label="synthetic journey",
        bindings=(ResourceBinding(resource_id="WELD_H", machine_ids=("WELD", "WELD2")), ResourceBinding(resource_id="CUT_H", machine_ids=("CUT",))),
        conversion=PlanConversion(policy=QuantityPolicy.EXACT_INTEGER_ONLY, batch_sizes={"A": 1, "B": 1}, release_at=q(0), due_at=q(DUE)),
        requirements=OperationalRequirements(max_late_orders=0),
        simulation=SimulationConfig(),
        decision=DecisionSpec(ranking_mode=RankingMode.OBJECTIVE_FIRST,
                              pareto_objectives=(MetricObjective(metric="objective", direction=MetricDirection.MAXIMIZE),
                                                 MetricObjective(metric="sim_makespan_s", direction=MetricDirection.MINIMIZE))),
        budget=Budget(max_iterations=3, max_candidates=12),
        refine_on_bottleneck=True,
    )  # fmt: skip
    data.update(kw)
    return ExperimentConfig(**data)


class CountingSolver:
    """The real HiGHS orchestrator, counting calls (no fabricated results)."""

    def __init__(self) -> None:
        self.inner = LPOrchestrator(HighsAdapter())
        self.calls = 0

    def solve_json(self, spec_json: str, job_id: str | None = None) -> Any:
        self.calls += 1
        return self.inner.solve_json(spec_json, job_id=job_id)


def engine(store: Any = None) -> tuple[CoOrchestrator, CountingSolver]:
    s = CountingSolver()
    return CoOrchestrator(s, store, clock=CLOCK), s  # type: ignore[arg-type]


# ============================================================================ independent references
def lp_optimum(p: PlanningProblem) -> tuple[Fraction, dict[str, Fraction]]:
    """Exhaustive vertex enumeration of the 2-variable LP with exact fractions."""
    ids = [x.id for x in p.products]
    rows: list[tuple[list[Fraction], Fraction]] = []
    for r in p.resources:
        rows.append(
            (
                [
                    Fraction(
                        next(
                            (
                                c.amount_per_unit
                                for c in p.consumptions
                                if c.resource_id == r.id and c.product_id == i
                            ),
                            0,
                        )
                    )
                    for i in ids
                ],
                Fraction(r.capacity),
            )
        )
    for k, prod in enumerate(ids):
        e = [Fraction(0)] * len(ids)
        e[k] = Fraction(1)
        mx = next(x.max_quantity for x in p.products if x.id == prod)
        if mx is not None:
            rows.append((e, Fraction(mx)))
        rows.append(
            ([-v for v in e], -Fraction(next(x.min_quantity for x in p.products if x.id == prod)))
        )
    best: tuple[Fraction, tuple[Fraction, Fraction]] | None = None
    for (a1, b1), (a2, b2) in itertools.combinations(rows, 2):
        det = a1[0] * a2[1] - a1[1] * a2[0]
        if det == 0:
            continue
        x = (b1 * a2[1] - a1[1] * b2) / det
        y = (a1[0] * b2 - b1 * a2[0]) / det
        if all(a[0] * x + a[1] * y <= b for a, b in rows):
            val = sum(
                Fraction(pp.value_per_unit) * v for pp, v in zip(p.products, (x, y), strict=True)
            )
            if best is None or val > best[0]:
                best = (val, (x, y))
    assert best is not None
    return best[0], dict(zip(ids, best[1], strict=True))


def weld_finish(
    n_b: int,
    start: int = 8 * 3600,
    per: int = 180,
    brk: tuple[int, int] = (12 * 3600, 12 * 3600 + 1800),
    end: int = DUE,
) -> int | None:
    """Completion of the n-th B piece on a continuously fed welder with one break; None if the
    shift ends first (hand arithmetic of the calendar, independent of the simulator)."""
    before = (brk[0] - start) // per
    if n_b <= before:
        return start + n_b * per
    t = brk[1] + (n_b - before) * per
    return t if t <= end else None


# ============================================================================ the hand-checked journey
class TestHandCheckedJourney:
    def test_optimal_plan_fails_in_simulation_and_refined_plan_is_recommended(self) -> None:
        eng, solver = engine()
        pkg = eng.run(problem(), twin(), config())
        c = {x.spec.origin: x for x in pkg.candidates}
        base, ref = c[CandidateOrigin.BASELINE], c[CandidateOrigin.REFINEMENT]

        # optimization: matches exhaustive vertex enumeration
        opt_val, opt_x = lp_optimum(problem())
        assert (opt_val, opt_x) == (960, {"A": 0, "B": 160})
        assert (
            D(base.math.objective) == 960
            and base.math.verified
            and base.math.admission_status == "ADMITTED_UNITS_VERIFIED"
        )
        assert dict((p, D(v)) for p, v, _ in base.math.quantities) == {"A": 0, "B": 160}

        # simulation of the optimum: the welder works 7.5 h until 16:00 -> 150 pieces, 10 short
        assert weld_finish(150) == DUE and weld_finish(151) is None
        assert base.status is CandidateStatus.OPERATIONAL_VIOLATIONS
        assert base.sim.metrics["sim_completed"] == 150 and base.sim.planned_pieces == 160
        assert {v.code for v in base.violations} >= {
            "PLAN_NOT_ACHIEVED",
            "LATE_ORDERS",
            "INCOMPLETE_WITHIN_HORIZON",
        }
        assert base.sim.bottlenecks[0] == "WELD"

        # justified refinement: scheduled productive welding time in [0, 16:00] is 7.5 h
        assert productive_seconds(twin(), ("WELD",), DUE) == 27_000
        assert ref.spec.parent_id == base.candidate_id and "7.5 h" in ref.spec.justification
        assert ref.spec.planning.changes == (
            CapacityChange(resource_id="WELD_H", capacity=D("7.5")),
        )
        ref_val, ref_x = lp_optimum(problem(weld_h="7.5"))
        assert (ref_val, ref_x) == (900, {"A": 0, "B": 150})
        assert D(ref.math.objective) == 900 and ref.math.verified

        # the refined plan finishes exactly at the due time and is operationally feasible
        assert ref.status is CandidateStatus.OPERATIONALLY_FEASIBLE and not ref.violations
        assert ref.sim.metrics["sim_makespan_s"] == weld_finish(150) == DUE
        assert ref.sim.metrics["sim_late_orders"] == 0

        # recommendation: lower objective, operationally superior; trade-off visible; not approved
        rec = pkg.recommendation
        assert (
            rec.status is RecommendationStatus.RECOMMENDED_FOR_HUMAN_REVIEW
            and rec.candidate_id == ref.candidate_id
        )
        assert rec.human_approval_required and not rec.approved
        assert any("+60" in t and base.candidate_id in t for t in rec.trade_offs)
        assert any("mathematically optimal baseline" in r for r in rec.rationale)
        assert pkg.stop_reason is StopReason.CONVERGED and len(pkg.iterations) == 2
        assert pkg.pareto is not None and pkg.pareto.non_dominated == (ref.candidate_id,)
        assert verify_package(pkg) and solver.calls == 2

    def test_simulation_evidence_is_independently_reproducible(self) -> None:
        eng, _ = engine()
        pkg = eng.run(problem(), twin(), config())
        ref = next(x for x in pkg.candidates if x.spec.origin is CandidateOrigin.REFINEMENT)
        # re-simulate the refined plan through the public twin API and compare hashes
        from qorvexa.industrial.production_planning import ProductionReport, ReportStatus
        from qorvexa.industrial.production_planning.report import ProductQuantity

        rep = ProductionReport(
            status=ReportStatus.VERIFIED_NUMERICAL_SOLUTION,
            reason="",
            problem_id="PP-SYN",
            mode=ObjectiveMode.MAXIMIZE_MARGIN,
            quantities=tuple(
                ProductQuantity(product_id=p, variable_id=p, unit=u, quantity=D(v))
                for p, v, u in ref.math.quantities
            ),
        )
        plan = import_plan(
            rep,
            policy=QuantityPolicy.EXACT_INTEGER_ONLY,
            batch_sizes={"A": 1, "B": 1},
            release_at=q(0),
            due_at=q(DUE),
        )
        again = simulate(
            twin().model_copy(update={"orders": plan.orders}), scenario_id=ref.spec.label[:60]
        )
        assert again.replications[0].event_digest and again.metrics.window_s == DUE
        assert (
            again.content_sha256 != ""
            and ref.sim.metrics["sim_completed"] == again.metrics.completed
        )


# ============================================================================ math-side safety
class TestMathSafety:
    def test_infeasible_candidate_is_never_simulated(self) -> None:
        bad = CandidateSpec(
            label="demand 300 B",
            origin=CandidateOrigin.PORTFOLIO,
            planning=ScenarioSpec(
                scenario_id="d300",
                changes=(
                    DemandBoundsChange(product_id="B", min_quantity=D(300), max_quantity=D(300)),
                ),
            ),
        )
        pkg = engine()[0].run(
            problem(), twin(), config(portfolio=(bad,), refine_on_bottleneck=False)
        )
        c = next(x for x in pkg.candidates if x.spec.label == "demand 300 B")
        assert c.status is CandidateStatus.MATH_INFEASIBLE and c.sim is None
        assert (
            c.math is not None
            and c.math.report_status == "INFEASIBLE_VERIFIED"
            and not c.math.verified
        )

    def test_invalid_admission_never_reaches_the_solver(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        minutes = problem().model_copy(
            update={
                "resources": (
                    problem().resources[0],
                    Resource(
                        id="CUT_H",
                        name="cutting",
                        kind=ResourceKind.MACHINE,
                        unit="min",
                        capacity=D(480),
                    ),
                ),
                "consumptions": (
                    *problem().consumptions[:2],
                    Consumption(
                        resource_id="CUT_H",
                        product_id="A",
                        amount_per_unit=D(3),
                        resource_unit="min",
                        product_unit="piece",
                    ),
                    Consumption(
                        resource_id="CUT_H",
                        product_id="B",
                        amount_per_unit=D("1.5"),
                        resource_unit="min",
                        product_unit="piece",
                    ),
                ),
            }
        )
        other = osco_opt.unit_evidence_for(
            minutes
        )  # evidence with different units than the problem
        monkeypatch.setattr(
            osco_opt, "unit_evidence_for", lambda p: other
        )  # evidence of another problem
        eng, solver = engine()
        pkg = eng.run(problem(), twin(), config())
        base = pkg.candidates[0]
        assert base.status is CandidateStatus.ADMISSION_REJECTED and solver.calls == 0
        assert pkg.stop_reason is StopReason.BASELINE_FAILED
        assert pkg.recommendation.status is RecommendationStatus.BASELINE_NOT_VERIFIED

    def test_tampered_solver_output_is_not_verified(self) -> None:
        class Forger(CountingSolver):
            def solve_json(self, spec_json: str, job_id: str | None = None) -> Any:
                out = super().solve_json(spec_json, job_id)
                return (
                    type(out)(**{**out.__dict__, "model_sha256": "0" * 64})
                    if hasattr(out, "__dict__")
                    else out
                )

        f = Forger()
        out = osco_opt.solve_and_verify(f, "c", problem())  # type: ignore[arg-type]
        assert out.status is CandidateStatus.NOT_VERIFIED and out.admitted is None and f.calls == 1

    def test_conflicting_and_incompatible_scenarios(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="ambiguous"):
            ScenarioSpec(
                scenario_id="c",
                changes=(
                    CapacityChange(resource_id="WELD_H", capacity=D(9)),
                    CapacityChange(resource_id="WELD_H", capacity=D(10)),
                ),
            )
        bad_twin = CandidateSpec(
            label="ghost machine",
            origin=CandidateOrigin.PORTFOLIO,
            twin_changes=(
                AddPlannedDowntime(machine_id="GHOST", interval=Interval(start=q(0), end=q(60))),
            ),
        )
        unknown_res = CandidateSpec(
            label="unknown resource",
            origin=CandidateOrigin.PORTFOLIO,
            planning=ScenarioSpec(
                scenario_id="u", changes=(CapacityChange(resource_id="NOPE", capacity=D(1)),)
            ),
        )
        pkg = engine()[0].run(
            problem(), twin(), config(portfolio=(bad_twin, unknown_res), refine_on_bottleneck=False)
        )
        by = {x.spec.label: x for x in pkg.candidates}
        for label in ("ghost machine", "unknown resource"):
            assert by[label].status is CandidateStatus.SCENARIO_INVALID, label
        assert "UNKNOWN_REFERENCE" in by["unknown resource"].reasons[0]


# ============================================================================ simulation-side safety
class TestSimulationSafety:
    def test_event_limit_makes_runs_incomplete_and_unrecommendable(self) -> None:
        pkg = engine()[0].run(
            problem(),
            twin(),
            config(simulation=SimulationConfig(max_events=5), refine_on_bottleneck=False),
        )
        assert (
            pkg.candidates[0].status is CandidateStatus.SIMULATION_INCOMPLETE
            and pkg.candidates[0].sim is None
        )
        assert pkg.recommendation.status is RecommendationStatus.NO_OPERATIONALLY_FEASIBLE_CANDIDATE

    def test_cancellation_stops_and_is_not_stored(self) -> None:
        tok = threading.Event()
        tok.set()
        store = InMemoryExperimentStore()
        eng, solver = engine(store)
        pkg = eng.run(problem(), twin(), config(), cancel=tok)
        assert pkg.stop_reason is StopReason.CANCELLED and solver.calls == 0
        assert pkg.recommendation.status is not RecommendationStatus.RECOMMENDED_FOR_HUMAN_REVIEW
        assert store.get(pkg.experiment_id) is None

    def test_missing_operational_parameters(self) -> None:
        no_cal = twin(
            machines=(Machine(machine_id="CUT", calendar_id="cut"), Machine(machine_id="WELD"))
        )
        pkg = engine()[0].run(problem(), no_cal, config(refine_on_bottleneck=False))
        assert pkg.candidates[0].status is CandidateStatus.SIMULATION_INVALID
        assert any("MISSING_CALENDAR" in r for r in pkg.candidates[0].reasons)
        conv = PlanConversion(
            policy=QuantityPolicy.EXACT_INTEGER_ONLY,
            batch_sizes={"A": 1},
            release_at=q(0),
            due_at=q(DUE),
        )
        pkg2 = engine()[0].run(
            problem(), twin(), config(conversion=conv, refine_on_bottleneck=False)
        )
        assert pkg2.candidates[0].status is CandidateStatus.PLAN_NOT_IMPORTABLE
        assert "MISSING_BATCH_SIZE" in pkg2.candidates[0].reasons[0]

    def test_fractional_lp_quantities_need_an_explicit_policy(self) -> None:
        frac = problem(weld_h="8.01")  # B = 160.2 pieces
        assert lp_optimum(frac)[1]["B"] == Fraction(801, 5)
        pkg = engine()[0].run(frac, twin(), config(refine_on_bottleneck=False))
        assert pkg.candidates[0].status is CandidateStatus.PLAN_NOT_IMPORTABLE
        assert "FRACTIONAL_QUANTITY" in pkg.candidates[0].reasons[0]
        floor = PlanConversion(
            policy=QuantityPolicy.FLOOR_TO_BATCH,
            batch_sizes={"A": 1, "B": 1},
            release_at=q(0),
            due_at=q(DUE),
        )
        pkg2 = engine()[0].run(frac, twin(), config(conversion=floor, refine_on_bottleneck=False))
        c = pkg2.candidates[0]
        assert c.sim is not None and c.sim.plan_conversion == (("B", "160.2", 160, "-0.2"),)

    def test_partial_failure_keeps_the_other_candidates(self) -> None:
        broken = CandidateSpec(
            label="broken buffer",
            origin=CandidateOrigin.PORTFOLIO,
            twin_changes=(SetBufferCapacity(buffer_id="B1", capacity=0),),
        )
        derated = CandidateSpec(
            label="7.5h",
            origin=CandidateOrigin.PORTFOLIO,
            planning=ScenarioSpec(
                scenario_id="d", changes=(CapacityChange(resource_id="WELD_H", capacity=D("7.5")),)
            ),
        )
        pkg = engine()[0].run(
            problem(), twin(), config(portfolio=(broken, derated), refine_on_bottleneck=False)
        )
        by = {x.spec.label: x for x in pkg.candidates}
        assert by["broken buffer"].status is CandidateStatus.SIMULATION_INVALID
        assert by["7.5h"].status is CandidateStatus.OPERATIONALLY_FEASIBLE
        assert pkg.recommendation.candidate_id == by["7.5h"].candidate_id

    def test_contradictions_are_preserved_and_block_the_recommendation(self) -> None:
        slow = twin(weld_b_s="200")  # planning says 0.05 h = 180 s per B piece on the welder
        found = contradictions(problem(weld_h="6"), slow, config().bindings)
        assert [(x.code, x.resource_id, x.product_id) for x in found] == [
            ("RATE_MISMATCH", "WELD_H", "B")
        ]
        assert found[0].planning_value == "180 s/piece" and found[0].twin_value == "200 s/piece"
        derated = CandidateSpec(
            label="6h",
            origin=CandidateOrigin.PORTFOLIO,
            planning=ScenarioSpec(
                scenario_id="d6", changes=(CapacityChange(resource_id="WELD_H", capacity=D(6)),)
            ),
        )
        pkg = engine()[0].run(
            problem(), slow, config(portfolio=(derated,), refine_on_bottleneck=False)
        )
        assert pkg.recommendation.status is RecommendationStatus.BLOCKED_BY_CONTRADICTIONS
        assert any("RATE_MISMATCH" in r for r in pkg.recommendation.rationale)


# ============================================================================ calendar, capacity, bottlenecks
class TestOperationalAnalysis:
    def test_downtime_overlapping_a_break_is_not_double_counted(self) -> None:
        bd = (
            Breakdown(machine_id="WELD", at=q(11, "h"), duration=q(2, "h")),
        )  # 11:00-13:00 covers 12:00-12:30 break
        t = twin(breakdowns=bd)
        assert productive_seconds(t, ("WELD",), DUE) == 27_000 - (7_200 - 1_800)  # 6 h
        cand = CandidateSpec(
            label="weld breakdown",
            origin=CandidateOrigin.PORTFOLIO,
            twin_changes=(AddBreakdown(breakdown=bd[0]),),
        )
        pkg = engine()[0].run(problem(), twin(), config(portfolio=(cand,)))
        refined = [
            x
            for x in pkg.candidates
            if x.spec.origin is CandidateOrigin.REFINEMENT and x.spec.twin_changes
        ]
        assert refined and refined[0].spec.planning.changes[-1] == CapacityChange(
            resource_id="WELD_H", capacity=D(6)
        )
        assert lp_optimum(problem(weld_h="6"))[1] == {"A": 0, "B": 120}
        assert dict((p, D(v)) for p, v, _ in refined[0].math.quantities)["B"] == 120
        # 120 pieces: 80 before 11:00?? no: 60 by 11:00 (3 h), then 13:00 + 60 x 180 s = 16:00
        assert refined[0].sim.metrics["sim_makespan_s"] == 13 * 3600 + 60 * 180 == DUE
        assert refined[0].status is CandidateStatus.OPERATIONALLY_FEASIBLE

    def test_capacity_addition_moves_the_bottleneck(self) -> None:
        # twin-only what-if: a second welder AND the cutter down 07:00-10:00; the LP plan (B=160) is unchanged
        spec = CandidateSpec(
            label="2 welders, cutter late", origin=CandidateOrigin.PORTFOLIO,
            twin_changes=(AddMachine(machine=Machine(machine_id="WELD2", calendar_id="weld"), eligible_for=("WELD_A", "WELD_B")),
                          AddPlannedDowntime(machine_id="CUT", interval=Interval(start=q(7, "h"), end=q(10, "h")))),
        )  # fmt: skip
        pkg = engine()[0].run(
            problem(), twin(), config(portfolio=(spec,), refine_on_bottleneck=False)
        )
        base = pkg.candidates[0]
        c = next(x for x in pkg.candidates if x.spec.label == "2 welders, cutter late")
        assert D(c.math.objective) == 960 and base.sim.bottlenecks[0] == "WELD"
        assert c.sim.bottlenecks[0] == "CUT"  # the bottleneck migrated
        # hand arithmetic: cutter 10:00-12:00 (80 pieces), 12:30 + 80 x 90 s = 14:30; last weld +180 s
        assert c.sim.metrics["sim_makespan_s"] == 14 * 3600 + 30 * 60 + 180
        assert c.sim.metrics["sim_util:CUT"] > c.sim.metrics["sim_util:WELD"]
        assert c.status is CandidateStatus.OPERATIONALLY_FEASIBLE
        assert pkg.recommendation.candidate_id == c.candidate_id  # full verified objective, on time

    def test_lp_capacity_increase_keeps_welders_saturated(self) -> None:
        both = CandidateSpec(
            label="welder + LP hours", origin=CandidateOrigin.PORTFOLIO,
            planning=ScenarioSpec(scenario_id="w2", changes=(CapacityChange(resource_id="WELD_H", capacity=D(15)),)),
            twin_changes=(AddMachine(machine=Machine(machine_id="WELD2", calendar_id="weld"), eligible_for=("WELD_A", "WELD_B")),),
        )  # fmt: skip
        pkg = engine()[0].run(
            problem(), twin(), config(portfolio=(both,), refine_on_bottleneck=False)
        )
        c = next(x for x in pkg.candidates if x.spec.label == "welder + LP hours")
        val, x = lp_optimum(problem(weld_h="15"))
        assert (val, x) == (1700, {"A": 50, "B": 200}) and D(c.math.objective) == 1700
        # 50 x 360 + 200 x 180 = 54 000 s over two welders = 7.5 h each: still the constraint
        assert c.sim.bottlenecks[0] in ("WELD", "WELD2")

    def test_sensitivity_portfolio_and_candidate_deduplication(self) -> None:
        sens = SensitivitySpec(
            resource_id="WELD_H", capacities=(D(8), D("7.5"), D(7))
        )  # 8 duplicates the baseline
        pkg = engine()[0].run(
            problem(), twin(), config(sensitivity=(sens,), refine_on_bottleneck=False)
        )
        by = {x.spec.label: x for x in pkg.candidates}
        assert (
            by["sens:WELD_H=8"].status is CandidateStatus.DUPLICATE
            and by["sens:WELD_H=8"].duplicate_of == pkg.baseline_candidate_id
        )
        assert by["sens:WELD_H=7.5"].status is CandidateStatus.OPERATIONALLY_FEASIBLE
        assert by["sens:WELD_H=7"].status is CandidateStatus.OPERATIONALLY_FEASIBLE
        # objective-first among feasible candidates picks 7.5 h (B=150, 900 EGP) over 7 h (B=140, 840 EGP)
        assert pkg.recommendation.candidate_id == by["sens:WELD_H=7.5"].candidate_id
        assert D(by["sens:WELD_H=7"].math.objective) == lp_optimum(problem(weld_h="7"))[0] == 840


# ============================================================================ decision rules
class TestDecisionRules:
    def test_no_invented_weights_and_hard_constraints(self) -> None:
        weighted = DecisionSpec(ranking_mode=RankingMode.WEIGHTED_PREFERENCE)
        pkg = engine()[0].run(problem(), twin(), config(decision=weighted))
        assert pkg.recommendation.status is RecommendationStatus.NO_OPERATIONALLY_FEASIBLE_CANDIDATE
        assert pkg.ranking is not None and pkg.ranking.status.value == "MISSING_CONFIGURATION"
        hard = DecisionSpec(
            ranking_mode=RankingMode.CONSTRAINT_FIRST,
            hard_constraints=(
                HardConstraint(metric="sim_makespan_s", op="<=", threshold=D(50_000)),
            ),
        )
        pkg2 = engine()[0].run(problem(), twin(), config(decision=hard))
        assert pkg2.recommendation.candidate_id is None  # 57 600 s makespan breaks the stated limit
        none = engine()[0].run(
            problem(),
            twin(),
            config(decision=DecisionSpec(ranking_mode=RankingMode.OBJECTIVE_FIRST)),
        )
        assert none.pareto is not None and none.pareto.status.value == "NOT_CONFIGURED"

    def test_budget_exhaustion(self) -> None:
        port = tuple(CandidateSpec(label=f"cap {c}", origin=CandidateOrigin.PORTFOLIO,
                                   planning=ScenarioSpec(scenario_id=f"c{c}", changes=(CapacityChange(resource_id="WELD_H", capacity=D(c)),)))
                     for c in (6, 7, 5))  # fmt: skip
        pkg = engine()[0].run(
            problem(), twin(), config(portfolio=port, budget=Budget(max_candidates=2))
        )
        assert pkg.stop_reason is StopReason.MAX_CANDIDATES
        assert sum(1 for c in pkg.candidates if c.status is CandidateStatus.SKIPPED_BUDGET) == 2
        one = engine()[0].run(problem(), twin(), config(budget=Budget(max_iterations=1)))
        assert one.stop_reason is StopReason.MAX_ITERATIONS and one.iterations[0].new_specs
        assert one.recommendation.status is RecommendationStatus.NO_OPERATIONALLY_FEASIBLE_CANDIDATE


# ============================================================================ stochastic
def stochastic_twin() -> FactoryModel:
    fail = FailureModel(
        time_to_failure=Dist(kind=DistKind.EXPONENTIAL, mean="2", unit="h"),
        repair=Dist(kind=DistKind.TRIANGULAR, low="5", mode="10", high="30", unit="min"),
    )
    return twin(
        machines=(
            Machine(machine_id="CUT", calendar_id="cut"),
            Machine(machine_id="WELD", calendar_id="weld", failures=fail),
        )
    )


class TestStochastic:
    def test_seeded_runs_are_reproducible_and_carry_uncertainty(self) -> None:
        cfg = config(
            simulation=SimulationConfig(seed=4, replications=12),
            refine_on_bottleneck=False,
            requirements=OperationalRequirements(
                require_plan_achieved=False, require_complete_run=False
            ),
        )
        a = engine()[0].run(problem(weld_h="6"), stochastic_twin(), cfg)
        b = engine()[0].run(problem(weld_h="6"), stochastic_twin(), cfg)
        c = engine()[0].run(
            problem(weld_h="6"),
            stochastic_twin(),
            cfg.model_copy(update={"simulation": SimulationConfig(seed=5, replications=12)}),
        )
        assert a.package_sha256 == b.package_sha256
        sa, sc = a.candidates[0].sim, c.candidates[0].sim
        assert sa.kind == "STOCHASTIC_ESTIMATE" and sa.replications == 12
        assert sa.content_sha256 != sc.content_sha256
        mean, lo, hi = sa.uncertainty["sim_makespan_s"]
        assert lo and hi and D(lo) <= D(mean) <= D(hi)
        assert "sim_late_probability" in sa.metrics
        assert (
            any("95% CI" in u for u in a.recommendation.uncertainty)
            or a.recommendation.candidate_id is None
        )

    def test_late_probability_requirement_needs_a_stochastic_model(self) -> None:
        cfg = config(
            requirements=OperationalRequirements(max_late_probability=D("0.1")),
            refine_on_bottleneck=False,
        )
        pkg = engine()[0].run(problem(weld_h="7.5"), twin(), cfg)
        assert "LATE_PROBABILITY_NOT_ESTIMABLE" in {v.code for v in pkg.candidates[0].violations}


# ============================================================================ persistence, idempotency, tampering
class TestPersistence:
    def test_idempotent_rerun_and_restart(self, tmp_path: Path) -> None:
        db = tmp_path / "osco.db"
        eng, solver = engine(SqliteExperimentStore(db))
        first = eng.run(problem(), twin(), config())
        assert solver.calls == 2
        eng2, solver2 = engine(SqliteExperimentStore(db))  # restart: new objects, same file
        again = eng2.run(problem(), twin(), config())
        assert again == first and solver2.calls == 0 and verify_package(again)
        different = eng2.run(problem(), twin(), config(experiment_label="another"))
        assert different.experiment_id != first.experiment_id and solver2.calls > 0

    def test_tampered_packages_are_detected(self, tmp_path: Path) -> None:
        pkg = engine()[0].run(problem(), twin(), config())
        forged = pkg.model_copy(
            update={"recommendation": pkg.recommendation.model_copy(update={"approved": True})}
        )
        assert not verify_package(forged)
        mem = InMemoryExperimentStore()
        with pytest.raises(OscoStoreError):
            mem.save(forged)
        mem.save(pkg)
        mem.tamper(
            pkg.experiment_id, pkg.model_dump_json().replace('"approved":false', '"approved":true')
        )
        with pytest.raises(OscoStoreError) as e:
            mem.get(pkg.experiment_id)
        assert e.value.code == "PACKAGE_TAMPERED"
        db = tmp_path / "t.db"
        SqliteExperimentStore(db).save(pkg)
        con = sqlite3.connect(db)
        con.execute("UPDATE osco_experiments SET body = replace(body, '\"900', '\"999')")
        con.commit()
        con.close()
        with pytest.raises(OscoStoreError):
            SqliteExperimentStore(db).get(pkg.experiment_id)

    def test_bounded_parallel_simulation_is_deterministic(self) -> None:
        port = tuple(CandidateSpec(label=f"cap {c}", origin=CandidateOrigin.PORTFOLIO,
                                   planning=ScenarioSpec(scenario_id=f"c{c}", changes=(CapacityChange(resource_id="WELD_H", capacity=D(c)),)))
                     for c in (5, 6, 7))  # fmt: skip
        seq = engine()[0].run(problem(), twin(), config(portfolio=port, refine_on_bottleneck=False))
        par = engine()[0].run(
            problem(),
            twin(),
            config(portfolio=port, refine_on_bottleneck=False, budget=Budget(max_workers=3)),
        )
        strip = lambda p: [
            (c.candidate_id, c.status, c.sim.content_sha256 if c.sim else None)
            for c in p.candidates
        ]
        assert strip(seq) == strip(par)
        assert seq.recommendation.candidate_id == par.recommendation.candidate_id


def test_simulation_cannot_override_infeasibility_even_if_twin_could_run() -> None:
    # demand forces 170 B pieces; the LP says infeasible with 8 h of welding: never simulated or recommended
    spec = CandidateSpec(
        label="forced 170",
        origin=CandidateOrigin.PORTFOLIO,
        planning=ScenarioSpec(
            scenario_id="f170", changes=(DemandBoundsChange(product_id="B", min_quantity=D(170)),)
        ),
    )
    pkg = engine()[0].run(
        problem(), twin(horizon=q(2, "day")), config(portfolio=(spec,), refine_on_bottleneck=False)
    )
    c = next(x for x in pkg.candidates if x.spec.label == "forced 170")
    assert c.status is CandidateStatus.MATH_INFEASIBLE and c.sim is None
    assert pkg.recommendation.candidate_id != c.candidate_id
    _ = SetProcessingTime  # imported contract kept for scenario typing

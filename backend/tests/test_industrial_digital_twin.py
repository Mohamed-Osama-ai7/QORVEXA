# ruff: noqa: E501, E731
"""Digital Twin & Industrial Simulation Engine.

All factories here are SYNTHETIC. Expected outcomes of the acceptance tests are computed by an
independent recurrence (tandem line with blocking after service), not by the simulator itself.
"""

from __future__ import annotations

import sqlite3
import threading
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from qorvexa.industrial.digital_twin import (
    AddMachine,
    AddPlannedDowntime,
    Breakdown,
    Buffer,
    Calendar,
    DispatchPolicy,
    Dist,
    DistKind,
    FactoryModel,
    FailureModel,
    InMemorySimulationStore,
    InterruptPolicy,
    Interval,
    Machine,
    Material,
    MaterialUse,
    Operation,
    OperationalInputs,
    Order,
    PlanImportError,
    Policies,
    Product,
    Qty,
    QuantityPolicy,
    ReportStatus,
    ResourcePool,
    Routing,
    RunKind,
    RunStatus,
    Scenario,
    ScenarioError,
    SetBufferCapacity,
    SetDispatchPolicy,
    SetOrderQuantity,
    SetProcessingTime,
    SetupPolicy,
    Shift,
    SqliteSimulationStore,
    TwinStoreError,
    apply_scenario,
    build_from_ontology,
    compare,
    compile_model,
    fixed,
    import_plan,
    model_sha256,
    scenario_sha256,
    simulate,
    simulate_scenario,
    to_evidence,
    verify_report,
)
from qorvexa.industrial.formulation import ActorKind
from qorvexa.industrial.governance.authz import Principal, Role
from qorvexa.industrial.ontology import (
    Basis,
    EvidenceKind,
    EvidenceRef,
    FactoryOntology,
    InMemoryOntologyStore,
    QuantityValue,
    Scope,
)
from qorvexa.industrial.production_planning.model import ObjectiveMode
from qorvexa.industrial.production_planning.report import (
    ProductionReport,
    ProductQuantity,
)
from qorvexa.industrial.production_planning.report import ReportStatus as PlanStatus

CLOCK = lambda: "2026-01-01T00:00:00+00:00"
CONT = Calendar(calendar_id="cont", continuous=True)


def q(v: object, u: str = "s") -> Qty:
    return Qty(value=str(v), unit=u)


# ============================================================================ independent reference
def ref_tandem(p1: int, p2: int, n: int, c: int) -> dict[str, object]:
    """Two single-server stations, buffer of capacity ``c`` between them, blocking after service,
    all jobs available at t=0. Returns completion times and blocked/starved time."""
    d1 = [0] * (n + 1)
    s2 = [0] * (n + 1)
    f2 = [0] * (n + 1)
    f1 = [0] * (n + 1)
    for i in range(1, n + 1):
        start1 = d1[i - 1]
        f1[i] = start1 + p1
        d1[i] = max(f1[i], s2[i - c]) if i - c >= 1 else f1[i]
        s2[i] = max(d1[i], f2[i - 1])
        f2[i] = s2[i] + p2
    window = f2[n]
    return {
        "finish": f2[1:],
        "window": window,
        "m1_blocked": sum(d1[i] - f1[i] for i in range(1, n + 1)),
        "m2_starved": sum(s2[i] - f2[i - 1] for i in range(1, n + 1)),
    }


def tandem(
    p1: int = 60, p2: int = 100, n: int = 10, cap: int | str = 1, **kw: object
) -> FactoryModel:
    data: dict[str, object] = dict(
        model_id="tandem", factory_id="F1", horizon=q(1, "day"),
        machines=(Machine(machine_id="M1", calendar_id="cont"), Machine(machine_id="M2", calendar_id="cont")),
        buffers=(Buffer(buffer_id="B0", capacity="UNBOUNDED"), Buffer(buffer_id="B1", capacity=cap)),
        calendars=(CONT,),
        routings=(
            Routing(routing_id="R", operations=(
                Operation(op_id="CUT", input_buffer="B0", machines=("M1",), processing=fixed(str(p1), "s")),
                Operation(op_id="WELD", input_buffer="B1", machines=("M2",), processing=fixed(str(p2), "s")),
            )),
        ),
        products=(Product(product_id="A", routing_id="R"),),
        orders=(Order(order_id="O1", product_id="A", quantity=n, release_at=q(0), due_at=q(1200)),),
    )  # fmt: skip
    data.update(kw)
    return FactoryModel(**data)  # type: ignore[arg-type]


def synthetic_line(**kw: object) -> FactoryModel:
    """One line, two products, shared machines and operators, finite buffers, two shifts with breaks,
    steel consumption with a delivery, setups and one planned interruption."""
    two_shift = Calendar(
        calendar_id="two_shift",
        shifts=(Shift(name="early", start="06:00", end="14:00", breaks=(("10:00", "10:30"),)),
                Shift(name="late", start="14:00", end="22:00", breaks=(("18:00", "18:30"),))),
    )  # fmt: skip
    m = lambda mid, **x: Machine(machine_id=mid, calendar_id="two_shift", line_id="L1", **x)
    ops_a = (
        Operation(op_id="CUT_A", input_buffer="B_IN", machines=("CUT1", "CUT2"), processing=fixed("45", "s"),
                  materials=(MaterialUse(material_id="STEEL", per_piece=q("2", "kg")),)),
        Operation(op_id="PRESS_A", input_buffer="B_CUT", machines=("PRESS",), processing=fixed("40", "s")),
        Operation(op_id="WELD_A", input_buffer="B_WELD", machines=("WELD",), processing=fixed("1", "min"),
                  setup=fixed("5", "min"), setup_policy=SetupPolicy.ON_PRODUCT_CHANGE, pools=(("OPS", 1),)),
        Operation(op_id="PACK_A", input_buffer="B_PACK", machines=("PACK",), processing=fixed("20", "s"), pools=(("OPS", 1),)),
    )  # fmt: skip
    ops_b = (
        Operation(op_id="CUT_B", input_buffer="B_IN", machines=("CUT1", "CUT2"), processing=fixed("30", "s"),
                  materials=(MaterialUse(material_id="STEEL", per_piece=q("1.5", "kg")),)),
        Operation(op_id="WELD_B", input_buffer="B_WELD", machines=("WELD",), processing=fixed("50", "s"),
                  setup=fixed("5", "min"), setup_policy=SetupPolicy.ON_PRODUCT_CHANGE, pools=(("OPS", 1),)),
        Operation(op_id="PACK_B", input_buffer="B_PACK", machines=("PACK",), processing=fixed("20", "s"), pools=(("OPS", 1),)),
    )  # fmt: skip
    data: dict[str, object] = dict(
        model_id="synthetic-line-1", factory_id="F1", horizon=q(2, "day"),
        machines=(m("CUT1"), m("CUT2"),
                  m("PRESS", planned_downtime=(Interval(start=q(8, "h"), end=q(9, "h")),)),
                  m("WELD"), m("PACK")),
        buffers=(Buffer(buffer_id="B_IN", capacity="UNBOUNDED"), Buffer(buffer_id="B_CUT", capacity=20),
                 Buffer(buffer_id="B_WELD", capacity=10), Buffer(buffer_id="B_PACK", capacity=10)),
        calendars=(two_shift,), pools=(ResourcePool(pool_id="OPS", size=1),),
        materials=(Material(material_id="STEEL", unit="kg", initial_stock=q("400", "kg"),
                            deliveries=((q(7, "h"), q("400", "kg")),)),),
        routings=(Routing(routing_id="RA", operations=ops_a), Routing(routing_id="RB", operations=ops_b)),
        products=(Product(product_id="A", routing_id="RA", batch_size=5), Product(product_id="B", routing_id="RB", batch_size=5)),
        orders=(Order(order_id="OA", product_id="A", quantity=200, release_at=q(0), due_at=q(16, "h")),
                Order(order_id="OB", product_id="B", quantity=150, release_at=q(0), due_at=q(12, "h"), priority=1)),
    )  # fmt: skip
    data.update(kw)
    return FactoryModel(**data)  # type: ignore[arg-type]


def single(proc: str = "600", unit: str = "s", cal: Calendar = CONT, **kw: object) -> FactoryModel:
    data: dict[str, object] = dict(
        model_id="single", factory_id="F1", horizon=q(3, "day"),
        machines=(Machine(machine_id="M", calendar_id=cal.calendar_id),),
        buffers=(Buffer(buffer_id="B", capacity="UNBOUNDED"),), calendars=(cal,),
        routings=(Routing(routing_id="R", operations=(Operation(op_id="OP", input_buffer="B", machines=("M",), processing=fixed(proc, unit)),)),),
        products=(Product(product_id="P", routing_id="R"),),
        orders=(Order(order_id="O", product_id="P", quantity=1, release_at=q(0)),),
    )  # fmt: skip
    data.update(kw)
    return FactoryModel(**data)  # type: ignore[arg-type]


def run(model: FactoryModel, **kw: object):  # type: ignore[no-untyped-def]
    return simulate(model, clock=CLOCK, **kw)  # type: ignore[arg-type]


def finish_times(rep) -> list[int]:  # type: ignore[no-untyped-def]
    return [t for t, kind, *_ in rep.replications[0].trace_sample if kind == "COMPLETE"]


# ============================================================================ acceptance: tandem line
class TestTandemAgainstIndependentReference:
    @pytest.mark.parametrize(
        ("p1", "p2", "n", "c"),
        [(60, 100, 10, 1), (100, 60, 8, 1), (60, 100, 12, 3), (90, 90, 6, 1), (30, 200, 5, 2)],
    )
    def test_completion_blocking_and_starvation_match_recurrence(
        self, p1: int, p2: int, n: int, c: int
    ) -> None:
        exp = ref_tandem(p1, p2, n, c)
        rep = run(tandem(p1, p2, n, c))
        assert rep.status is ReportStatus.COMPLETED and rep.kind is RunKind.DETERMINISTIC_SIMULATION
        assert finish_times(rep) == exp["finish"]
        m = rep.metrics
        assert m.window_s == exp["window"]
        assert m.machine("M1").blocked_s == exp["m1_blocked"]
        assert m.machine("M2").starved_s == exp["m2_starved"]
        assert m.machine("M1").processing_s == n * p1 and m.machine("M2").processing_s == n * p2
        assert (m.machine("M2").utilization.num, m.machine("M2").utilization.den) == _frac(
            n * p2, exp["window"]
        )
        assert (m.throughput_per_hour.num, m.throughput_per_hour.den) == _frac(
            n * 3600, exp["window"]
        )
        assert m.conservation.balanced and m.completed == n

    def test_bottleneck_is_the_slow_station(self) -> None:
        m = run(tandem(60, 100)).metrics
        assert m.bottlenecks[0].machine_id == "M2"
        assert "highest utilization in the run" in m.bottlenecks[0].reasons
        assert run(tandem(100, 60)).metrics.bottlenecks[0].machine_id == "M1"

    def test_lead_time_wip_and_lateness_from_events(self) -> None:
        exp = ref_tandem(60, 100, 10, 1)
        m = run(tandem(60, 100, 10, 1)).metrics
        fins = exp["finish"]
        assert (m.avg_lead_time_s.num, m.avg_lead_time_s.den) == _frac(
            sum(fins), 10
        )  # release at 0
        # WIP integral = sum of time each piece spends in the line (all admitted at 0)
        assert (m.wip_avg.num, m.wip_avg.den) == _frac(sum(fins), exp["window"])
        assert (
            exp["window"] == 1060 and m.late_orders == 0 and m.orders[0].late is False
        )  # due 1200
        early_due = (
            Order(order_id="O1", product_id="A", quantity=10, release_at=q(0), due_at=q(1000)),
        )
        late = run(tandem(60, 100, 10, 1, orders=early_due)).metrics
        assert (
            late.late_orders == 1
            and late.orders[0].late is True
            and late.orders[0].finished_at == 1060
        )


def _frac(a: int, b: int) -> tuple[int, int]:
    from fractions import Fraction

    f = Fraction(a, b)
    return f.numerator, f.denominator


# ============================================================================ scenarios
class TestScenarios:
    def test_faster_bottleneck_scenario_matches_reference_and_is_independent(self) -> None:
        base = tandem(60, 100, 10, 1)
        before = model_sha256(base)
        s = Scenario(
            scenario_id="weld-75",
            baseline_model_sha256=before,
            changes=(SetProcessingTime(op_id="WELD", processing=fixed("75", "s")),),
        )
        rb, rs = run(base), simulate_scenario(base, s, clock=CLOCK)
        assert model_sha256(base) == before  # baseline untouched
        assert finish_times(rs) == ref_tandem(60, 75, 10, 1)["finish"]
        assert rs.scenario_sha256 == scenario_sha256(s) and rs.baseline_model_sha256 == before
        assert rs.model_sha256 != rb.model_sha256 and rs.simulation_id != rb.simulation_id
        cmp_ = compare(rb, rs)
        d = {x.metric: x for x in cmp_.deltas}
        assert (
            Decimal(d["window_s"].change)
            == ref_tandem(60, 75, 10, 1)["window"] - ref_tandem(60, 100, 10, 1)["window"]
        )
        assert Decimal(d["throughput_per_hour"].change) > 0

    def test_planned_downtime_on_a_saturated_bottleneck_delays_by_exactly_its_length(self) -> None:
        base = tandem(60, 100, 10, 1)
        s = Scenario(
            scenario_id="weld-maint",
            baseline_model_sha256=model_sha256(base),
            changes=(
                AddPlannedDowntime(machine_id="M2", interval=Interval(start=q(300), end=q(600))),
            ),
        )
        rb, rs = run(base), simulate_scenario(base, s, clock=CLOCK)
        assert rs.metrics.window_s == rb.metrics.window_s + 300
        assert rs.metrics.machine("M2").planned_downtime_s == 300

    def test_adding_a_parallel_machine_raises_throughput(self) -> None:
        base = tandem(60, 100, 10, 2)
        s = Scenario(
            scenario_id="2nd-welder",
            baseline_model_sha256=model_sha256(base),
            changes=(
                AddMachine(
                    machine=Machine(machine_id="M2b", calendar_id="cont"), eligible_for=("WELD",)
                ),
            ),
        )
        rb, rs = run(base), simulate_scenario(base, s, clock=CLOCK)
        assert rs.metrics.window_s < rb.metrics.window_s
        # with two welders at 100 s and a cutter at 60 s, the cutter becomes the pace setter:
        # pieces leave the cutter every 60 s, the last at 600 s, and need 100 s more
        assert rs.metrics.window_s == 700
        assert rs.metrics.machine("M2b").jobs > 0

    def test_scenario_lineage_and_validation(self) -> None:
        base = tandem()
        with pytest.raises(ScenarioError) as e:
            apply_scenario(
                base,
                Scenario(
                    scenario_id="x",
                    baseline_model_sha256="0" * 64,
                    changes=(SetDispatchPolicy(dispatch=DispatchPolicy.EDD),),
                ),
            )
        assert e.value.code == "BASELINE_MISMATCH"
        for change, code in (
            (SetProcessingTime(op_id="NOPE", processing=fixed("1", "s")), "UNKNOWN_OPERATION"),
            (SetBufferCapacity(buffer_id="NOPE", capacity=3), "UNKNOWN_BUFFER"),
            (SetOrderQuantity(order_id="NOPE", quantity=3), "UNKNOWN_ORDER"),
            (
                AddMachine(
                    machine=Machine(machine_id="M1", calendar_id="cont"), eligible_for=("CUT",)
                ),
                "DUPLICATE_MACHINE",
            ),
        ):
            with pytest.raises(ScenarioError) as e2:
                apply_scenario(
                    base,
                    Scenario(
                        scenario_id="x", baseline_model_sha256=model_sha256(base), changes=(change,)
                    ),
                )
            assert e2.value.code == code
        with pytest.raises(ValidationError):
            Scenario(scenario_id="empty", baseline_model_sha256=model_sha256(base), changes=())
        # a scenario that makes the model invalid is validated on its own and not simulated
        bad = Scenario(
            scenario_id="bad",
            baseline_model_sha256=model_sha256(base),
            changes=(SetProcessingTime(op_id="WELD", processing=fixed("1", "kg")),),
        )
        r = simulate_scenario(base, bad, clock=CLOCK)
        assert r.status is ReportStatus.INVALID_INPUT and not r.replications

    def test_comparison_refuses_unrelated_or_mismatched_runs(self) -> None:
        a, b = run(tandem()), run(tandem(60, 90))
        with pytest.raises(ScenarioError) as e:
            compare(a, b)  # b is not a scenario of a
        assert e.value.code == "LINEAGE_MISMATCH"
        base = tandem()
        s = Scenario(
            scenario_id="s",
            baseline_model_sha256=model_sha256(base),
            changes=(SetOrderQuantity(order_id="O1", quantity=5),),
        )
        with pytest.raises(ScenarioError) as e2:
            compare(run(base, seed=1), simulate_scenario(base, s, clock=CLOCK, seed=2))
        assert e2.value.code == "PARAMETER_MISMATCH"


# ============================================================================ mechanics
class TestMechanics:
    def test_deterministic_replay_and_event_ordering(self) -> None:
        r1, r2 = run(synthetic_line()), run(synthetic_line())
        assert r1.replications[0].event_digest == r2.replications[0].event_digest
        assert r1.content_sha256 == r2.content_sha256
        times = [t for t, *_ in r1.replications[0].trace_sample]
        assert times == sorted(times)
        assert r1.replications[0].events > 100 and r1.metrics.completed == 350

    def test_conservation_of_pieces_and_material(self) -> None:
        for model in (synthetic_line(), tandem(), synthetic_line(horizon=q(10, "h"))):
            m = run(model).metrics
            assert m.conservation.balanced, m.conservation
            assert all(x.balanced for x in m.materials)
        m = run(synthetic_line()).metrics
        steel = m.materials[0]
        assert Decimal(steel.consumed) == 2 * 200 + Decimal("1.5") * 150

    def test_time_accounting_covers_the_window_exactly(self) -> None:
        m = run(synthetic_line()).metrics
        for x in m.machines:
            total = (
                x.processing_s
                + x.setup_s
                + x.blocked_s
                + x.starved_s
                + x.waiting_resource_s
                + x.down_s
                + x.planned_downtime_s
                + x.off_shift_s
            )
            assert total == m.window_s, x.machine_id

    def test_buffers_never_exceed_capacity(self) -> None:
        rep = run(
            synthetic_line(
                buffers=(
                    Buffer(buffer_id="B_IN", capacity="UNBOUNDED"),
                    Buffer(buffer_id="B_CUT", capacity=5),
                    Buffer(buffer_id="B_WELD", capacity=5),
                    Buffer(buffer_id="B_PACK", capacity=5),
                )
            )
        )
        for b in rep.metrics.buffers:
            assert b.capacity is None or b.peak <= b.capacity
        assert any(mm.blocked_s > 0 for mm in rep.metrics.machines)  # small buffers cause blocking

    def test_shared_operator_pool_serialises_work(self) -> None:
        model = FactoryModel(
            model_id="pool", factory_id="F1", horizon=q(1, "day"),
            machines=(Machine(machine_id="M1", calendar_id="cont"), Machine(machine_id="M2", calendar_id="cont")),
            buffers=(Buffer(buffer_id="B", capacity="UNBOUNDED"),), calendars=(CONT,), pools=(ResourcePool(pool_id="OP", size=1),),
            routings=(Routing(routing_id="R", operations=(Operation(op_id="X", input_buffer="B", machines=("M1", "M2"), processing=fixed("100", "s"), pools=(("OP", 1),)),)),),
            products=(Product(product_id="P", routing_id="R"),),
            orders=(Order(order_id="O", product_id="P", quantity=2, release_at=q(0)),),
        )  # fmt: skip
        m = run(model).metrics
        assert m.window_s == 200  # two machines, one operator
        assert m.pools[0].utilization.value == "1.000000"
        no_pool = model.model_copy(update={"pools": (ResourcePool(pool_id="OP", size=2),)})
        assert run(no_pool).metrics.window_s == 100

    def test_routing_precedence_is_respected(self) -> None:
        rep = run(synthetic_line())
        seen: dict[str, list[str]] = {}
        for _, kind, _, b in rep.replications[0].trace_sample:
            if kind == "OP_END":
                job, op = b.split("@")
                seen.setdefault(job, []).append(op)
        for job, ops in seen.items():
            route = (
                ["CUT_A", "PRESS_A", "WELD_A", "PACK_A"]
                if job.startswith("OA")
                else ["CUT_B", "WELD_B", "PACK_B"]
            )
            assert ops == route[: len(ops)], job

    def test_no_work_starts_off_shift_and_breaks_are_honoured(self) -> None:
        rep = run(synthetic_line())
        day = 86_400
        work_windows = [(6 * 3600, 10 * 3600), (10.5 * 3600, 18 * 3600), (18.5 * 3600, 22 * 3600)]
        for t, kind, *_ in rep.replications[0].trace_sample:
            if kind in ("START", "RESUME"):
                tod = t % day
                assert any(a <= tod < b for a, b in work_windows), (t, kind)
        m = rep.metrics.machine("PRESS")
        assert m.planned_downtime_s == 3600 and m.off_shift_s > 0

    def test_setup_on_product_change(self) -> None:
        m = run(synthetic_line()).metrics.machine("WELD")
        assert m.setup_s >= 2 * 300 and m.setup_s % 300 == 0

    def test_dispatch_policies_change_order_of_work(self) -> None:
        pri = run(synthetic_line(policies=Policies(dispatch=DispatchPolicy.PRIORITY)))
        fifo = run(synthetic_line(policies=Policies(dispatch=DispatchPolicy.FIFO)))
        first = lambda r: next(
            b for _, k, a, b in r.replications[0].trace_sample if k == "START" and a == "CUT1"
        )
        assert first(pri).startswith("OB") and first(fifo).startswith("OA")

    def test_shift_interruption_resume_versus_restart(self) -> None:
        one_hour = Calendar(calendar_id="h", shifts=(Shift(name="s", start="08:00", end="09:00"),))
        resume = run(single("5400", cal=one_hour))
        assert resume.status is ReportStatus.COMPLETED
        # 3600 s on day 0 (08:00-09:00), 1800 s on day 1 from 08:00 -> 86400 + 28800 + 1800
        assert finish_times(resume) == [117_000]
        restart = run(
            single("5400", cal=one_hour, policies=Policies(interrupt=InterruptPolicy.RESTART))
        )
        assert restart.status is ReportStatus.HORIZON_REACHED and restart.metrics.completed == 0

    def test_breakdown_and_recovery(self) -> None:
        bd = (Breakdown(machine_id="M", at=q(100), duration=q(50)),)
        r = run(single("600", breakdowns=bd))
        assert finish_times(r) == [650]
        mm = r.metrics.machine("M")
        assert mm.down_s == 50 and mm.failures == 1
        kinds = [k for _, k, *_ in r.replications[0].trace_sample]
        assert kinds.index("FAILURE") < kinds.index("INTERRUPT") or "INTERRUPT" in kinds
        assert "RESUME" in kinds and "REPAIRED" in kinds
        r2 = run(single("600", breakdowns=bd, policies=Policies(interrupt=InterruptPolicy.RESTART)))
        assert finish_times(r2) == [750]

    def test_material_shortage_stops_work_until_delivery(self) -> None:
        mat = lambda dels: Material(
            material_id="S", unit="kg", initial_stock=q("3", "kg"), deliveries=dels
        )
        op = Operation(
            op_id="OP",
            input_buffer="B",
            machines=("M",),
            processing=fixed("10", "s"),
            materials=(MaterialUse(material_id="S", per_piece=q("1", "kg")),),
        )
        routing = (Routing(routing_id="R", operations=(op,)),)
        orders = (Order(order_id="O", product_id="P", quantity=5, release_at=q(0)),)
        short = run(single(materials=(mat(()),), routings=routing, orders=orders))
        assert short.status is ReportStatus.HORIZON_REACHED and short.metrics.completed == 3
        assert short.metrics.machine("M").waiting_resource_s > 0
        fed = run(
            single(materials=(mat(((q(1000), q("2", "kg")),)),), routings=routing, orders=orders)
        )
        assert fed.status is ReportStatus.COMPLETED and finish_times(fed)[-1] == 1020

    def test_deadlock_is_detected(self) -> None:
        model = FactoryModel(
            model_id="dl", factory_id="F1", horizon=q(1, "day"),
            machines=(Machine(machine_id="M1", calendar_id="cont"), Machine(machine_id="M2", calendar_id="cont")),
            buffers=(Buffer(buffer_id="BA", capacity=1), Buffer(buffer_id="BB", capacity=1)), calendars=(CONT,),
            routings=(
                Routing(routing_id="RP", operations=(Operation(op_id="P1", input_buffer="BA", machines=("M1",), processing=fixed("10", "s")),
                                                      Operation(op_id="P2", input_buffer="BB", machines=("M2",), processing=fixed("10", "s")))),
                Routing(routing_id="RQ", operations=(Operation(op_id="Q1", input_buffer="BB", machines=("M2",), processing=fixed("10", "s")),
                                                      Operation(op_id="Q2", input_buffer="BA", machines=("M1",), processing=fixed("10", "s")))),
            ),
            products=(Product(product_id="P", routing_id="RP"), Product(product_id="Q", routing_id="RQ")),
            orders=(Order(order_id="OP", product_id="P", quantity=2, release_at=q(0)), Order(order_id="OQ", product_id="Q", quantity=2, release_at=q(0))),
        )  # fmt: skip
        r = run(model)
        assert r.status is ReportStatus.DEADLOCK
        assert r.replications[0].deadlock_machines == ("M1", "M2")
        assert any("deadlock" in x.lower() for x in r.limitations)

    def test_scrap_only_when_modeled_and_conserved(self) -> None:
        op = Operation(
            op_id="OP",
            input_buffer="B",
            machines=("M",),
            processing=fixed("10", "s"),
            scrap_rate="0.25",
        )
        r = run(
            single(
                routings=(Routing(routing_id="R", operations=(op,)),),
                orders=(Order(order_id="O", product_id="P", quantity=8, release_at=q(0)),),
            )
        )
        m = r.metrics
        assert m.scrapped == 2 and m.completed == 6 and m.conservation.balanced
        assert run(tandem()).metrics.scrapped == 0

    def test_event_limit_timeout_and_cancellation(self) -> None:
        assert run(synthetic_line(), max_events=50).status is ReportStatus.EVENT_LIMIT
        tok = threading.Event()
        tok.set()
        assert run(synthetic_line(), cancel=tok).status is ReportStatus.CANCELLED
        with pytest.raises(ValueError, match="INVALID_BUDGET"):
            run(tandem(), max_events=0)
        with pytest.raises(ValueError, match="INVALID_REPLICATIONS"):
            run(tandem(), replications=0)


# ============================================================================ inputs, units
class TestInputsAndUnits:
    def codes(self, model: FactoryModel) -> set[str]:
        cm, rep = compile_model(model)
        assert cm is None and not rep.ok
        return {i.code for i in rep.issues if i.blocking}

    def test_unit_mismatches_and_resolution(self) -> None:
        assert "UNIT_MISMATCH" in self.codes(single("5", "kg"))
        assert "DURATION_NOT_ON_RESOLUTION" in self.codes(single("0.5", "s"))
        assert "UNIT_REJECTED" in self.codes(single("5", "fortnights"))
        assert compile_model(single("1.5", "min"))[0] is not None  # 90 s: exact on the resolution
        bad_mat = lambda unit: single(
            materials=(Material(material_id="S", unit="kg", initial_stock=q("1", "kg")),),
            routings=(Routing(routing_id="R", operations=(Operation(op_id="OP", input_buffer="B", machines=("M",), processing=fixed("1", "s"),
                                                                    materials=(MaterialUse(material_id="S", per_piece=q("1", unit)),)),)),),
        )  # fmt: skip
        assert "UNIT_CONVERSION_REQUIRED" in self.codes(bad_mat("g"))
        assert "UNIT_MISMATCH" in self.codes(bad_mat("L"))

    def test_unit_evidence_records_every_conversion(self) -> None:
        cm, rep = compile_model(synthetic_line())
        assert cm is not None and rep.unit_evidence is not None
        conv = {c.path: c for c in rep.unit_evidence.conversions}
        weld = conv["routings[RA].WELD_A.processing.value"]
        assert (weld.from_unit, weld.to_unit, weld.factor, weld.result) == (
            "min",
            "s",
            "60/1",
            "60/1",
        )
        assert len(rep.unit_evidence.sha256) == 64

    def test_missing_inputs_block_instead_of_being_guessed(self) -> None:
        base = tandem()
        cases = {
            "MISSING_CALENDAR": base.model_copy(
                update={
                    "machines": (
                        Machine(machine_id="M1"),
                        Machine(machine_id="M2", calendar_id="cont"),
                    )
                }
            ),
            "MISSING_PROCESSING_TIME": base.model_copy(
                update={
                    "routings": (
                        Routing(
                            routing_id="R",
                            operations=(
                                Operation(op_id="CUT", input_buffer="B0", machines=("M1",)),
                                Operation(
                                    op_id="WELD",
                                    input_buffer="B1",
                                    machines=("M2",),
                                    processing=fixed("1", "s"),
                                ),
                            ),
                        ),
                    )
                }
            ),
            "MISSING_BUFFER_CAPACITY": base.model_copy(
                update={
                    "buffers": (
                        Buffer(buffer_id="B0", capacity="UNBOUNDED"),
                        Buffer(buffer_id="B1"),
                    )
                }
            ),
            "UNKNOWN_MACHINE": base.model_copy(
                update={
                    "routings": (
                        Routing(
                            routing_id="R",
                            operations=(
                                Operation(
                                    op_id="CUT",
                                    input_buffer="B0",
                                    machines=("GHOST",),
                                    processing=fixed("1", "s"),
                                ),
                            ),
                        ),
                    )
                }
            ),
            "UNKNOWN_BUFFER": base.model_copy(
                update={
                    "routings": (
                        Routing(
                            routing_id="R",
                            operations=(
                                Operation(
                                    op_id="CUT",
                                    input_buffer="NOPE",
                                    machines=("M1",),
                                    processing=fixed("1", "s"),
                                ),
                            ),
                        ),
                    )
                }
            ),
            "EMPTY_ROUTING": base.model_copy(
                update={"routings": (Routing(routing_id="R", operations=()),)}
            ),
            "BATCH_EXCEEDS_BUFFER": base.model_copy(
                update={"products": (Product(product_id="A", routing_id="R", batch_size=5),)}
            ),
            "INVALID_HORIZON": base.model_copy(update={"horizon": q(0)}),
            "UNKNOWN_PRODUCT": base.model_copy(
                update={
                    "orders": (Order(order_id="O", product_id="Z", quantity=1, release_at=q(0)),)
                }
            ),
            "MISSING_SETUP_TIME": base.model_copy(
                update={
                    "routings": (
                        Routing(
                            routing_id="R",
                            operations=(
                                Operation(
                                    op_id="CUT",
                                    input_buffer="B0",
                                    machines=("M1",),
                                    processing=fixed("1", "s"),
                                    setup_policy=SetupPolicy.EVERY_JOB,
                                ),
                            ),
                        ),
                    )
                }
            ),
            "INVALID_CALENDAR": base.model_copy(
                update={"calendars": (Calendar(calendar_id="cont"),)}
            ),
            "UNSUPPORTED_POLICY": base.model_copy(
                update={"policies": Policies(pools_held_during_interruption=False)}
            ),
        }
        for code, model in cases.items():
            assert code in self.codes(model), code
        missing_stock = single(
            materials=(Material(material_id="S", unit="kg"),),
            routings=(
                Routing(
                    routing_id="R",
                    operations=(
                        Operation(
                            op_id="OP",
                            input_buffer="B",
                            machines=("M",),
                            processing=fixed("1", "s"),
                            materials=(MaterialUse(material_id="S", per_piece=q("1", "kg")),),
                        ),
                    ),
                ),
            ),
        )
        assert "MISSING_MATERIAL_STOCK" in self.codes(missing_stock)
        r = run(cases["MISSING_CALENDAR"])
        assert r.status is ReportStatus.INVALID_INPUT and verify_report(r) and not r.replications

    def test_malformed_distributions(self) -> None:
        tri = Dist(kind=DistKind.TRIANGULAR, low="10", mode="5", high="20", unit="s")
        assert "INVALID_DISTRIBUTION" in self.codes(
            single(
                routings=(
                    Routing(
                        routing_id="R",
                        operations=(
                            Operation(
                                op_id="OP", input_buffer="B", machines=("M",), processing=tri
                            ),
                        ),
                    ),
                )
            )
        )
        exp0 = Dist(kind=DistKind.EXPONENTIAL, mean="0", unit="s")
        assert "INVALID_DISTRIBUTION" in self.codes(
            single(
                routings=(
                    Routing(
                        routing_id="R",
                        operations=(
                            Operation(
                                op_id="OP", input_buffer="B", machines=("M",), processing=exp0
                            ),
                        ),
                    ),
                )
            )
        )
        mixed = Dist(kind=DistKind.DETERMINISTIC, value="1", mean="3", unit="s")
        assert "INVALID_DISTRIBUTION" in self.codes(
            single(
                routings=(
                    Routing(
                        routing_id="R",
                        operations=(
                            Operation(
                                op_id="OP", input_buffer="B", machines=("M",), processing=mixed
                            ),
                        ),
                    ),
                )
            )
        )

    def test_fractional_quantities_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Order(order_id="O", product_id="A", quantity=2.5, release_at=q(0))  # type: ignore[arg-type]
        assert "INVALID_QUANTITY" in self.codes(
            tandem(n=1).model_copy(
                update={
                    "orders": (Order(order_id="O", product_id="A", quantity=0, release_at=q(0)),)
                }
            )
        )

    def test_oversized_and_malformed_models(self) -> None:
        many = tandem().model_copy(
            update={
                "machines": tuple(
                    Machine(machine_id=f"M{i}", calendar_id="cont") for i in range(501)
                )
            }
        )
        assert "MODEL_TOO_LARGE" in self.codes(many)
        assert "DUPLICATE_ID" in self.codes(
            tandem().model_copy(
                update={"machines": (Machine(machine_id="M1", calendar_id="cont"),) * 2}
            )
        )
        with pytest.raises(ValidationError):
            FactoryModel.model_validate({"model_id": "x"})


# ============================================================================ production plan
def _plan(
    quantities: dict[str, str],
    unit: str = "piece",
    status: PlanStatus = PlanStatus.VERIFIED_NUMERICAL_SOLUTION,
) -> ProductionReport:
    return ProductionReport(
        status=status, reason="synthetic", problem_id="PP-SYN", mode=ObjectiveMode.MAXIMIZE_MARGIN,
        quantities=tuple(ProductQuantity(product_id=k, variable_id=f"x_{k}", unit=unit, quantity=Decimal(v)) for k, v in quantities.items()),
    )  # fmt: skip


class TestPlanImport:
    def test_exact_integer_plan_becomes_orders(self) -> None:
        p = import_plan(
            _plan({"A": "10"}),
            policy=QuantityPolicy.EXACT_INTEGER_ONLY,
            batch_sizes={"A": 1},
            release_at=q(0),
        )
        assert [o.quantity for o in p.orders] == [10] and p.lines[0].difference == "0"
        r = run(tandem().model_copy(update={"orders": p.orders}))
        assert r.metrics.completed == 10

    def test_fractional_solver_quantity_needs_an_explicit_policy(self) -> None:
        with pytest.raises(PlanImportError) as e:
            import_plan(
                _plan({"A": "12.5"}),
                policy=QuantityPolicy.EXACT_INTEGER_ONLY,
                batch_sizes={"A": 5},
                release_at=q(0),
            )
        assert e.value.code == "FRACTIONAL_QUANTITY"
        fl = import_plan(
            _plan({"A": "12.5"}),
            policy=QuantityPolicy.FLOOR_TO_BATCH,
            batch_sizes={"A": 5},
            release_at=q(0),
        )
        ce = import_plan(
            _plan({"A": "12.5"}),
            policy=QuantityPolicy.CEIL_TO_BATCH,
            batch_sizes={"A": 5},
            release_at=q(0),
        )
        assert (
            fl.lines[0].simulated_quantity,
            fl.lines[0].solver_quantity,
            fl.lines[0].difference,
        ) == (10, "12.5", "-2.5")
        assert (ce.lines[0].simulated_quantity, ce.lines[0].difference) == (15, "2.5")

    def test_unverified_or_wrong_unit_plans_are_rejected(self) -> None:
        for kw, code in (
            ({"status": PlanStatus.NO_VERIFIED_RECOMMENDATION}, "PLAN_NOT_VERIFIED"),
            ({"unit": "kg"}, "UNIT_MISMATCH"),
            ({"unit": "dozen"}, "UNIT_CONVERSION_REQUIRED"),
        ):
            with pytest.raises(PlanImportError) as e:
                import_plan(
                    _plan({"A": "10"}, **kw),
                    policy=QuantityPolicy.EXACT_INTEGER_ONLY,
                    batch_sizes={"A": 1},
                    release_at=q(0),
                )  # type: ignore[arg-type]
            assert e.value.code == code
        with pytest.raises(PlanImportError) as e2:
            import_plan(
                _plan({"A": "10"}),
                policy=QuantityPolicy.EXACT_INTEGER_ONLY,
                batch_sizes={},
                release_at=q(0),
            )
        assert e2.value.code == "MISSING_BATCH_SIZE"


# ============================================================================ stochastic
def stochastic_line() -> FactoryModel:
    tri = Dist(kind=DistKind.TRIANGULAR, low="50", mode="60", high="90", unit="s")
    fail = FailureModel(
        time_to_failure=Dist(kind=DistKind.EXPONENTIAL, mean="30", unit="min"),
        repair=Dist(kind=DistKind.TRIANGULAR, low="5", mode="10", high="20", unit="min"),
    )
    return tandem(60, 100, 40, 2).model_copy(update={
        "machines": (Machine(machine_id="M1", calendar_id="cont"), Machine(machine_id="M2", calendar_id="cont", failures=fail)),
        "routings": (Routing(routing_id="R", operations=(
            Operation(op_id="CUT", input_buffer="B0", machines=("M1",), processing=tri),
            Operation(op_id="WELD", input_buffer="B1", machines=("M2",), processing=fixed("100", "s")))),),
    })  # fmt: skip


class TestStochastic:
    def test_seeded_replications_are_reproducible(self) -> None:
        a = run(stochastic_line(), seed=7, replications=12)
        b = run(stochastic_line(), seed=7, replications=12)
        c = run(stochastic_line(), seed=8, replications=12)
        assert a.kind is RunKind.STOCHASTIC_ESTIMATE
        assert a.content_sha256 == b.content_sha256
        assert [r.event_digest for r in a.replications] == [r.event_digest for r in b.replications]
        assert [r.event_digest for r in a.replications] != [r.event_digest for r in c.replications]
        assert len({r.event_digest for r in a.replications}) > 1  # replications are independent

    def test_replication_statistics(self) -> None:
        r = run(stochastic_line(), seed=3, replications=12)
        s = {x.metric: x for x in r.summaries}
        thr = s["throughput_per_hour"]
        vals = sorted(float(x.metrics.throughput_per_hour.value) for x in r.replications)
        assert thr.n == 12 and thr.minimum == vals[0] and thr.maximum == vals[-1]
        assert abs(thr.mean - sum(vals) / 12) < 1e-9
        assert thr.ci95 is not None and thr.ci95[0] < thr.mean < thr.ci95[1]
        assert thr.p10 <= thr.median <= thr.p90 and thr.std_error is not None
        assert all(x.metrics.conservation.balanced for x in r.replications)
        few = run(stochastic_line(), seed=3, replications=5)
        assert next(x for x in few.summaries if x.metric == "completed").ci95 is None
        det = run(tandem(), replications=3)
        d = next(x for x in det.summaries if x.metric == "completed")
        assert d.ci95 is None and d.variance == 0 and "deterministic" in d.ci_note

    def test_failures_actually_happen_and_are_accounted(self) -> None:
        r = run(stochastic_line(), seed=11, replications=3)
        assert sum(x.metrics.machine("M2").failures for x in r.replications) > 0
        assert any(x.metrics.machine("M2").down_s > 0 for x in r.replications)
        assert any("CEIL_TO_TICK" in a for a in r.assumptions)


# ============================================================================ reports, persistence
class TestReportsAndPersistence:
    def test_report_is_versioned_and_hash_bound(self) -> None:
        r = run(synthetic_line())
        assert verify_report(r) and r.engine_version and r.model_schema and r.report_schema
        assert (
            r.parameters.seed == 0 and r.replications[0].events > 0 and r.unit_evidence is not None
        )
        assert "not a factory measurement" in r.disclaimer
        forged = r.model_copy(
            update={"status": ReportStatus.COMPLETED, "simulation_id": "sim-forged"}
        )
        assert not verify_report(forged)
        m2 = r.replications[0].metrics.model_copy(update={"completed": 999})
        rep2 = r.replications[0].model_copy(update={"metrics": m2})
        assert not verify_report(r.model_copy(update={"replications": (rep2,)}))

    def test_in_memory_history_and_tamper_detection(self) -> None:
        st = InMemorySimulationStore()
        base = tandem()
        rb = run(base)
        s = Scenario(
            scenario_id="s",
            baseline_model_sha256=model_sha256(base),
            changes=(SetProcessingTime(op_id="WELD", processing=fixed("75", "s")),),
        )
        rs = simulate_scenario(base, s, clock=CLOCK)
        st.save(rb)
        st.save(rs)
        assert [x.simulation_id for x in st.history("F1", model_sha256(base))] == [
            rb.simulation_id,
            rs.simulation_id,
        ]
        assert st.load(rb.simulation_id) == rb
        st.tamper(0, rb.model_dump_json().replace('"completed":10', '"completed":11', 1))
        with pytest.raises(TwinStoreError) as e:
            st.load(rb.simulation_id)
        assert e.value.code == "REPORT_TAMPERED"
        with pytest.raises(TwinStoreError):
            st.save(rb.model_copy(update={"factory_id": "F2"}))

    def test_sqlite_persistence_after_restart(self, tmp_path: Path) -> None:
        db = tmp_path / "twin.db"
        rb = run(synthetic_line())
        SqliteSimulationStore(db).save(rb)
        again = SqliteSimulationStore(db).load(rb.simulation_id)
        assert again == rb and verify_report(again)
        assert [x.simulation_id for x in SqliteSimulationStore(db).history("F1")] == [
            rb.simulation_id
        ]
        assert SqliteSimulationStore(db).history("F2") == []
        con = sqlite3.connect(db)
        con.execute("UPDATE twin_runs SET body = replace(body, '\"seed\":0', '\"seed\":1')")
        con.commit()
        con.close()
        with pytest.raises(TwinStoreError):
            SqliteSimulationStore(db).load(rb.simulation_id)


# ============================================================================ ontology
H = Principal("ana", ActorKind.HUMAN, frozenset({Role.PLANNER}), frozenset({"F1"}))
S = Principal("bot", ActorKind.LLM, frozenset({Role.EXTRACTOR}), frozenset({"F1"}))
EV = (EvidenceRef(kind=EvidenceKind.HUMAN_STATEMENT, ref="synthetic"),)


def onto_factory(*, op2_rate: str = "100", proposed_extra: bool = False, dispute: bool = False):  # type: ignore[no-untyped-def]
    fo = FactoryOntology(InMemoryOntologyStore(), "F1", clock=CLOCK)
    e = lambda c, i, **k: fo.create_entity(
        H, c, i, entity_id=i, basis=Basis.HUMAN_CONFIRMED, evidence=EV, **k
    )
    r = lambda rel, a, b, **k: fo.create_relation(
        H, rel, a, b, basis=Basis.HUMAN_CONFIRMED, evidence=EV, **k
    )
    e("Site", "s1")
    e("ProductionLine", "L1", scope=Scope(site_id="s1"))
    e("Machine", "M1", scope=Scope(site_id="s1"))
    e("Machine", "M2", scope=Scope(site_id="s1"))
    e("Product", "P1")
    e("Material", "STEEL")
    e("Operation", "OP1")
    e("Operation", "OP2")
    r("CONTAINS", "s1", "L1")
    r("CONTAINS", "L1", "M1")
    r("CONTAINS", "L1", "M2")
    r("PRECEDES", "OP1", "OP2")
    r("PRODUCES", "OP2", "P1")
    r("REQUIRES", "OP1", "M1", attrs={"amount": QuantityValue(value="1", unit="min/piece")})
    cap = r(
        "REQUIRES", "OP2", "M2", attrs={"amount": QuantityValue(value=op2_rate, unit="s/piece")}
    )
    r("CONSUMES", "OP1", "STEEL", attrs={"amount": QuantityValue(value="2", unit="kg/piece")})
    if proposed_extra:
        fo.create_relation(
            S, "REQUIRES", "OP2", "M1", attrs={"amount": QuantityValue(value="100", unit="s/piece")}
        )
    if dispute:
        fo.dispute(S, cap.relation_id, "operator says 120 s")
    return fo


def inputs(factory: str = "F1", **kw: object) -> OperationalInputs:
    data: dict[str, object] = dict(
        factory_id=factory, model_id="onto-line", horizon=q(1, "day"),
        input_buffers={"OP1": Buffer(buffer_id="B0", capacity="UNBOUNDED"), "OP2": Buffer(buffer_id="B1", capacity=1)},
        calendars=(CONT,), machine_calendars={"M1": "cont", "M2": "cont"},
        materials=(Material(material_id="STEEL", unit="kg", initial_stock=q("100", "kg")),),
        orders=(Order(order_id="O1", product_id="P1", quantity=10, release_at=q(0)),), batch_sizes={"P1": 1},
    )  # fmt: skip
    data.update(kw)
    return OperationalInputs(**data)  # type: ignore[arg-type]


class TestOntologyIntegration:
    def test_confirmed_snapshot_builds_the_same_line_as_the_reference(self) -> None:
        fo = onto_factory()
        b = build_from_ontology(fo.view(H), fo.ontology, "L1", inputs())
        assert b.ok and b.model is not None
        assert (
            b.snapshot.kind == "ontology"
            and b.snapshot.revision == fo.revision
            and b.snapshot.sha256 == fo.head_sha
        )
        r = run(b.model)
        assert finish_times(r) == ref_tandem(60, 100, 10, 1)["finish"]
        assert r.metrics.materials[0].consumed == "20"
        assert r.sources[0].revision == fo.revision

    def test_ontology_isolation(self) -> None:
        fo = onto_factory()
        with pytest.raises(ValueError, match="ONTOLOGY_FACTORY_MISMATCH"):
            build_from_ontology(fo.view(H), fo.ontology, "L1", inputs(factory="F2"))

    def test_unconfirmed_or_disputed_relations_block(self) -> None:
        for fo in (onto_factory(proposed_extra=True), onto_factory(dispute=True)):
            b = build_from_ontology(fo.view(H), fo.ontology, "L1", inputs())
            assert not b.ok and b.model is None
            assert any(i.code == "UNCONFIRMED_INPUT" for i in b.issues)

    def test_missing_operational_inputs_block(self) -> None:
        fo = onto_factory()
        b = build_from_ontology(
            fo.view(H),
            fo.ontology,
            "L1",
            inputs(input_buffers={"OP1": Buffer(buffer_id="B0", capacity="UNBOUNDED")}),
        )
        assert not b.ok and any(i.code == "MISSING_BUFFER" for i in b.issues)
        b2 = build_from_ontology(
            fo.view(H), fo.ontology, "L1", inputs(machine_calendars={"M1": "cont"})
        )
        assert b2.model is not None
        cm, rep = compile_model(b2.model)
        assert cm is None and any(i.code == "MISSING_CALENDAR" for i in rep.issues)
        b3 = build_from_ontology(fo.view(H), fo.ontology, "L1", inputs(materials=()))
        assert any(i.code == "MISSING_MATERIAL_STOCK" for i in b3.issues)
        assert build_from_ontology(fo.view(H), fo.ontology, "NOPE", inputs()).model is None

    def test_non_integer_ontology_rates_are_not_rounded(self) -> None:
        fo = onto_factory(op2_rate="100.5")
        b = build_from_ontology(fo.view(H), fo.ontology, "L1", inputs())
        assert any(i.code == "DURATION_NOT_ON_RESOLUTION" for i in b.issues)


# ============================================================================ the whole journey
def test_definition_of_done_journey(tmp_path: Path) -> None:
    # 1-2 confirmed ontology snapshot -> validated model with unit evidence
    fo = onto_factory()
    build = build_from_ontology(fo.view(H), fo.ontology, "L1", inputs())
    assert build.ok and build.model is not None
    cm, vrep = compile_model(build.model)
    assert cm is not None and vrep.ok and vrep.unit_evidence is not None
    # 3 a verified production plan with an explicit conversion policy
    plan = import_plan(
        _plan({"P1": "10"}),
        policy=QuantityPolicy.EXACT_INTEGER_ONLY,
        batch_sizes={"P1": 1},
        release_at=q(0),
        due_at=q(1200),
    )
    model = build.model.model_copy(update={"orders": plan.orders})
    # 4 missing / incompatible inputs are rejected
    assert compile_model(model.model_copy(update={"horizon": q(1, "kg")}))[0] is None
    with pytest.raises(PlanImportError):
        import_plan(
            _plan({"P1": "10.5"}),
            policy=QuantityPolicy.EXACT_INTEGER_ONLY,
            batch_sizes={"P1": 1},
            release_at=q(0),
        )
    # 5-7 baseline with real event transitions and metrics
    base = run(model)
    exp = ref_tandem(60, 100, 10, 1)
    assert base.status is ReportStatus.COMPLETED and finish_times(base) == exp["finish"]
    kinds = {k for _, k, *_ in base.replications[0].trace_sample}
    assert {
        "RELEASE",
        "ADMIT",
        "START",
        "PROCESS_START",
        "OP_END",
        "BLOCKED",
        "UNBLOCK",
        "COMPLETE",
    } <= kinds
    m = base.metrics
    assert (
        m.throughput_per_hour
        and m.wip_avg
        and m.avg_lead_time_s
        and m.bottlenecks[0].machine_id == "M2"
    )
    # 8-10 a downtime scenario, simulated independently and compared
    s = Scenario(
        scenario_id="m2-maint",
        baseline_model_sha256=model_sha256(model),
        changes=(AddPlannedDowntime(machine_id="M2", interval=Interval(start=q(300), end=q(600))),),
    )
    scen = simulate_scenario(model, s, clock=CLOCK)
    cmp_ = compare(base, scen)
    assert {d.metric: d for d in cmp_.deltas}["window_s"].change == "300.000000"
    # 11 reproducible stochastic replications
    st1 = run(stochastic_line(), seed=5, replications=10)
    st2 = run(stochastic_line(), seed=5, replications=10)
    assert st1.content_sha256 == st2.content_sha256 and st1.summaries[0].ci95 is not None
    # 12 versioned hash-bound report
    assert verify_report(base) and verify_report(scen) and base.sources[0].kind == "ontology"
    # 13 persist and reload history
    db = tmp_path / "journey.db"
    for r in (base, scen):
        SqliteSimulationStore(db).save(r)
    hist = SqliteSimulationStore(db).history("F1", model_sha256(model))
    assert [h.simulation_id for h in hist] == [base.simulation_id, scen.simulation_id]
    # 14 traceable evidence for Decision Intelligence
    ev = to_evidence(scen, (cmp_,))
    assert ev.ontology_revision == fo.revision and ev.report_sha256 == scen.report_sha256
    assert "not an optimisation guarantee" in ev.statement and len(ev.evidence_sha256) == 64
    with pytest.raises(ValueError, match="REPORT_TAMPERED"):
        to_evidence(scen.model_copy(update={"status": ReportStatus.DEADLOCK}))
    assert RunStatus.COMPLETED.value == base.status.value

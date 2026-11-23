"""Production planning end to end with the REAL HiGHS subprocess and the real orchestrator.

No stub, fake adapter or hand-built result is used for the main flow. Reference optima (2600, 13,
35, 2800) are mathematical facts proved in test_production_planning.py with exact arithmetic; here
the real solver must reproduce them and the independent verifier must accept them.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Any

import pytest

from qorvexa.contracts.solver_runtime import (
    MathOutcome,
    OptimalityTier,
    SolverRuntimeResult,
)
from qorvexa.contracts.solver_runtime import (
    VerificationClaimKind as K,
)
from qorvexa.contracts.solver_runtime import (
    VerificationLevel as L,
)
from qorvexa.industrial.production_planning import (
    CapacityChange,
    CompiledFormulation,
    DemandBoundsChange,
    ProductionReport,
    ReportStatus,
    ScenarioSpec,
    build_production_report,
    compile_problem,
    compile_scenario,
    validate_planning_dict,
)
from qorvexa.solver_fabric import LPOrchestrator, OrchestrationOutcome
from qorvexa.solver_fabric.highs_adapter import HighsAdapter
from qorvexa.solver_fabric.verifier import check_primal, exact_vector
from tests.test_production_planning import (
    canon,
    doc,
    example_min_cost,
    example_multi_resource,
    example_product_mix,
)

TOL = 1e-6


@dataclass
class Solved:
    compiled: CompiledFormulation
    outcome: OrchestrationOutcome
    report: ProductionReport

    @property
    def result(self) -> SolverRuntimeResult:
        assert self.outcome.result is not None
        return self.outcome.result


def solve_formulation(compiled: CompiledFormulation, job: str) -> Solved:
    out = LPOrchestrator(HighsAdapter()).solve_json(compiled.spec_json, job_id=job)
    assert out.model_sha256 is not None and out.result is not None
    rep = build_production_report(
        compiled.manifest, out.result, expected_model_sha256=out.model_sha256
    )
    return Solved(compiled, out, rep)


def solve_raw(raw: dict[str, Any], job: str) -> Solved:
    return solve_formulation(compile_problem(validate_planning_dict(raw)), job)


@pytest.fixture(scope="module")
def mix() -> Solved:
    return solve_raw(example_product_mix(), "e2e-mix")


@pytest.fixture(scope="module")
def multi() -> Solved:
    return solve_raw(example_multi_resource(), "e2e-multi")


@pytest.fixture(scope="module")
def mincost() -> Solved:
    return solve_raw(example_min_cost(), "e2e-mincost")


def assert_trusted_numerical_solution(s: Solved, expected: float, raw: dict[str, Any]) -> None:
    r = s.result
    assert s.outcome.job.state.value == "COMPLETED"
    assert r.outcome == MathOutcome.SOLVED and r.optimality_tier == OptimalityTier.GAP_NUMERICAL
    assert r.objective_value is not None and math.isclose(r.objective_value, expected, abs_tol=TOL)
    kinds = {(c.claim_kind, c.level) for c in r.claims}
    assert (K.MODEL_LOWERING_VALIDATED, L.EXACT_RATIONAL) in kinds
    assert (K.DUAL_GAP, L.NUMERICAL) in kinds and (K.DUAL_GAP, L.EXACT_RATIONAL) not in kinds
    # independent feasibility of the real primal vector against the exact model (binary64 tolerance)
    _, model = canon(raw)
    assert r.primal_solution is not None
    x = exact_vector([r.primal_solution[v] for v in model.variable_ids], model.num_variables, "x")
    pc = check_primal(model, x)
    assert pc.max_violation <= Fraction(1, 10**9)
    sign = -1 if model.original_sense == "maximize" else 1
    assert math.isclose(sign * float(pc.objective_min_form), expected, abs_tol=TOL)
    # report built from the real result
    rep = s.report
    assert rep.status == ReportStatus.VERIFIED_NUMERICAL_SOLUTION, rep.reason
    assert rep.objective_value is not None and math.isclose(
        float(rep.objective_value), expected, abs_tol=TOL
    )
    assert rep.objective_matches_runtime is True
    assert "not an exact optimality proof" in rep.epistemic_note
    for u in rep.utilization:
        assert u.remaining >= -Decimal("1e-6") * max(Decimal(1), u.capacity)
    assert all(d.meets_minimum for d in rep.demand)


class TestRealExamples:
    def test_example_a_product_mix(self, mix: Solved) -> None:
        assert_trusted_numerical_solution(mix, 2600.0, example_product_mix())
        q = {x.product_id: float(x.quantity) for x in mix.report.quantities}
        assert math.isclose(q["A"], 20, abs_tol=1e-6) and math.isclose(q["B"], 60, abs_tol=1e-6)
        assert mix.report.bottleneck_resource_ids == ("LABOR", "MACHINE")
        assert mix.result.metadata["objective_sense"] == "maximize"

    def test_example_b_multi_resource(self, multi: Solved) -> None:
        assert_trusted_numerical_solution(multi, 13.0, example_multi_resource())
        used = {u.resource_id: float(u.used) for u in multi.report.utilization}
        cap = {u.resource_id: float(u.capacity) for u in multi.report.utilization}
        assert all(used[k] <= cap[k] + 1e-6 for k in cap)
        assert math.isclose(used["R1"], 5, abs_tol=1e-6) and math.isclose(
            used["R3"], 8, abs_tol=1e-6
        )
        assert "R2" not in multi.report.bottleneck_resource_ids  # 10 of 11 used

    def test_example_c_minimize_cost(self, mincost: Solved) -> None:
        assert_trusted_numerical_solution(mincost, 35.0, example_min_cost())
        assert mincost.result.metadata["objective_sense"] == "minimize"
        dem = {d.product_id: d for d in mincost.report.demand}
        assert dem["A"].meets_minimum and dem["B"].meets_minimum

    def test_report_is_built_from_the_actual_runtime_result(self, mix: Solved) -> None:
        again = build_production_report(
            mix.compiled.manifest,
            mix.result,
            expected_model_sha256=mix.outcome.model_sha256 or "",
        )
        assert again == mix.report
        assert mix.result.lowering_evidence is not None
        assert mix.result.lowering_evidence.model_sha256 == mix.outcome.model_sha256

    def test_decimal_business_data_end_to_end(self) -> None:
        raw = doc(
            "MAXIMIZE_MARGIN",
            [("A", "0.1", "0", None), ("B", "0.2", "0", None)],
            [("R", "10")],
            {("R", "A"): "1", ("R", "B"): "1"},
        )
        s = solve_raw(raw, "e2e-decimal")
        assert s.report.status == ReportStatus.VERIFIED_NUMERICAL_SOLUTION
        assert s.report.objective_value is not None
        assert math.isclose(float(s.report.objective_value), 2.0, abs_tol=1e-9)  # 10 x B


class TestRejectionOfUntrustedResults:
    def test_wrong_model_hash(self, mix: Solved) -> None:
        rep = build_production_report(
            mix.compiled.manifest, mix.result, expected_model_sha256="d" * 64
        )
        assert rep.status == ReportStatus.NO_VERIFIED_RECOMMENDATION and rep.quantities == ()

    def test_result_of_another_problem_is_not_reusable(self, mix: Solved, multi: Solved) -> None:
        rep = build_production_report(
            multi.compiled.manifest,
            mix.result,
            expected_model_sha256=multi.outcome.model_sha256 or "",
        )
        assert rep.status == ReportStatus.NO_VERIFIED_RECOMMENDATION

    def test_variable_mapping_mismatch(self, mix: Solved) -> None:
        man = mix.compiled.manifest
        bad_vars = (
            man.variables[0].model_copy(update={"variable_id": "VAR-0009"}),
            *man.variables[1:],
        )
        bad = man.model_copy(update={"variables": bad_vars})
        rep = build_production_report(
            bad, mix.result, expected_model_sha256=mix.outcome.model_sha256 or ""
        )
        assert rep.status == ReportStatus.NO_VERIFIED_RECOMMENDATION

    def test_unknown_and_failed_results_yield_no_recommendation(self) -> None:
        class Corrupt:
            """Delegates to real HiGHS, then discards the duals (isolated failure handling)."""

            name = "corrupt-real"

            def __init__(self) -> None:
                self._real = HighsAdapter()

            def solve(self, request: Any) -> Any:
                r = self._real.solve(request)
                return r.model_copy(update={"dual_solution": None})

        class Dead:
            name = "dead"

            def solve(self, request: Any) -> Any:
                raise RuntimeError("worker died")

        c = compile_problem(validate_planning_dict(example_product_mix()))
        for adapter, outcome in ((Corrupt(), MathOutcome.UNKNOWN), (Dead(), MathOutcome.FAILED)):
            out = LPOrchestrator(adapter).solve_json(c.spec_json, job_id="e2e-bad")
            assert out.result is not None and out.model_sha256 is not None
            assert out.result.outcome == outcome
            rep = build_production_report(
                c.manifest, out.result, expected_model_sha256=out.model_sha256
            )
            assert rep.status == ReportStatus.NO_VERIFIED_RECOMMENDATION
            assert rep.quantities == () and rep.bottleneck_resource_ids == ()  # no raw promotion


class TestScenarioWorkflow:
    def test_capacity_what_if_solved_independently_and_compared(self, mix: Solved) -> None:
        base = validate_planning_dict(example_product_mix())
        before = base.model_dump()
        spec = ScenarioSpec(
            scenario_id="MACH120",
            description="more machine hours",
            changes=(CapacityChange(resource_id="MACHINE", capacity="120"),),
        )
        scen = compile_scenario(base, spec)
        assert base.model_dump() == before  # base immutable
        assert scen.status == "FORMULATED_NOT_SOLVED"  # formulated, not solved
        assert scen.formulation.manifest.lineage[1] == f"base_input_sha256:{scen.base_input_sha256}"

        solved = solve_formulation(scen.formulation, "e2e-scn")
        assert solved.outcome.model_sha256 != mix.outcome.model_sha256  # distinct model
        assert solved.report.status == ReportStatus.VERIFIED_NUMERICAL_SOLUTION
        assert solved.report.problem_id == "PP-TEST::MACH120"
        assert solved.report.objective_value is not None and mix.report.objective_value is not None
        assert math.isclose(float(solved.report.objective_value), 2800.0, abs_tol=TOL)

        # no result is reused across model hashes
        cross = build_production_report(
            scen.formulation.manifest,
            mix.result,
            expected_model_sha256=solved.outcome.model_sha256 or "",
        )
        assert cross.status == ReportStatus.NO_VERIFIED_RECOMMENDATION

        delta = float(solved.report.objective_value - mix.report.objective_value)
        assert math.isclose(delta, 200.0, abs_tol=TOL)
        base_q = {q.product_id: float(q.quantity) for q in mix.report.quantities}
        scen_q = {q.product_id: float(q.quantity) for q in solved.report.quantities}
        assert math.isclose(scen_q["A"], 40, abs_tol=1e-6) and math.isclose(
            scen_q["B"], 40, abs_tol=1e-6
        )
        assert math.isclose(scen_q["B"] - base_q["B"], -20, abs_tol=1e-6)
        # the original base report is untouched by the scenario run
        assert math.isclose(float(mix.report.objective_value), 2600.0, abs_tol=TOL)

    def test_demand_what_if_changes_the_model_and_the_answer(self, mincost: Solved) -> None:
        base = validate_planning_dict(example_min_cost())
        scen = compile_scenario(
            base,
            ScenarioSpec(
                scenario_id="B4",
                changes=(DemandBoundsChange(product_id="B", min_quantity="4"),),
            ),
        )
        s = solve_formulation(scen.formulation, "e2e-scn2")
        assert s.outcome.model_sha256 != mincost.outcome.model_sha256
        assert s.report.objective_value is not None
        assert math.isclose(
            float(s.report.objective_value), 32.0, abs_tol=TOL
        )  # 2*10 + 3*4 (machine 10+8 <= 20)

    def test_infeasible_what_if_is_certified_exactly(self) -> None:
        base = validate_planning_dict(example_min_cost())
        scen = compile_scenario(
            base,
            ScenarioSpec(
                scenario_id="CAP15",
                changes=(CapacityChange(resource_id="MACHINE", capacity="15"),),
            ),
        )
        s = solve_formulation(scen.formulation, "e2e-inf")
        assert s.result.outcome == MathOutcome.INFEASIBLE
        assert s.result.infeasibility_evidence is not None
        assert Fraction(s.result.infeasibility_evidence.certificate_value) > 0
        assert s.report.status == ReportStatus.INFEASIBLE_VERIFIED
        assert s.report.quantities == ()


class TestBoundaries:
    def test_unbounded_plan_needs_exact_anchor_and_ray(self) -> None:
        raw = doc(
            "MAXIMIZE_MARGIN",
            [("A", "5", "0", None), ("B", "1", "0", "4")],
            [("R", "10")],
            {("R", "B"): "1"},
        )
        s = solve_raw(raw, "e2e-unb")
        r = s.result
        if r.outcome == MathOutcome.UNBOUNDED:
            assert r.unbounded_evidence is not None
            assert Fraction(r.unbounded_evidence.objective_slope_min_form) < 0
            assert s.report.status == ReportStatus.UNBOUNDED_VERIFIED
        else:
            assert r.outcome == MathOutcome.UNKNOWN
            assert s.report.status == ReportStatus.NO_VERIFIED_RECOMMENDATION
        assert s.report.quantities == ()

    def test_unbounded_without_a_solver_ray_fails_closed(self) -> None:
        raw = doc("MAXIMIZE_MARGIN", [("A", "5", "0", None)], [("R", "10")], {})
        s = solve_raw(raw, "e2e-unb2")
        assert s.result.outcome in (MathOutcome.UNKNOWN, MathOutcome.UNBOUNDED)
        if s.result.outcome == MathOutcome.UNKNOWN:
            assert s.result.unbounded_evidence is None
            assert s.report.status == ReportStatus.NO_VERIFIED_RECOMMENDATION

    def test_spec_json_is_plain_strict_json(self, mix: Solved) -> None:
        d = json.loads(mix.compiled.spec_json)
        assert d["problemType"] == "linear_programming"
        assert d["objectives"][0]["direction"] == "maximize"

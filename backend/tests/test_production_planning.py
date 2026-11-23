"""Production-planning formulation tests (analytic oracles; no solver is implemented or faked).

Optimality of the worked examples is *proved* with the existing independent exact verifier
(hand-derived primal point + hand-derived dual multipliers => equal primal/dual objectives), not by
solving anything here.
"""

from __future__ import annotations

import copy
import json
from decimal import Decimal
from fractions import Fraction
from typing import Any

import pytest
from pydantic import ValidationError

from qorvexa.contracts.solver_runtime import (
    FarkasEvidence,
    LoweringEvidence,
    MathOutcome,
    OptimalityTier,
    RoundingSummary,
    SolverRuntimeResult,
    SolverTermination,
    VerificationClaim,
)
from qorvexa.contracts.solver_runtime import (
    VerificationClaimKind as K,
)
from qorvexa.contracts.solver_runtime import (
    VerificationLevel as L,
)
from qorvexa.contracts.solver_runtime import (
    VerificationStatus as S,
)
from qorvexa.industrial.production_planning import (
    CapacityChange,
    DemandBoundsChange,
    ObjectiveMode,
    PlanningInputError,
    ReportStatus,
    ScenarioSpec,
    ValueChange,
    build_production_report,
    compile_problem,
    compile_scenario,
    parse_planning_json,
    parse_scenario,
    validate_planning_dict,
)
from qorvexa.solver_fabric import (
    CANONICALIZED_INPUT_VALUES,
    CanonicalRangedLP,
    LPOrchestrator,
    build_model_from_derived,
    derive_problem_forms,
    lower_ranged_lp,
)
from qorvexa.solver_fabric.verifier import (
    check_primal,
    dual_bound_min_form,
    find_interval_conflict,
    verify_lowering,
)
from qorvexa.solver_fabric.worker_contract import (
    WorkerFailure,
    WorkerFailureCode,
    WorkerRequest,
    WorkerResponse,
    WorkerStatus,
)

F = Fraction


# ------------------------------------------------------------------ fixtures (synthetic data)


def doc(
    mode: str,
    products: list[tuple[str, str, str, str | None]],  # id, value, min, max
    resources: list[tuple[str, str]],  # id, capacity
    use: dict[tuple[str, str], str],  # (resource, product) -> amount
) -> dict[str, Any]:
    return {
        "metadata": {"problem_id": "PP-TEST", "title": "synthetic"},
        "mode": mode,
        "currency": "USD",
        "products": [
            {
                "id": i,
                "name": f"Product {i}",
                "unit": "pcs",
                "value_per_unit": v,
                "value_currency": "USD",
                "min_quantity": lo,
                **({"max_quantity": hi} if hi is not None else {}),
            }
            for i, v, lo, hi in products
        ],
        "resources": [
            {"id": r, "name": f"Resource {r}", "kind": "MACHINE", "unit": "h", "capacity": c}
            for r, c in resources
        ],
        "consumptions": [
            {
                "resource_id": r,
                "product_id": p,
                "amount_per_unit": a,
                "resource_unit": "h",
                "product_unit": "pcs",
            }
            for (r, p), a in use.items()
        ],
    }


def example_product_mix() -> dict[str, Any]:
    """Example 1 - resource-constrained product mix: max 40A+30B; 2A+B<=100 (machine);
    A+B<=80 (labor); A<=40.  Optimum 2600 at (20, 60)."""
    return doc(
        "MAXIMIZE_MARGIN",
        [("A", "40", "0", "40"), ("B", "30", "0", None)],
        [("LABOR", "80"), ("MACHINE", "100")],
        {
            ("MACHINE", "A"): "2",
            ("MACHINE", "B"): "1",
            ("LABOR", "A"): "1",
            ("LABOR", "B"): "1",
        },
    )


def example_multi_resource() -> dict[str, Any]:
    """Example 2 - multi-resource plan (3 products x 3 resources): max 5A+4B+3C;
    2A+3B+C<=5; 4A+B+2C<=11; 3A+4B+2C<=8.  Optimum 13 at (2, 0, 1)."""
    return doc(
        "MAXIMIZE_MARGIN",
        [("A", "5", "0", None), ("B", "4", "0", None), ("C", "3", "0", None)],
        [("R1", "5"), ("R2", "11"), ("R3", "8")],
        {
            ("R1", "A"): "2",
            ("R1", "B"): "3",
            ("R1", "C"): "1",
            ("R2", "A"): "4",
            ("R2", "B"): "1",
            ("R2", "C"): "2",
            ("R3", "A"): "3",
            ("R3", "B"): "4",
            ("R3", "C"): "2",
        },
    )


def example_min_cost() -> dict[str, Any]:
    """Example 3 - demand-commitment cost minimization: min 2A+3B; A in [10,12]; B>=5;
    A+2B<=20 (machine).  Optimum 35 at (10, 5)."""
    return doc(
        "MINIMIZE_COST",
        [("A", "2", "10", "12"), ("B", "3", "5", None)],
        [("MACHINE", "20")],
        {("MACHINE", "A"): "1", ("MACHINE", "B"): "2"},
    )


def canon(raw: dict[str, Any]) -> tuple[Any, CanonicalRangedLP]:
    compiled = compile_problem(validate_planning_dict(raw))
    model = build_model_from_derived(derive_problem_forms(compiled.to_spec()), None)
    return compiled, model


def err(raw: dict[str, Any]) -> PlanningInputError:
    with pytest.raises(PlanningInputError) as ei:
        validate_planning_dict(raw)
    return ei.value


# ------------------------------------------------------------------ exact formulation oracles


class TestExample1ProductMix:
    def test_exact_coefficients_bounds_and_sense(self) -> None:
        c, m = canon(example_product_mix())
        assert m.original_sense == "maximize"
        assert m.variable_ids == ("VAR-0001", "VAR-0002")
        assert m.cost == (F(-40), F(-30))  # compiler negates maximize -> minimize
        assert m.offset == 0
        assert m.row_ids == ("CAP-0001", "CAP-0002")  # LABOR, MACHINE (sorted by id)
        assert m.rows == (((0, F(1)), (1, F(1))), ((0, F(2)), (1, F(1))))
        assert (m.row_lower, m.row_upper) == ((None, None), (F(80), F(100)))
        assert (m.col_lower, m.col_upper) == ((F(0), F(0)), (F(40), None))
        assert [t.coefficient for t in c.manifest.objective.terms] == [Decimal(40), Decimal(30)]

    def test_optimality_is_proved_by_the_independent_verifier(self) -> None:
        _, m = canon(example_product_mix())
        x = [F(20), F(60)]
        pc = check_primal(m, x)
        assert pc.exactly_feasible and pc.objective_min_form == F(-2600)
        # min-form multipliers: LABOR -20, MACHINE -10 (reduced costs vanish on the basic columns)
        assert dual_bound_min_form(m, [F(-20), F(-10)]) == F(-2600)

    def test_not_the_neighbouring_vertices(self) -> None:
        _, m = canon(example_product_mix())
        for x, obj in (([F(40), F(20)], -2200), ([F(0), F(80)], -2400)):
            pc = check_primal(m, x)
            assert pc.exactly_feasible and pc.objective_min_form == obj > -2600


class TestExample2MultiResource:
    def test_exact_matrix(self) -> None:
        _, m = canon(example_multi_resource())
        assert m.cost == (F(-5), F(-4), F(-3))
        assert m.rows == (
            ((0, F(2)), (1, F(3)), (2, F(1))),
            ((0, F(4)), (1, F(1)), (2, F(2))),
            ((0, F(3)), (1, F(4)), (2, F(2))),
        )
        assert m.row_upper == (F(5), F(11), F(8)) and m.row_lower == (None, None, None)

    def test_optimality_is_proved_by_the_independent_verifier(self) -> None:
        _, m = canon(example_multi_resource())
        pc = check_primal(m, [F(2), F(0), F(1)])
        assert pc.exactly_feasible and pc.objective_min_form == F(-13)
        assert dual_bound_min_form(m, [F(-1), F(0), F(-1)]) == F(-13)


class TestExample3MinimizeCost:
    def test_exact_formulation_and_sense(self) -> None:
        _, m = canon(example_min_cost())
        assert m.original_sense == "minimize" and m.cost == (F(2), F(3))
        assert (m.col_lower, m.col_upper) == ((F(10), F(5)), (F(12), None))
        assert m.rows == (((0, F(1)), (1, F(2))),) and m.row_upper == (F(20),)

    def test_optimality_is_proved_by_the_independent_verifier(self) -> None:
        _, m = canon(example_min_cost())
        pc = check_primal(m, [F(10), F(5)])
        assert pc.exactly_feasible and pc.objective_min_form == F(35)
        assert dual_bound_min_form(m, [F(0)]) == F(35)  # lower-bound reduced costs 2, 3 >= 0

    def test_commitments_exceeding_capacity_are_exactly_infeasible(self) -> None:
        raw = example_min_cost()
        raw["resources"][0]["capacity"] = "15"  # 10 + 2*5 = 20 > 15
        _, m = canon(raw)
        v = find_interval_conflict(m)
        assert v is not None and v.certificate_value == F(5)


class TestEdgeFormulations:
    def test_single_product(self) -> None:
        raw = doc("MAXIMIZE_MARGIN", [("A", "3", "0", "7")], [("R", "10")], {("R", "A"): "2"})
        _, m = canon(raw)
        assert m.cost == (F(-3),) and m.rows == (((0, F(2)),),) and m.col_upper == (F(7),)

    def test_zero_capacity_row_is_kept_and_forces_zero(self) -> None:
        raw = doc("MAXIMIZE_MARGIN", [("A", "3", "0", None)], [("R", "0")], {("R", "A"): "2"})
        _, m = canon(raw)
        assert m.row_upper == (F(0),)
        assert check_primal(m, [F(0)]).exactly_feasible
        assert not check_primal(m, [F(1)]).exactly_feasible

    def test_resource_without_consumption_becomes_an_empty_row(self) -> None:
        raw = doc(
            "MAXIMIZE_MARGIN", [("A", "3", "0", "1")], [("R", "5"), ("S", "9")], {("R", "A"): "1"}
        )
        c, m = canon(raw)
        assert m.rows[1] == () and m.row_upper[1] == F(9)
        assert c.manifest.constraints[1].terms == ()

    def test_zero_consumption_and_zero_margin_are_preserved(self) -> None:
        raw = doc("MAXIMIZE_MARGIN", [("A", "0", "0", "4")], [("R", "5")], {("R", "A"): "0"})
        c, m = canon(raw)
        assert m.cost == (F(0),)
        assert c.manifest.constraints[0].terms[0].coefficient == 0

    def test_negative_margin_is_allowed_for_maximize(self) -> None:
        raw = doc("MAXIMIZE_MARGIN", [("A", "-2", "0", "4")], [("R", "5")], {("R", "A"): "1"})
        assert canon(raw)[1].cost == (F(2),)

    def test_non_dyadic_decimals_are_canonicalized_by_the_existing_pipeline(self) -> None:
        raw = doc("MAXIMIZE_MARGIN", [("A", "0.1", "0", "4")], [("R", "5")], {("R", "A"): "1"})
        c, m = canon(raw)
        assert m.cost == (-F(0.1),)  # exact binary64 value of 0.1, M1 canonicalization
        assert c.manifest.objective.terms[0].coefficient == Decimal("0.1")  # source preserved

    def test_demand_bounds_are_variable_bounds_not_rows(self) -> None:
        c, m = canon(example_min_cost())
        assert m.num_rows == 1
        v = c.manifest.variable_for_product("A")
        assert (v.lower, v.upper) == (Decimal(10), Decimal(12))
        assert (
            v.lower_source == "product:A.min_quantity"
            and v.upper_source == "product:A.max_quantity"
        )
        assert c.manifest.variable_for_product("B").upper_source is None


# ------------------------------------------------------------------ determinism


class TestDeterminism:
    def test_stable_across_runs_and_input_order(self) -> None:
        a = compile_problem(validate_planning_dict(example_multi_resource()))
        b = compile_problem(validate_planning_dict(example_multi_resource()))
        assert a.spec_json == b.spec_json and a.manifest == b.manifest
        shuffled = example_multi_resource()
        shuffled["products"].reverse()
        shuffled["resources"].reverse()
        shuffled["consumptions"].reverse()
        c = compile_problem(validate_planning_dict(shuffled))
        assert c.spec_json == a.spec_json
        assert c.manifest.input_sha256 == a.manifest.input_sha256

    def test_ids_are_stable_and_sorted_by_business_id(self) -> None:
        raw = example_multi_resource()
        raw["products"].reverse()
        c = compile_problem(validate_planning_dict(raw))
        assert [(v.variable_id, v.product_id) for v in c.manifest.variables] == [
            ("VAR-0001", "A"),
            ("VAR-0002", "B"),
            ("VAR-0003", "C"),
        ]
        assert [(k.constraint_id, k.resource_id) for k in c.manifest.constraints] == [
            ("CAP-0001", "R1"),
            ("CAP-0002", "R2"),
            ("CAP-0003", "R3"),
        ]

    def test_different_inputs_change_the_hash(self) -> None:
        a = compile_problem(validate_planning_dict(example_product_mix()))
        raw = example_product_mix()
        raw["resources"][0]["capacity"] = "81"
        b = compile_problem(validate_planning_dict(raw))
        assert a.manifest.input_sha256 != b.manifest.input_sha256
        assert a.manifest.spec_sha256 != b.manifest.spec_sha256


# ------------------------------------------------------------------ manifest consistency


class TestManifest:
    def test_manifest_matches_the_canonical_model_exactly(self) -> None:
        c, m = canon(example_multi_resource())
        man = c.manifest
        assert tuple(v.variable_id for v in man.variables) == m.variable_ids
        assert tuple(k.constraint_id for k in man.constraints) == m.row_ids
        sign = -1 if man.mode is ObjectiveMode.MAXIMIZE_MARGIN else 1
        for t in man.objective.terms:
            j = m.variable_ids.index(t.variable_id)
            assert m.cost[j] == sign * F(t.coefficient)
        for i, k in enumerate(man.constraints):
            assert dict(m.rows[i]) == {
                m.variable_ids.index(t.variable_id): F(t.coefficient) for t in k.terms
            }
            assert m.row_upper[i] == F(k.capacity)
        for t in man.objective.terms + tuple(x for k in man.constraints for x in k.terms):
            assert man.variable_for_product(t.product_id).variable_id == t.variable_id
            assert t.source.startswith(("product:", "consumption:"))

    def test_input_records_map_to_terms(self) -> None:
        c, _ = canon(example_product_mix())
        srcs = {t.source for k in c.manifest.constraints for t in k.terms}
        assert srcs == {
            "consumption:MACHINE:A",
            "consumption:MACHINE:B",
            "consumption:LABOR:A",
            "consumption:LABOR:B",
        }

    def test_expression_summary_is_inert_text(self) -> None:
        c, _ = canon(example_product_mix())
        d = json.loads(c.spec_json)
        for item in d["objectives"] + d["constraints"]:
            assert "x[p]" in item["expressionSummary"] and "(" not in item["expressionAst"].get(
                "operator", ""
            )


# ------------------------------------------------------------------ adversarial inputs


class TestValidation:
    def test_duplicate_ids(self) -> None:
        raw = example_product_mix()
        raw["products"].append(copy.deepcopy(raw["products"][0]))
        assert err(raw).code == "DUPLICATE_ID"
        raw = example_product_mix()
        raw["resources"].append(copy.deepcopy(raw["resources"][0]))
        assert err(raw).code == "DUPLICATE_ID"

    def test_duplicate_consumption(self) -> None:
        raw = example_product_mix()
        raw["consumptions"].append(copy.deepcopy(raw["consumptions"][0]))
        assert err(raw).code == "DUPLICATE_CONSUMPTION"

    def test_missing_references(self) -> None:
        raw = example_product_mix()
        raw["consumptions"][0]["product_id"] = "ZZZ"
        assert err(raw).code == "UNKNOWN_REFERENCE"
        raw = example_product_mix()
        raw["consumptions"][0]["resource_id"] = "ZZZ"
        assert err(raw).code == "UNKNOWN_REFERENCE"

    def test_unit_mismatches_are_never_converted(self) -> None:
        raw = example_product_mix()
        raw["consumptions"][0]["resource_unit"] = "min"
        assert err(raw).code == "UNIT_MISMATCH"
        raw = example_product_mix()
        raw["consumptions"][0]["product_unit"] = "kg"
        assert err(raw).code == "UNIT_MISMATCH"
        raw = example_product_mix()
        raw["products"][0]["value_currency"] = "EUR"
        assert err(raw).code == "CURRENCY_MISMATCH"

    @pytest.mark.parametrize("unit", ["unknown", "N/A", "", " h", "TBD", "x" * 33])
    def test_unspecified_or_malformed_units(self, unit: str) -> None:
        raw = example_product_mix()
        raw["resources"][0]["unit"] = unit
        assert err(raw).code == "INVALID_VALUE"

    def test_negative_values(self) -> None:
        for path, key in (
            ("consumptions", "amount_per_unit"),
            ("resources", "capacity"),
            ("products", "min_quantity"),
        ):
            raw = example_product_mix()
            raw[path][0][key] = "-1"
            assert err(raw).code == "INVALID_VALUE"

    def test_contradictory_product_bounds(self) -> None:
        raw = example_product_mix()
        raw["products"][0]["min_quantity"] = "50"  # max is 40
        e = err(raw)
        assert e.code == "INVALID_VALUE" and "exceeds" in e.message

    def test_missing_margin_or_cost(self) -> None:
        raw = example_product_mix()
        del raw["products"][0]["value_per_unit"]
        assert err(raw).code == "MISSING_FIELD"

    def test_empty_inputs(self) -> None:
        raw = example_product_mix()
        raw["products"] = []
        assert err(raw).code == "EMPTY_PRODUCTS"
        raw = example_product_mix()
        raw["resources"] = []
        assert err(raw).code == "EMPTY_RESOURCES"

    def test_oversized_inputs(self) -> None:
        raw = doc(
            "MAXIMIZE_MARGIN", [(f"P{i}", "1", "0", None) for i in range(201)], [("R", "1")], {}
        )
        assert err(raw).code == "TOO_LARGE"
        with pytest.raises(PlanningInputError) as ei:
            parse_planning_json(b" " * 1_000_001)
        assert ei.value.code == "INPUT_TOO_LARGE"

    @pytest.mark.parametrize(
        "key",
        [
            "integer",
            "binary",
            "setup_time",
            "lot_size",
            "sequence",
            "schedule",
            "nonlinear",
            "piecewise",
        ],
    )
    def test_unsupported_features_are_refused_explicitly(self, key: str) -> None:
        raw = example_product_mix()
        raw["products"][0][key] = True
        e = err(raw)
        assert e.code == "UNSUPPORTED_FEATURE" and key in e.message

    def test_unknown_field_is_refused(self) -> None:
        raw = example_product_mix()
        raw["products"][0]["mystery"] = 1
        assert err(raw).code == "UNSUPPORTED_OR_UNKNOWN_FIELD"

    def test_binary_floats_and_bools_are_refused(self) -> None:
        raw = example_product_mix()
        raw["products"][0]["value_per_unit"] = 0.1
        assert err(raw).code == "INVALID_VALUE"
        raw["products"][0]["value_per_unit"] = True
        assert err(raw).code == "INVALID_VALUE"

    def test_bad_ids_and_magnitudes(self) -> None:
        raw = example_product_mix()
        raw["products"][0]["id"] = "bad id!"
        assert err(raw).code == "INVALID_VALUE"
        raw = example_product_mix()
        raw["resources"][0]["capacity"] = "1e13"
        assert err(raw).code == "INVALID_VALUE"
        raw = example_product_mix()
        raw["resources"][0]["capacity"] = "1.234567890123456789"
        assert err(raw).code == "INVALID_VALUE"

    def test_minimize_cost_rules(self) -> None:
        raw = example_min_cost()
        raw["products"][0]["value_per_unit"] = "-1"
        assert err(raw).code == "NEGATIVE_COST"
        raw = example_min_cost()
        for p in raw["products"]:
            p["min_quantity"] = "0"
        assert err(raw).code == "NO_COMMITMENTS"

    def test_json_level_attacks(self) -> None:
        good = json.dumps(example_product_mix())
        assert parse_planning_json(good).mode is ObjectiveMode.MAXIMIZE_MARGIN
        for bad, code in (
            ("{", "INVALID_JSON"),
            ("[]", "INVALID_JSON"),
            ('{"a":1,"a":2}', "DUPLICATE_KEY"),
            (good.replace('"capacity": "80"', '"capacity": NaN'), "NON_FINITE_NUMBER"),
            (good.replace('"capacity": "80"', '"capacity": Infinity'), "NON_FINITE_NUMBER"),
            ("\xff".encode("latin-1").decode("latin-1") + "{}", "INVALID_JSON"),
        ):
            with pytest.raises(PlanningInputError) as ei:
                parse_planning_json(bad)
            assert ei.value.code == code

    def test_json_numbers_are_exact_decimals(self) -> None:
        text = json.dumps(example_product_mix()).replace('"capacity": "80"', '"capacity": 80.5')
        p = parse_planning_json(text)
        assert next(r for r in p.resources if r.id == "LABOR").capacity == Decimal("80.5")

    def test_models_are_immutable(self) -> None:
        p = validate_planning_dict(example_product_mix())
        with pytest.raises(ValidationError):  # frozen instance
            p.products[0].id = "X"


# ------------------------------------------------------------------ scenarios


class TestScenarios:
    def base(self) -> Any:
        return validate_planning_dict(example_product_mix())

    def test_capacity_scenario_is_isolated_and_traceable(self) -> None:
        base = self.base()
        before = base.model_dump()
        s = compile_scenario(
            base,
            ScenarioSpec(
                scenario_id="S1", changes=(CapacityChange(resource_id="MACHINE", capacity="120"),)
            ),
        )
        assert base.model_dump() == before  # base untouched
        assert s.status == "FORMULATED_NOT_SOLVED"
        assert s.formulation.manifest.constraints[1].capacity == Decimal(120)
        assert s.formulation.manifest.constraints[0].capacity == Decimal(80)
        assert s.change_log == ("resource:MACHINE.capacity 100 -> 120",)
        assert s.base_input_sha256 != s.scenario_input_sha256
        lin = s.formulation.manifest.lineage
        assert f"base_input_sha256:{s.base_input_sha256}" in lin and "scenario:S1" in lin
        assert s.formulation.manifest.problem_id == "PP-TEST::S1"
        _, m = canon_from(s)
        assert m.row_upper == (F(80), F(120))

    def test_demand_and_value_scenarios_and_independence(self) -> None:
        base = self.base()
        s1 = compile_scenario(
            base,
            ScenarioSpec(
                scenario_id="S2",
                parent_scenario_id="S1",
                changes=(
                    DemandBoundsChange(product_id="A", min_quantity="5", clear_max=True),
                    ValueChange(product_id="B", value_per_unit="35"),
                ),
            ),
        )
        s2 = compile_scenario(
            base,
            ScenarioSpec(
                scenario_id="S3", changes=(ValueChange(product_id="B", value_per_unit="31"),)
            ),
        )
        v = s1.formulation.manifest.variable_for_product("A")
        assert (v.lower, v.upper) == (Decimal(5), None)
        assert "parent_scenario:S1" in s1.formulation.manifest.lineage
        _, m1 = canon_from(s1)
        _, m2 = canon_from(s2)
        assert m1.cost == (F(-40), F(-35)) and m2.cost == (F(-40), F(-31))
        assert m2.col_upper == (F(40), None)  # S3 did not inherit S2's change
        again = compile_scenario(
            base,
            ScenarioSpec(
                scenario_id="S3", changes=(ValueChange(product_id="B", value_per_unit="31"),)
            ),
        )
        assert again.formulation.spec_json == s2.formulation.spec_json  # deterministic

    def test_invalid_scenarios_are_rejected(self) -> None:
        base = self.base()
        with pytest.raises(PlanningInputError) as ei:
            compile_scenario(
                base,
                ScenarioSpec(
                    scenario_id="S", changes=(CapacityChange(resource_id="NOPE", capacity="1"),)
                ),
            )
        assert ei.value.code == "UNKNOWN_REFERENCE"
        with pytest.raises(PlanningInputError) as ei2:  # min above existing max 40
            compile_scenario(
                base,
                ScenarioSpec(
                    scenario_id="S",
                    changes=(DemandBoundsChange(product_id="A", min_quantity="99"),),
                ),
            )
        assert ei2.value.code == "INVALID_VALUE"
        with pytest.raises(PlanningInputError):
            parse_scenario({"scenario_id": "S", "changes": []})
        with pytest.raises(PlanningInputError):  # two changes for one target are ambiguous
            parse_scenario(
                {
                    "scenario_id": "S",
                    "changes": [
                        {"kind": "capacity", "resource_id": "MACHINE", "capacity": "1"},
                        {"kind": "capacity", "resource_id": "MACHINE", "capacity": "2"},
                    ],
                }
            )
        with pytest.raises(PlanningInputError):
            parse_scenario(
                {
                    "scenario_id": "S",
                    "changes": [{"kind": "capacity", "resource_id": "M", "capacity": 1.5}],
                }
            )


def canon_from(s: Any) -> tuple[Any, CanonicalRangedLP]:
    return s.formulation, build_model_from_derived(
        derive_problem_forms(s.formulation.to_spec()), None
    )


# ------------------------------------------------------------------ existing pipeline compatibility


class StubUnavailable:
    """Not a solver: reports the worker as unavailable so only the formulation path is exercised."""

    name = "stub-unavailable"

    def __init__(self) -> None:
        self.requests: list[WorkerRequest] = []

    def solve(self, request: WorkerRequest) -> WorkerResponse:
        self.requests.append(request)
        from qorvexa.contracts.solver_runtime import SolverTermination as T

        return WorkerResponse(
            request_id=request.request_id,
            model_sha256=request.model_sha256,
            ir_sha256=request.ir_sha256,
            status=WorkerStatus.FAILED,
            termination=T.CRASHED,
            failure=WorkerFailure(
                code=WorkerFailureCode.UNAVAILABLE, message="no solver in this test"
            ),
        )


class TestExistingPipelineCompatibility:
    @pytest.mark.parametrize(
        "builder", [example_product_mix, example_multi_resource, example_min_cost]
    )
    def test_orchestrator_accepts_the_compiled_spec_up_to_the_solver_boundary(
        self, builder: Any
    ) -> None:
        c = compile_problem(validate_planning_dict(builder()))
        stub = StubUnavailable()
        out = LPOrchestrator(stub).solve_json(c.spec_json, job_id="pp-1")
        # ingress, intake, Encoding A and lowering accepted; the only rejection is the stub worker
        assert out.rejection is not None and out.rejection.stage == "worker"
        assert out.model_sha256 is not None and len(stub.requests) == 1
        req = stub.requests[0]
        assert req.problem.num_variables == len(c.manifest.variables)
        assert req.problem.num_rows == len(c.manifest.constraints)
        assert out.result is not None and out.result.outcome == MathOutcome.FAILED  # never "solved"

    def test_lowering_verifies_independently(self) -> None:
        _, m = canon(example_multi_resource())
        ir, rep = lower_ranged_lp(m)
        assert verify_lowering(m, ir, rep).valid

    def test_compiled_json_goes_through_strict_ingress(self) -> None:
        c = compile_problem(validate_planning_dict(example_product_mix()))
        spec = c.to_spec()
        assert spec.mathematically_verified is False
        assert spec.problem_type == "linear_programming"
        assert all(v.domain == "continuous" for v in spec.decision_variables)
        assert spec.objectives[0].direction == "maximize"


# ---------------------------------------------- report (synthetic DTOs, not a solve)


def evidence(m: CanonicalRangedLP) -> tuple[LoweringEvidence, VerificationClaim]:
    ir, rep = lower_ranged_lp(m)
    chk = verify_lowering(m, ir, rep)
    low = LoweringEvidence(
        model_sha256=m.model_sha256,
        ir_sha256=ir.ir_sha256,
        compiler_version=rep.compiler_version,
        verifier_version="test",
        num_variables=m.num_variables,
        num_rows=m.num_rows,
        rounding=RoundingSummary(
            coefficients_total=chk.values_total,
            coefficients_rounded=chk.values_rounded,
            subnormal_count=chk.subnormal_count,
            max_abs_rounding_error=str(chk.max_abs_rounding_error),
        ),
    )
    claim = VerificationClaim(
        claim_kind=K.MODEL_LOWERING_VALIDATED,
        status=S.VERIFIED,
        level=L.EXACT_RATIONAL,
        exactness_scope=CANONICALIZED_INPUT_VALUES,
    )
    return low, claim


def solved_dto(
    m: CanonicalRangedLP, x: tuple[float, ...], ids: tuple[str, ...], obj: float
) -> SolverRuntimeResult:
    from qorvexa.contracts.solver_runtime import GapEvidence

    low, lclaim = evidence(m)
    return SolverRuntimeResult(
        outcome=MathOutcome.SOLVED,
        solver_termination=SolverTermination.OPTIMAL,
        optimality_tier=OptimalityTier.GAP_NUMERICAL,
        claims=(
            lclaim,
            VerificationClaim(
                claim_kind=K.PRIMAL_FEASIBILITY,
                status=S.VERIFIED,
                level=L.EXACT_RATIONAL,
                exactness_scope=CANONICALIZED_INPUT_VALUES,
                residual=0.0,
            ),
            VerificationClaim(
                claim_kind=K.DUAL_GAP, status=S.VERIFIED, level=L.NUMERICAL, residual=0.0
            ),
        ),
        objective_value=obj,
        primal_solution=dict(zip(ids, x, strict=True)),
        lowering_evidence=low,
        gap_evidence=GapEvidence(
            model_sha256=m.model_sha256,
            policy_id="p",
            primal_objective=obj,
            dual_objective=obj,
            absolute_gap=0.0,
            relative_gap=0.0,
            absolute_tolerance=1e-6,
            relative_tolerance=1e-6,
        ),
    )


class TestReport:
    def setup_method(self) -> None:
        self.c, self.m = canon(example_product_mix())
        self.ids = self.m.variable_ids

    def test_verified_solution_report(self) -> None:
        r = solved_dto(self.m, (20.0, 60.0), self.ids, 2600.0)
        rep = build_production_report(self.c.manifest, r, expected_model_sha256=self.m.model_sha256)
        assert rep.status == ReportStatus.VERIFIED_NUMERICAL_SOLUTION
        assert [(q.product_id, q.quantity) for q in rep.quantities] == [
            ("A", Decimal(20)),
            ("B", Decimal(60)),
        ]
        assert rep.objective_value == 2600 and rep.objective_matches_runtime is True
        util = {u.resource_id: u for u in rep.utilization}
        assert (
            util["MACHINE"].used == 100
            and util["MACHINE"].remaining == 0
            and util["MACHINE"].at_capacity
        )
        assert util["LABOR"].used == 80 and util["LABOR"].utilization == 1
        assert rep.bottleneck_resource_ids == ("LABOR", "MACHINE")
        dem = {d.product_id: d for d in rep.demand}
        assert dem["A"].meets_minimum and not dem["A"].at_maximum
        assert "not an exact optimality proof" in rep.epistemic_note

    def test_slack_resource_is_not_a_bottleneck(self) -> None:
        r = solved_dto(self.m, (10.0, 10.0), self.ids, 700.0)  # feasible, not optimal; slack
        rep = build_production_report(self.c.manifest, r, expected_model_sha256=self.m.model_sha256)
        assert rep.bottleneck_resource_ids == ()
        assert {u.resource_id: u.remaining for u in rep.utilization} == {"LABOR": 60, "MACHINE": 70}

    def test_runtime_objective_mismatch_is_flagged(self) -> None:
        r = solved_dto(self.m, (20.0, 60.0), self.ids, 9999.0)
        rep = build_production_report(self.c.manifest, r, expected_model_sha256=self.m.model_sha256)
        assert rep.objective_value == 2600 and rep.objective_matches_runtime is False

    def test_gates_refuse_unverified_results(self) -> None:
        good = solved_dto(self.m, (20.0, 60.0), self.ids, 2600.0)
        man, h = self.c.manifest, self.m.model_sha256
        none = ReportStatus.NO_VERIFIED_RECOMMENDATION
        assert build_production_report(man, good, expected_model_sha256="f" * 64).status == none
        bad_ids = solved_dto(self.m, (20.0, 60.0), ("VAR-0001", "VAR-0009"), 2600.0)
        assert build_production_report(man, bad_ids, expected_model_sha256=h).status == none
        # (SOLVED without lowering evidence / primal claim cannot even be constructed: contract)
        unknown = SolverRuntimeResult(
            outcome=MathOutcome.UNKNOWN,
            solver_termination=SolverTermination.OPTIMAL,
            lowering_evidence=good.lowering_evidence,
            claims=(good.claims[0],),
            primal_solution=good.primal_solution,
        )
        rep = build_production_report(man, unknown, expected_model_sha256=h)
        assert rep.status == none and rep.quantities == ()  # raw candidate never promoted

    def test_verified_infeasible_report(self) -> None:
        raw = example_min_cost()
        raw["resources"][0]["capacity"] = "15"
        c, m = canon(raw)
        verdict = find_interval_conflict(m)
        assert verdict is not None and verdict.certificate_value is not None
        low, lclaim = evidence(m)
        res = SolverRuntimeResult(
            outcome=MathOutcome.INFEASIBLE,
            solver_termination=SolverTermination.INFEASIBLE,
            claims=(
                lclaim,
                VerificationClaim(
                    claim_kind=K.INFEASIBILITY,
                    status=S.VERIFIED,
                    level=L.EXACT_RATIONAL,
                    exactness_scope=CANONICALIZED_INPUT_VALUES,
                ),
            ),
            lowering_evidence=low,
            infeasibility_evidence=FarkasEvidence(
                model_sha256=m.model_sha256,
                multipliers={
                    rid: str(y) for rid, y in zip(m.row_ids, verdict.multipliers, strict=True)
                },
                certificate_value=str(verdict.certificate_value),
            ),
        )
        rep = build_production_report(c.manifest, res, expected_model_sha256=m.model_sha256)
        assert rep.status == ReportStatus.INFEASIBLE_VERIFIED and rep.quantities == ()

"""Industrial units engine: exact algebra, conversion, parsing safety, dimensional validation."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest

from qorvexa.industrial.production_planning import validate_planning_dict
from qorvexa.industrial.units import (
    BoundUnits,
    Compat,
    ConstraintUnits,
    ObjectiveUnits,
    PlanConsumption,
    PlanningUnitsInput,
    PlanProduct,
    PlanResource,
    Quantity,
    Term,
    UnitError,
    UnitRegistry,
    UnitStatus,
    classify,
    parse_unit,
    planning_units_from_problem,
    validate_linear_units,
    validate_planning_units,
)
from tests.test_production_planning import example_product_mix


def code(fn) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(UnitError) as e:
        fn()
    return e.value.code


class TestParsingAndRegistry:
    def test_aliases_resolve_to_canonical_symbols(self) -> None:
        assert parse_unit("pcs").symbol == "piece"
        assert parse_unit("hours").symbol == "h"
        assert parse_unit("kg/piece").symbol == "kg/piece"
        assert parse_unit("1/h").symbol == "1/h"

    def test_dimensions_compose(self) -> None:
        j = parse_unit("kg*m^2/s^2")
        assert j.dimension == parse_unit("J").dimension
        assert parse_unit("kW*h").dimension == parse_unit("kWh").dimension
        assert parse_unit("kW*h").scale == parse_unit("kWh").scale == 3_600_000
        assert parse_unit("m^3").dimension == parse_unit("L").dimension
        assert parse_unit("kg/kg").dimension.is_dimensionless

    def test_division_is_left_associative_and_parentheses_work(self) -> None:
        assert parse_unit("kg/piece/h").dimension == parse_unit("kg/(piece*h)").dimension
        assert parse_unit("(kg/h)^2").dimension == parse_unit("kg^2/h^2").dimension
        assert parse_unit("kg**2").symbol == "kg^2"

    def test_unknown_ambiguous_and_offset_units_are_refused(self) -> None:
        assert code(lambda: parse_unit("furlong")) == "UNKNOWN_UNIT"
        assert code(lambda: parse_unit("ton")) == "AMBIGUOUS_UNIT"
        assert code(lambda: parse_unit("gal")) == "AMBIGUOUS_UNIT"
        assert code(lambda: parse_unit("degC")) == "OFFSET_UNIT_UNSUPPORTED"
        assert code(lambda: parse_unit("kg*degF")) == "OFFSET_UNIT_UNSUPPORTED"
        assert code(lambda: parse_unit("XYZ")) == "UNKNOWN_UNIT"
        assert code(lambda: parse_unit("cent")) == "UNKNOWN_UNIT"

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "   ",
            "kg*",
            "*kg",
            "kg//h",
            "(kg",
            "kg)",
            "kg^",
            "kg^x",
            "kg^1.5",
            "2*kg",
            "kg 3",
            "__import__('os')",
            "kg;rm",
            "kg+g",
            "kg-g",
            "m^-",
            "kg h",
            "\x00",
            "1e3 kg",
        ],
    )
    def test_malformed_and_malicious_expressions_fail_closed(self, bad: str) -> None:
        assert code(lambda: parse_unit(bad)) in {"PARSE_ERROR", "UNKNOWN_UNIT"}

    def test_size_limits(self) -> None:
        assert code(lambda: parse_unit("kg*" * 100 + "g")) in {
            "PARSE_ERROR",
            "EXPRESSION_TOO_LARGE",
        }
        assert code(lambda: parse_unit("(" * 20 + "kg" + ")" * 20)) == "EXPRESSION_TOO_LARGE"
        assert code(lambda: parse_unit("kg^13")) == "EXPONENT_OUT_OF_RANGE"
        assert code(lambda: parse_unit("kg^-13")) == "EXPONENT_OUT_OF_RANGE"
        assert code(lambda: parse_unit("tonne^12*tonne^12")) == "EXPONENT_OUT_OF_RANGE"
        assert code(lambda: parse_unit("k" * 300)) == "PARSE_ERROR"
        assert code(lambda: parse_unit(5)) == "PARSE_ERROR"  # type: ignore[arg-type]

    def test_scale_overflow_is_detected(self) -> None:
        def chain() -> None:
            reg = UnitRegistry.default()
            for i in range(120):  # each step multiplies the exact scale by 1e30
                reg = reg.extended(f"u{i}", "kg" if i == 0 else f"u{i - 1}", "1" + "0" * 30)

        assert code(chain) == "OVERFLOW"
        reg = UnitRegistry.default().extended("big", "kg", "1" + "0" * 30)
        assert code(lambda: parse_unit("big^12*big^12", reg)) == "EXPONENT_OUT_OF_RANGE"
        assert (
            code(lambda: parse_unit("big^12*big^-12*big^12*big^12", reg)) == "EXPONENT_OUT_OF_RANGE"
        )

    def test_custom_units_are_explicit_and_exact(self) -> None:
        reg = UnitRegistry.default().extended("pallet", "piece", "24")
        q = Quantity.of(2, "pallet", reg).to("piece", reg)
        assert q.result.value == 48
        assert code(lambda: parse_unit("pallet")) == "UNKNOWN_UNIT"  # default registry untouched
        assert code(lambda: UnitRegistry.default().extended("kg", "g", "1")) == "DUPLICATE_UNIT"
        assert (
            code(lambda: UnitRegistry.default().extended("x", "kg", "0")) == "INVALID_CUSTOM_UNIT"
        )
        assert (
            code(lambda: UnitRegistry.default().extended("x", "kg", "abc")) == "INVALID_CUSTOM_UNIT"
        )
        assert (
            code(lambda: UnitRegistry.default().extended("usd2", "USD", "2"))
            == "INVALID_CUSTOM_UNIT"
        )
        assert (
            code(lambda: UnitRegistry.default().extended("a b", "kg", "2")) == "INVALID_CUSTOM_UNIT"
        )
        assert code(lambda: UnitRegistry.default().extended("t", "kg", "1000")) == "DUPLICATE_UNIT"


class TestQuantity:
    def test_exact_arithmetic_no_float_error(self) -> None:
        a = Quantity.of("0.1", "kg")
        b = Quantity.of("0.2", "kg")
        assert a.add(b).value == Fraction(3, 10)
        assert a.add(b).compare(Quantity.of("0.3", "kg")) == 0
        assert Quantity.of(Decimal("1.1"), "m").mul(Quantity.of(3, "m")).value == Fraction(33, 10)

    def test_floats_bools_and_garbage_are_refused(self) -> None:
        assert code(lambda: Quantity.of(0.1, "kg")) == "FLOAT_REFUSED"
        assert code(lambda: Quantity.of(True, "kg")) == "FLOAT_REFUSED"
        assert code(lambda: Quantity.of("nan", "kg")) == "INVALID_NUMBER"
        assert code(lambda: Quantity.of("inf", "kg")) == "INVALID_NUMBER"
        assert code(lambda: Quantity.of("1,5", "kg")) == "INVALID_NUMBER"
        assert code(lambda: Quantity.of(None, "kg")) == "INVALID_NUMBER"
        assert code(lambda: Quantity.of("1" * 41, "kg")) == "INVALID_NUMBER"
        assert code(lambda: Quantity.of("1e31", "kg")) == "OVERFLOW"
        assert code(lambda: Quantity.of(10**31, "kg")) == "OVERFLOW"

    def test_example_2kg_per_piece_times_10_pieces(self) -> None:
        coef = Quantity.of(2, "kg/piece")
        out = coef.mul(Quantity.of(10, "piece"))
        assert out.value == 20 and out.unit.dimension == parse_unit("kg").dimension
        assert classify(out.unit, parse_unit("kg")) is Compat.SAME_UNIT
        assert str(out.to("kg").result) == "20 kg"

    def test_invalid_2kg_per_piece_equals_20_hours(self) -> None:
        out = Quantity.of(2, "kg/piece").mul(Quantity.of(10, "piece"))
        assert code(lambda: out.to("h")) == "DIMENSION_MISMATCH"
        assert code(lambda: out.compare(Quantity.of(20, "h"))) == "DIMENSION_MISMATCH"
        assert code(lambda: out.add(Quantity.of(20, "h"))) == "DIMENSION_MISMATCH"

    def test_conversion_is_exact_and_traceable(self) -> None:
        r = Quantity.of("2.5", "tonne").to("kg")
        assert r.result.value == 2500 and r.factor == 1000
        assert r.rule == "EXACT_SCALE_RATIO" and len(r.trace) == 3
        assert Quantity.of(1, "lb").to("g").result.value == Fraction(45359237, 100000)
        assert Quantity.of(1, "kWh").to("MJ").result.value == Fraction(18, 5)
        assert Quantity.of(90, "min").to("h").result.value == Fraction(3, 2)
        assert Quantity.of(1, "in").to("mm").result.value == Fraction(127, 5)
        back = Quantity.of(1, "ft").to("mm").result.to("ft").result
        assert back.value == 1  # round trip exact

    def test_conversion_precision_of_rates(self) -> None:
        r = Quantity.of(5, "tonne/h").to("kg/s")
        assert r.result.value == Fraction(5000, 3600)
        assert r.result.to("tonne/h").result.value == 5

    def test_no_implicit_conversion_in_add_sub(self) -> None:
        assert (
            code(lambda: Quantity.of(1, "tonne").add(Quantity.of(1, "kg"))) == "CONVERSION_REQUIRED"
        )
        assert code(lambda: Quantity.of(1, "h").sub(Quantity.of(1, "min"))) == "CONVERSION_REQUIRED"
        assert Quantity.of(1, "tonne").compare(Quantity.of(1000, "kg")) == 0  # exact compare ok
        assert Quantity.of(1, "tonne").compare(Quantity.of(999, "kg")) == 1

    def test_currencies_never_interconvert(self) -> None:
        assert code(lambda: Quantity.of(10, "USD").to("EUR")) == "CURRENCY_MISMATCH"
        assert (
            code(lambda: Quantity.of(10, "USD").add(Quantity.of(10, "EUR"))) == "CURRENCY_MISMATCH"
        )
        assert code(lambda: Quantity.of(10, "USD/piece").to("EUR/piece")) == "CURRENCY_MISMATCH"
        assert (
            classify(parse_unit("USD/piece"), parse_unit("EGP/piece")) is Compat.CURRENCY_MISMATCH
        )
        assert classify(parse_unit("USD"), parse_unit("kg")) is Compat.DIMENSION_MISMATCH
        assert (
            Quantity.of(3, "USD").mul(Quantity.of(2, "piece")).unit.dimension
            != parse_unit("EUR").dimension
        )

    def test_division_by_zero_and_overflow(self) -> None:
        assert code(lambda: Quantity.of(1, "kg").div(Quantity.of(0, "h"))) == "DIVISION_BY_ZERO"
        big = Quantity.of(10**30, "kg")
        assert code(lambda: big.mul(big).mul(big)) == "OVERFLOW"

    def test_division_cancels_units(self) -> None:
        r = Quantity.of(6, "kg").div(Quantity.of(3, "kg"))
        assert r.unit.dimension.is_dimensionless and r.value == 2
        r2 = Quantity.of(1, "tonne").div(Quantity.of(1, "kg"))
        assert r2.unit.dimension.is_dimensionless and r2.value * r2.unit.scale == 1000

    def test_results_are_deterministic(self) -> None:
        a = [
            str(Quantity.of("1.5", "kW").mul(Quantity.of(2, "h")).to("kWh").result)
            for _ in range(3)
        ]
        assert a == ["3 kWh"] * 3


class TestLinearValidation:
    def test_valid_constraint_and_objective(self) -> None:
        r = validate_linear_units(
            {"x": "piece", "y": "piece"},
            [ConstraintUnits("C1", (Term("x", "kg/piece"), Term("y", "kg/piece")), "kg")],
            ObjectiveUnits((Term("x", "EUR/piece"), Term("y", "EUR/piece")), "EUR"),
        )
        assert r.status is UnitStatus.CONSISTENT and r.issues == ()

    def test_dimension_mismatch_detected(self) -> None:
        r = validate_linear_units(
            {"x": "piece"},
            [ConstraintUnits("C1", (Term("x", "kg/piece"),), "h")],
            ObjectiveUnits((Term("x", "EUR/piece"),), "EUR"),
        )
        assert r.status is UnitStatus.INVALID
        assert [i.code for i in r.issues] == ["DIMENSION_MISMATCH"]

    def test_convertible_scale_is_reported_with_exact_factor_not_applied(self) -> None:
        r = validate_linear_units(
            {"x": "piece"},
            [ConstraintUnits("C1", (Term("x", "min/piece"),), "h")],
            ObjectiveUnits((Term("x", "EUR/piece"),), "EUR"),
        )
        assert r.status is UnitStatus.NEEDS_EXPLICIT_CONVERSION
        assert r.issues[0].code == "CONVERSION_REQUIRED" and r.issues[0].factor == Fraction(1, 60)

    def test_tonne_rhs_with_kg_coefficients(self) -> None:
        r = validate_linear_units(
            {"x": "piece"},
            [ConstraintUnits("C1", (Term("x", "kg/piece"),), "tonne")],
            ObjectiveUnits((Term("x", "EUR/piece"),), "EUR"),
        )
        assert r.issues[0].factor == Fraction(1, 1000)

    def test_objective_currency_mismatch_and_mixed_terms(self) -> None:
        r = validate_linear_units(
            {"x": "piece", "y": "piece"},
            [],
            ObjectiveUnits((Term("x", "USD/piece"), Term("y", "EUR/piece")), "EUR"),
        )
        assert r.status is UnitStatus.INVALID
        assert [i.code for i in r.issues] == ["CURRENCY_MISMATCH"]
        assert r.issues[0].location == "objective/term:x"

    def test_bounds_unknown_variables_and_bad_units(self) -> None:
        r = validate_linear_units(
            {"x": "piece"},
            [ConstraintUnits("C1", (Term("z", "kg/piece"), Term("x", "furlong/piece")), "kg")],
            ObjectiveUnits((Term("x", "EUR/piece"),), "EUR"),
            [BoundUnits("x.max", "x", "kg"), BoundUnits("q.max", "q", "piece")],
        )
        codes = sorted(i.code for i in r.issues)
        assert codes == [
            "DIMENSION_MISMATCH",
            "UNKNOWN_UNIT",
            "UNKNOWN_VARIABLE",
            "UNKNOWN_VARIABLE",
        ]

    def test_report_is_deterministic(self) -> None:
        def run():  # type: ignore[no-untyped-def]
            return validate_linear_units(
                {"x": "piece"},
                [ConstraintUnits("C1", (Term("x", "min/piece"),), "h")],
                ObjectiveUnits((Term("x", "USD/piece"),), "EUR"),
            )

        assert run().sha256 == run().sha256 and run() == run()


def good_input() -> PlanningUnitsInput:
    return PlanningUnitsInput(
        "EUR",
        (
            PlanProduct("A", "piece", "EUR/piece", "piece"),
            PlanProduct("B", "kg", "EUR/kg"),
        ),
        (PlanResource("M", "h"), PlanResource("RM", "kg")),
        (
            PlanConsumption("M", "A", "h/piece"),
            PlanConsumption("M", "B", "h/kg"),
            PlanConsumption("RM", "A", "kg/piece"),
            PlanConsumption("RM", "B", "kg/kg"),
        ),
    )


class TestPlanning:
    def test_valid_plan_is_consistent(self) -> None:
        assert validate_planning_units(good_input()).status is UnitStatus.CONSISTENT

    def test_wrong_consumption_dimension(self) -> None:
        inp = good_input()
        bad = PlanningUnitsInput(
            inp.currency,
            inp.products,
            inp.resources,
            (PlanConsumption("M", "A", "kg/piece"), *inp.consumptions[1:]),
        )
        r = validate_planning_units(bad)
        assert r.status is UnitStatus.INVALID and r.issues[0].code == "DIMENSION_MISMATCH"
        assert "constraint:M" in r.issues[0].location

    def test_demand_bound_unit_must_match_product_unit(self) -> None:
        inp = PlanningUnitsInput(
            "EUR",
            (PlanProduct("A", "piece", "EUR/piece", "kg"),),
            (PlanResource("M", "h"),),
            (PlanConsumption("M", "A", "h/piece"),),
        )
        r = validate_planning_units(inp)
        assert {i.code for i in r.issues} == {"DIMENSION_MISMATCH"}
        assert all(i.location.startswith("bound:A.") for i in r.issues)

    def test_bound_in_dozens_needs_explicit_conversion(self) -> None:
        inp = PlanningUnitsInput(
            "EUR",
            (PlanProduct("A", "piece", "EUR/piece", "dozen"),),
            (PlanResource("M", "h"),),
            (PlanConsumption("M", "A", "h/piece"),),
        )
        r = validate_planning_units(inp)
        assert r.status is UnitStatus.NEEDS_EXPLICIT_CONVERSION
        assert {i.factor for i in r.issues} == {Fraction(12)}

    def test_mixed_currency_objective_rejected(self) -> None:
        inp = PlanningUnitsInput(
            "EUR",
            (PlanProduct("A", "piece", "USD/piece"), PlanProduct("B", "piece", "EUR/piece")),
            (PlanResource("M", "h"),),
            (PlanConsumption("M", "A", "h/piece"),),
        )
        r = validate_planning_units(inp)
        assert [i.code for i in r.issues if i.code != "CONVERSION_REQUIRED"] == [
            "CURRENCY_MISMATCH"
        ]

    def test_currency_must_be_a_currency(self) -> None:
        inp = good_input()
        for cur, want in (
            ("kg", "CURRENCY_INVALID"),
            ("EUR*USD", "CURRENCY_INVALID"),
            ("pesos", "UNKNOWN_UNIT"),
        ):
            r = validate_planning_units(
                PlanningUnitsInput(cur, inp.products, inp.resources, inp.consumptions)
            )
            assert want in {i.code for i in r.issues}, cur

    def test_unknown_references_and_duplicates(self) -> None:
        inp = good_input()
        r = validate_planning_units(
            PlanningUnitsInput(
                "EUR",
                (*inp.products, PlanProduct("A", "piece", "EUR/piece")),
                inp.resources,
                (
                    *inp.consumptions,
                    PlanConsumption("ZZ", "A", "h/piece"),
                    PlanConsumption("M", "Q", "h/piece"),
                ),
            )
        )
        assert {"DUPLICATE_ID", "UNKNOWN_REFERENCE"} <= {i.code for i in r.issues}

    def test_existing_production_planning_problem_adapts_read_only(self) -> None:
        problem = validate_planning_dict(example_product_mix())
        before = problem.model_dump_json()
        r = validate_planning_units(planning_units_from_problem(problem))
        assert problem.model_dump_json() == before
        assert r.status in (UnitStatus.CONSISTENT, UnitStatus.INVALID)
        if r.status is UnitStatus.INVALID:  # only unknown legacy unit strings may fail here
            assert {i.code for i in r.issues} <= {"UNKNOWN_UNIT", "AMBIGUOUS_UNIT"}

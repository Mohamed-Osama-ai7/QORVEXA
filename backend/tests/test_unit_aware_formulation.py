"""Unit-aware formulation: claims, explicit conversions, custom units, solver gate, evidence."""

from __future__ import annotations

import itertools
import math
from decimal import Decimal
from pathlib import Path

import pytest

from qorvexa.industrial.decision_intelligence import DecisionConfig, ScenarioRun, decide, solve_run
from qorvexa.industrial.decision_intelligence.trust import admit
from qorvexa.industrial.formulation import (
    Actor,
    ActorKind,
    Entry,
    FormulationError,
    FormulationStatus,
    Ledger,
    Op,
    analyze,
    convert_units,
    formulate,
    propose_from_draft,
)
from qorvexa.industrial.formulation.ledger import entry_digest
from qorvexa.industrial.governance import GovernedSessions, Principal, Role, SqliteStore
from qorvexa.industrial.production_planning import (
    CapacityChange,
    ScenarioSpec,
    compile_problem,
    compile_scenario,
)
from qorvexa.industrial.production_planning.admission import AdmissionPolicy
from qorvexa.industrial.production_planning.model import PlanningInputError
from qorvexa.industrial.production_planning.unit_gate import compile_unit_gated
from qorvexa.solver_fabric import LPOrchestrator
from qorvexa.solver_fabric.highs_adapter import HighsAdapter
from tests.test_formulation import FACTS, H, stated

LLM = Actor(id="extractor-1", kind=ActorKind.LLM)
ANA = Principal("ana", ActorKind.HUMAN, frozenset({Role.PLANNER}))
NOTE = "approved after checking the work instruction"


def strict(machine_unit: str = "h/piece", a: str = "2", b: str = "1") -> dict[str, str]:
    f = dict(FACTS)
    f["consumption:MACHINE:A"], f["consumption:MACHINE:B"] = a, b
    f["product:A:value_unit"] = f["product:B:value_unit"] = "EUR/piece"
    f["product:A:bound_unit"] = "piece"
    f["consumption:MACHINE:A:unit"] = f["consumption:MACHINE:B:unit"] = machine_unit
    f["consumption:LABOR:A:unit"] = f["consumption:LABOR:B:unit"] = "h/piece"
    return f


def codes(led: Ledger) -> set[str]:
    return {i.code for i in analyze(led).issues}


class SpyOrchestrator:
    def solve_json(self, *a: object, **k: object) -> object:
        raise AssertionError("the solver must never be reached")


class TestUnitClaims:
    def test_complete_unit_metadata_validates_and_is_recorded(self) -> None:
        r = formulate(stated(strict()))
        assert r.state.status is FormulationStatus.READY and r.compiled is not None
        assert r.record is not None and r.unit_evidence is not None
        assert r.state.units_status == r.record.units_status == "DIMENSIONALLY_VALIDATED"
        assert r.record.unit_report_sha256 == r.unit_evidence.report_sha256 != ""
        assert f"unit_report:{r.record.unit_report_sha256}" in r.compiled.manifest.lineage
        keys = {c[0] for c in r.record.unit_claims}
        assert {"product:A:value_unit", "consumption:MACHINE:A:unit", "resource:LABOR:unit"} <= keys
        assert all(c[2] == "USER_STATEMENT" for c in r.record.unit_claims)

    def test_legacy_models_are_explicitly_unverified_never_validated(self) -> None:
        r = formulate(stated())
        assert r.state.status is FormulationStatus.READY and r.unit_evidence is None
        assert r.state.units_status == r.record.units_status == "LEGACY_UNITS_UNVERIFIED"  # type: ignore[union-attr]
        assert r.state.unit_report_sha256 == ""

    def test_missing_unit_metadata_is_never_assumed(self) -> None:
        f = strict()
        del f["consumption:LABOR:B:unit"], f["product:B:value_unit"]
        r = formulate(stated(f))
        assert r.problem is None and r.state.status is FormulationStatus.NEEDS_CLARIFICATION
        missing = {i.key: i for i in r.state.issues if i.code == "MISSING_VALUE"}
        assert {"consumption:LABOR:B:unit", "product:B:value_unit"} <= set(missing)
        assert not any(i.can_assume for i in missing.values())
        led = stated(f)
        with pytest.raises(FormulationError):
            led.assume("consumption:LABOR:B:unit", "h/piece", H, "closed world assumption")

    def test_bound_unit_required_only_for_nontrivial_bounds(self) -> None:
        f = strict()
        del f["product:A:bound_unit"]
        assert "product:A:bound_unit" in {i.key for i in formulate(stated(f)).state.issues}
        f2 = strict()
        f2["product:A:max_quantity"] = "NONE"
        del f2["product:A:bound_unit"]
        assert formulate(stated(f2)).state.status is FormulationStatus.READY

    def test_dimension_mismatch_is_rejected(self) -> None:
        r = formulate(stated(strict(machine_unit="kg/piece")))
        assert r.problem is None and r.state.status is FormulationStatus.INVALID_MODEL
        assert "DIMENSION_MISMATCH" in {i.code for i in r.state.issues}
        r2 = formulate(stated(dict(strict(), **{"product:A:bound_unit": "kg"})))
        assert r2.problem is None and "DIMENSION_MISMATCH" in {i.code for i in r2.state.issues}

    def test_currency_mismatch_is_rejected(self) -> None:
        r = formulate(stated(dict(strict(), **{"product:A:value_unit": "USD/piece"})))
        assert r.problem is None and "CURRENCY_MISMATCH" in {i.code for i in r.state.issues}

    @pytest.mark.parametrize(
        ("unit", "code"),
        [
            ("furlong/piece", "UNKNOWN_UNIT"),
            ("ton/piece", "AMBIGUOUS_UNIT"),
            ("degC/piece", "OFFSET_UNIT_UNSUPPORTED"),
            ("h/", "PARSE_ERROR"),
        ],
    )
    def test_unknown_ambiguous_and_offset_units_block(self, unit: str, code: str) -> None:
        r = formulate(stated(strict(machine_unit=unit)))
        assert r.problem is None and r.state.status is FormulationStatus.NEEDS_CLARIFICATION
        assert code in {i.code for i in r.state.issues}

    def test_llm_proposed_units_are_untrusted_until_a_human_confirms(self) -> None:
        f = strict()
        led = stated({k: v for k, v in f.items() if k != "product:A:value_unit"})
        led = propose_from_draft(
            led, {"products": [{"id": "A", "value_unit": "EUR/piece"}]}, LLM, source_ref="chat"
        )
        r = formulate(led)
        assert r.problem is None
        assert "UNCONFIRMED_PROPOSAL" in {i.code for i in r.state.issues}
        from qorvexa.industrial.formulation import confirm_keys

        assert formulate(confirm_keys(led, ["product:A:value_unit"], H)).problem is not None


class TestExplicitConversion:
    def led(self) -> Ledger:
        return stated(strict("min/piece", "120", "60"))

    def test_scale_mismatch_blocks_and_shows_exact_factor_and_candidates(self) -> None:
        r = formulate(self.led())
        assert r.problem is None and r.compiled is None
        assert r.state.status is FormulationStatus.NEEDS_CLARIFICATION
        i = next(x for x in r.state.issues if x.code == "CONVERSION_REQUIRED")
        assert "1/60" in i.message and "Nothing is converted" in i.message
        assert any(
            c.startswith("consumption:MACHINE:") and "|1/60|exact" in c for c in i.candidates
        )
        assert "resource:MACHINE|min|60/1|exact" in i.candidates

    def test_unapproved_conversion_never_builds(self) -> None:
        led = self.led()
        for _ in range(2):  # repeated analysis changes nothing
            assert formulate(led).problem is None

    def test_resource_conversion_rebuilds_exactly(self) -> None:
        led = convert_units(
            self.led(), "resource:MACHINE", "min", "ana", NOTE, "2026-02-01T10:00:00Z"
        )
        r = formulate(led)
        assert r.state.status is FormulationStatus.READY and r.problem is not None
        res = {x.id: x for x in r.problem.resources}["MACHINE"]
        assert (res.capacity, res.unit) == (Decimal(6000), "min")
        assert r.record is not None and len(r.record.conversions) == 1
        slot, src, dst, factor, actor, why, at = r.record.conversions[0]
        assert (slot, src, dst, factor, actor, at) == (
            "resource:MACHINE",
            "h",
            "min",
            "60/1",
            "ana",
            "2026-02-01T10:00:00Z",
        )
        assert why == NOTE

    def test_consumption_conversion_is_exact_and_deterministic(self) -> None:
        led = self.led()
        for p in ("A", "B"):
            led = convert_units(led, f"consumption:MACHINE:{p}", "(h)/(piece)", "ana", NOTE, "t1")
        r = formulate(led)
        assert r.problem is not None
        amounts = {
            c.product_id: c.amount_per_unit
            for c in r.problem.consumptions
            if c.resource_id == "MACHINE"
        }
        assert amounts == {"A": Decimal(2), "B": Decimal(1)}
        assert formulate(led).state.state_sha256 == r.state.state_sha256
        assert formulate(led).record.record_sha256 == r.record.record_sha256  # type: ignore[union-attr]

    def test_inexact_decimal_conversion_is_refused(self) -> None:
        led = stated(strict("min/piece", "7", "60"))
        with pytest.raises(FormulationError) as e:
            convert_units(led, "consumption:MACHINE:A", "(h)/(piece)", "ana", NOTE, "t")
        assert e.value.code == "CONVERSION_NOT_EXACT"

    def test_conversion_input_validation(self) -> None:
        led = self.led()
        bad = [
            ("resource:NOPE", "min", NOTE, "t", "CONVERSION_SLOT_UNKNOWN"),
            ("resource:MACHINE", "h", NOTE, "t", "CONVERSION_NOT_NEEDED"),
            ("resource:MACHINE", "kg", NOTE, "t", "DIMENSION_MISMATCH"),
            ("resource:MACHINE", "furlong", NOTE, "t", "UNKNOWN_UNIT"),
            ("resource:MACHINE", "min", "short", "t", "CONVERSION_INVALID"),
            ("resource:MACHINE", "min", NOTE, "", "CONVERSION_INVALID"),
        ]
        for slot, to, why, at, code in bad:
            with pytest.raises(FormulationError) as e:
                convert_units(led, slot, to, "ana", why, at)
            assert e.value.code == code, (slot, to)
        with pytest.raises(FormulationError):
            convert_units(led, "resource:MACHINE", "min", " ", NOTE, "t")

    def test_currency_conversion_is_never_possible(self) -> None:
        led = stated(strict())
        with pytest.raises(FormulationError) as e:
            convert_units(led, "value:A", "USD/piece", "ana", NOTE, "t")
        assert e.value.code in {"CURRENCY_MISMATCH", "DIMENSION_MISMATCH"}

    def test_bound_conversion_from_dozen(self) -> None:
        f = strict()
        f["product:A:bound_unit"], f["product:A:max_quantity"] = "dozen", "4"
        led = stated(f)
        r0 = formulate(led)
        assert r0.problem is None and "CONVERSION_REQUIRED" in {i.code for i in r0.state.issues}
        r = formulate(convert_units(led, "bound:A", "piece", "ana", NOTE, "t"))
        assert r.problem is not None
        assert {p.id: p.max_quantity for p in r.problem.products}["A"] == Decimal(48)

    def test_retracting_a_conversion_unapproves_it(self) -> None:
        led = convert_units(self.led(), "resource:MACHINE", "min", "ana", NOTE, "t")
        assert formulate(led).problem is not None
        undone = led.retract(len(led.entries), H)
        r = formulate(undone)
        assert r.problem is None and "CONVERSION_REQUIRED" in {i.code for i in r.state.issues}

    def test_forged_factor_or_llm_conversion_or_missing_time_is_detected(self) -> None:
        led = convert_units(self.led(), "resource:MACHINE", "min", "ana", NOTE, "t")
        last = led.entries[-1]

        def forged(**upd: object) -> Ledger:
            e = last.model_copy(update=upd)
            e = e.model_copy(update={"entry_sha256": entry_digest(e)})
            return Ledger((*led.entries[:-1], e))

        for variant in (
            forged(factor="61/1"),
            forged(unit_from="min"),
            forged(actor=LLM),
            forged(at=""),
            forged(rationale="x"),
            forged(value="day"),
        ):
            with pytest.raises(FormulationError) as e:
                formulate(variant)
            assert e.value.code == "LEDGER_TAMPERED"
        edited = (*led.entries[:-1], last.model_copy(update={"factor": "1/1"}))
        with pytest.raises(FormulationError):
            formulate(Ledger(edited))

    def test_old_ledgers_keep_their_hashes(self) -> None:
        e = stated().entries[0]
        assert e.unit_from == e.factor == e.at == ""
        assert e.entry_sha256 == entry_digest(e)
        assert Op.CONVERT.value == "CONVERT"


class TestCustomUnits:
    def base(self) -> dict[str, str]:
        f = strict()
        f["product:A:bound_unit"], f["product:A:max_quantity"] = "tray", "7"
        return f

    def test_custom_unit_defined_by_human_converts_exactly(self) -> None:
        led = stated(self.base()).assert_fact("unitdef:tray", "6|piece", H)
        assert "CONVERSION_REQUIRED" in codes(led)
        r = formulate(convert_units(led, "bound:A", "piece", "ana", NOTE, "t"))
        assert r.problem is not None
        assert {p.id: p.max_quantity for p in r.problem.products}["A"] == Decimal(42)
        assert r.record is not None and r.record.custom_units == (("tray", "piece", "6/1", H),)
        assert r.unit_evidence is not None and r.unit_evidence.custom_units == (
            ("tray", "piece", "6/1"),
        )

    def test_undefined_custom_unit_is_unknown(self) -> None:
        assert "UNKNOWN_UNIT" in codes(stated(self.base()))

    def test_llm_proposed_definition_is_untrusted(self) -> None:
        led = propose_from_draft(
            stated(self.base()),
            {"unit_definitions": [{"symbol": "tray", "factor": "6", "expression": "piece"}]},
            LLM,
        )
        assert "UNCONFIRMED_PROPOSAL" in codes(led)
        assert formulate(led).problem is None

    def test_duplicate_circular_and_invalid_definitions_are_rejected(self) -> None:
        base = stated(self.base())
        cases = {
            "DUPLICATE_UNIT": [("kg", "1000|g")],
            "CIRCULAR_DEFINITION": [("aa", "2|bb"), ("bb", "3|aa")],
            "INVALID_CUSTOM_UNIT": [("cc", "2|EUR")],
            "UNKNOWN_UNIT": [("dd", "2|zork")],
            "PARSE_ERROR": [("ee", "2|piece//h")],
        }
        for code, defs in cases.items():
            led = base
            for sym, val in defs:
                led = led.assert_fact(f"unitdef:{sym}", val, H)
            assert code in codes(led), code
            assert formulate(led).problem is None
        with pytest.raises(FormulationError):
            base.assert_fact("unitdef:zz", "0|piece", H)
        with pytest.raises(FormulationError):
            base.assert_fact("unitdef:zz", "1.5", H)
        selfref = base.assert_fact("unitdef:ss", "2|ss", H)
        assert "CIRCULAR_DEFINITION" in codes(selfref)

    def test_registry_does_not_depend_on_declaration_order(self) -> None:
        a = (
            stated(self.base())
            .assert_fact("unitdef:box", "4|tray", H)
            .assert_fact("unitdef:tray", "6|piece", H)
        )
        b = (
            stated(self.base())
            .assert_fact("unitdef:tray", "6|piece", H)
            .assert_fact("unitdef:box", "4|tray", H)
        )
        ra = formulate(convert_units(a, "bound:A", "piece", "ana", NOTE, "t"))
        rb = formulate(convert_units(b, "bound:A", "piece", "ana", NOTE, "t"))
        assert ra.unit_evidence is not None and rb.unit_evidence is not None
        assert ra.unit_evidence.custom_units == rb.unit_evidence.custom_units
        assert ra.unit_evidence.report_sha256 == rb.unit_evidence.report_sha256

    def test_forged_definition_in_stored_ledger_is_detected(self) -> None:
        led = stated(self.base()).assert_fact("unitdef:tray", "6|piece", H)
        i = len(led.entries) - 1
        forged = Ledger((*led.entries[:i], led.entries[i].model_copy(update={"value": "60|piece"})))
        with pytest.raises(FormulationError) as e:
            formulate(forged)
        assert e.value.code == "LEDGER_TAMPERED"


@pytest.fixture(scope="module")
def validated_run() -> tuple[ScenarioRun, object]:
    r = formulate(stated(strict()))
    assert r.problem is not None and r.compiled is not None and r.unit_evidence is not None
    run = solve_run(
        LPOrchestrator(HighsAdapter()), "BASE", r.problem, r.compiled, units=r.unit_evidence
    )
    return run, r


class TestSolverGate:
    def test_valid_units_reach_real_highs_and_are_admitted(self, validated_run) -> None:  # type: ignore[no-untyped-def]
        run, r = validated_run
        adm = admit(run, require_units=True)
        assert not hasattr(adm, "code"), adm
        assert adm.units_status == "DIMENSIONALLY_VALIDATED"
        assert adm.unit_report_sha256 == r.record.unit_report_sha256
        assert math.isclose(float(adm.metrics["objective"]), 2600.0, abs_tol=1e-6)

    def test_modified_evidence_hash_never_reaches_the_solver(self) -> None:
        r = formulate(stated(strict()))
        bad = r.unit_evidence.model_copy(update={"report_sha256": "0" * 64})  # type: ignore[union-attr]
        with pytest.raises(PlanningInputError) as e:
            solve_run(SpyOrchestrator(), "BASE", r.problem, r.compiled, units=bad)  # type: ignore[arg-type]
        assert e.value.code == "UNIT_EVIDENCE_MISMATCH"

    def test_tampered_inputs_or_foreign_evidence_never_reach_the_solver(self) -> None:
        r = formulate(stated(strict()))
        ev = r.unit_evidence
        assert ev is not None and r.problem is not None
        inp = ev.inputs
        swapped = type(inp)(
            inp.currency,
            inp.products,
            inp.resources,
            tuple(
                type(c)(c.resource_id, c.product_id, "kg/piece") if c.resource_id == "LABOR" else c
                for c in inp.consumptions
            ),
        )
        for evidence in (ev.model_copy(update={"inputs": swapped}),):
            with pytest.raises(PlanningInputError) as e:
                solve_run(SpyOrchestrator(), "BASE", r.problem, r.compiled, units=evidence)  # type: ignore[arg-type]
            assert e.value.code in {"UNIT_VALIDATION_FAILED", "UNIT_EVIDENCE_MISMATCH"}
        other = formulate(
            stated(
                dict(
                    strict(),
                    **{
                        "resource:LABOR:unit": "min",
                        "consumption:LABOR:A:unit": "min/piece",
                        "consumption:LABOR:B:unit": "min/piece",
                    },
                )
            )
        )
        assert other.problem is not None
        with pytest.raises(PlanningInputError) as e2:  # evidence of another problem
            solve_run(SpyOrchestrator(), "BASE", r.problem, r.compiled, units=other.unit_evidence)  # type: ignore[arg-type]
        assert e2.value.code == "UNIT_EVIDENCE_MISMATCH"

    def test_failed_evidence_cannot_be_compiled(self) -> None:
        r = formulate(stated(strict()))
        ev = r.unit_evidence
        assert ev is not None and r.problem is not None
        bad = ev.model_copy(update={"custom_units": (("kg", "g", "1000"),)})
        with pytest.raises(PlanningInputError):
            compile_unit_gated(r.problem, bad)

    def test_missing_evidence_is_rejected_when_required(self) -> None:
        r = formulate(stated())
        assert r.problem is not None and r.compiled is not None
        with pytest.raises(PlanningInputError) as e:
            solve_run(SpyOrchestrator(), "BASE", r.problem, r.compiled, require_units=True)  # type: ignore[arg-type]
        assert e.value.code == "UNIT_EVIDENCE_MISSING"

    def test_admit_rejects_tampered_evidence_after_the_fact(self, validated_run) -> None:  # type: ignore[no-untyped-def]
        run, _ = validated_run
        forged = ScenarioRun(
            run.scenario_id,
            run.problem,
            run.formulation,
            run.result,
            run.model_sha256,
            run.units.model_copy(update={"report_sha256": "f" * 64}),
        )
        rej = admit(forged)
        assert getattr(rej, "code", "") == "UNIT_EVIDENCE_INVALID"
        legacy = ScenarioRun(
            run.scenario_id, run.problem, run.formulation, run.result, run.model_sha256
        )
        assert admit(legacy).units_status == "LEGACY_UNITS_UNVERIFIED"  # type: ignore[union-attr]
        assert getattr(admit(legacy, require_units=True), "code", "") == "UNIT_EVIDENCE_MISSING"

    def test_decision_conditions_on_unit_evidence(self, validated_run) -> None:  # type: ignore[no-untyped-def]
        run, r = validated_run
        scen = compile_scenario(
            run.problem,
            ScenarioSpec(
                scenario_id="M120", changes=(CapacityChange(resource_id="MACHINE", capacity="120"),)
            ),
        )
        legacy = solve_run(
            LPOrchestrator(HighsAdapter()),
            "M120",
            scen.problem,
            scen.formulation,
            policy=AdmissionPolicy.LEGACY_UNVERIFIED,
        )
        brief = decide(run, [legacy], DecisionConfig())
        assert [(x.scenario_id, x.code) for x in brief.rejected_scenarios] == [
            ("M120", "UNIT_EVIDENCE_MISSING")
        ]
        assert (
            brief.recommendation.recommended_scenario_id is None
            or brief.recommendation.coverage.rejected_untrusted == 1
        )
        assert any("DIMENSIONALLY_VALIDATED" in x for x in brief.limitations)
        ev = {e.scenario_id: e for e in brief.evidence}["BASE"]
        assert ev.unit_status == "DIMENSIONALLY_VALIDATED"
        assert ev.unit_report_sha256 == r.record.unit_report_sha256
        strict_cfg = DecisionConfig(require_unit_evidence=True)
        old = formulate(stated())
        legacy_base = solve_run(
            LPOrchestrator(HighsAdapter()),
            "BASE",
            old.problem,
            old.compiled,  # type: ignore[arg-type]
            policy=AdmissionPolicy.LEGACY_UNVERIFIED,
        )
        b2 = decide(legacy_base, [], strict_cfg)
        assert b2.baseline_status == "REJECTED:UNIT_EVIDENCE_MISSING"
        b3 = decide(legacy_base, [], DecisionConfig())
        assert any("LEGACY_UNVERIFIED" in x for x in b3.limitations)


@pytest.fixture
def svc(tmp_path: Path) -> GovernedSessions:
    tick = itertools.count(1)
    return GovernedSessions(
        SqliteStore(tmp_path / "u.db"),
        LPOrchestrator(HighsAdapter()),
        clock=lambda: f"2026-03-01T00:{next(tick) // 60:02d}:{next(tick) % 60:02d}+00:00",
    )


class TestAcceptanceJourney:
    def test_scale_mismatch_to_approved_conversion_to_real_decision(
        self, svc: GovernedSessions, tmp_path: Path
    ) -> None:
        # 1-2: problem entered, unit claims recorded (machine coefficients stated in minutes)
        svc.create(ANA, "U1", "Weekly mix with minutes")
        for k, v in strict("min/piece", "120", "60").items():
            svc.assert_fact(ANA, "U1", k, v)
        # 3-4: differently scaled equation detected; explicit clarification requested
        st = svc.state(ANA, "U1")
        assert st.status is FormulationStatus.NEEDS_CLARIFICATION
        issue = next(i for i in st.issues if i.code == "CONVERSION_REQUIRED")
        assert "resource:MACHINE|min|60/1|exact" in issue.candidates
        with pytest.raises(Exception, match="NOT_READY"):
            svc.run_decision(ANA, "U1", [])  # nothing reaches the solver
        # 5-6: authorized human approves the exact conversion; the ledger records it
        st = svc.convert_units(
            ANA, "U1", "resource:MACHINE", "min", "capacity expressed in minutes per planner"
        )
        assert st.status is FormulationStatus.READY and st.units_status == "DIMENSIONALLY_VALIDATED"
        conv = [e for e in svc._store.load_ledger("U1").entries if e.op is Op.CONVERT]
        assert len(conv) == 1 and conv[0].actor.id == "ana" and conv[0].factor == "60/1"
        assert conv[0].at.startswith("2026-03-01") and conv[0].unit_from == "h"
        # 7-8: formulation rebuilt with exact units; pre-solver validation passes
        built = svc.build(ANA, "U1")
        assert built.problem is not None and built.unit_evidence is not None
        # 9-10: real HiGHS solves; decision evidence carries the unit-report hash
        brief = svc.run_decision(
            ANA,
            "U1",
            [
                ScenarioSpec(
                    scenario_id="M7200",
                    changes=(CapacityChange(resource_id="MACHINE", capacity="7200"),),
                )
            ],
        )
        by = {e.scenario_id: e for e in brief.evidence}
        assert set(by) == {"BASE", "M7200"}
        assert all(e.unit_status == "DIMENSIONALLY_VALIDATED" for e in by.values())
        assert all(e.unit_report_sha256 == st.unit_report_sha256 for e in by.values())
        o = brief.comparison.compared[0].objective  # type: ignore[union-attr]
        assert o is not None and math.isclose(float(o.baseline), 2600.0, abs_tol=1e-6)
        assert math.isclose(float(o.scenario), 2800.0, abs_tol=1e-6)
        assert brief.rejected_scenarios == ()
        # custom state survives a restart: rebuild from the persisted ledger only
        again = GovernedSessions(SqliteStore(tmp_path / "u.db"), LPOrchestrator(HighsAdapter()))
        r2 = again.build(ANA, "U1")
        assert r2.record is not None and built.record is not None
        assert r2.record.record_sha256 == built.record.record_sha256
        assert r2.record.conversions == built.record.conversions

    def test_custom_unit_and_conversion_survive_restart(self, tmp_path: Path) -> None:
        path = tmp_path / "c.db"
        s1 = GovernedSessions(SqliteStore(path), LPOrchestrator(HighsAdapter()))
        s1.create(ANA, "C1", "trays")
        f = strict()
        f["product:A:bound_unit"], f["product:A:max_quantity"] = "tray", "7"
        for k, v in f.items():
            s1.assert_fact(ANA, "C1", k, v)
        s1.assert_fact(ANA, "C1", "unitdef:tray", "6|piece")
        s1.convert_units(ANA, "C1", "bound:A", "piece", "tray is six pieces per site standard")
        s2 = GovernedSessions(SqliteStore(path), LPOrchestrator(HighsAdapter()))
        r = s2.build(ANA, "C1")
        assert r.problem is not None and r.record is not None
        assert {p.id: p.max_quantity for p in r.problem.products}["A"] == Decimal(42)
        assert r.record.custom_units == (("tray", "piece", "6/1", "ana"),)
        assert r.record.conversions[0][4] == "ana"

    def test_require_units_governed_sessions_refuse_legacy(self, tmp_path: Path) -> None:
        s = GovernedSessions(
            SqliteStore(tmp_path / "l.db"), LPOrchestrator(HighsAdapter()), require_units=True
        )
        s.create(ANA, "L1", "legacy")
        for k, v in FACTS.items():
            s.assert_fact(ANA, "L1", k, v)
        with pytest.raises(PlanningInputError) as e:
            s.run_decision(ANA, "L1", [])
        assert e.value.code == "UNIT_EVIDENCE_MISSING"


def test_legacy_compile_path_is_unchanged() -> None:
    r = formulate(stated())
    assert r.problem is not None and r.compiled is not None
    assert (
        compile_problem(r.problem, lineage=r.compiled.manifest.lineage).spec_json
        == r.compiled.spec_json
    )
    _ = Entry

"""Production-planning admission gate: fail-closed, explicit policies, no silent bypass."""

from __future__ import annotations

import itertools
import math
from pathlib import Path
from typing import Any

import pytest

from qorvexa.industrial.decision_intelligence import (
    DecisionConfig,
    ScenarioRun,
    decide,
    run_from_outcome,
    solve_run,
)
from qorvexa.industrial.decision_intelligence import service as di_service
from qorvexa.industrial.decision_intelligence.recommendation import RecommendationStatus
from qorvexa.industrial.decision_intelligence.trust import admit
from qorvexa.industrial.formulation import (
    Actor,
    ActorKind,
    Entry,
    Ledger,
    Op,
    convert_units,
    formulate,
)
from qorvexa.industrial.formulation.ledger import entry_digest
from qorvexa.industrial.governance import GovernedSessions, Principal, Role, SqliteStore
from qorvexa.industrial.governance.admission import admit_ledger_formulation
from qorvexa.industrial.production_planning import (
    CapacityChange,
    ScenarioSpec,
    compile_scenario,
)
from qorvexa.industrial.production_planning.admission import (
    AdmissionError,
    AdmissionPolicy,
    AdmissionStatus,
    admit_for_solve,
    verify_admission,
)
from qorvexa.solver_fabric import LPOrchestrator
from qorvexa.solver_fabric.highs_adapter import HighsAdapter
from tests.test_formulation import FACTS, stated
from tests.test_unit_aware_formulation import NOTE, strict

STRICT = AdmissionPolicy.STRICT
LEGACY = AdmissionPolicy.LEGACY_UNVERIFIED
ANA = Principal("ana", ActorKind.HUMAN, frozenset({Role.PLANNER}))
ACK_ALL = frozenset({"CAPACITY_CHANGE_COST_NOT_MODELED", "UNIT_VALIDATION_NOT_PERFORMED"})


class Spy:
    """Solver boundary spy: any call is recorded and fails the test."""

    def __init__(self) -> None:
        self.calls = 0

    def solve_json(self, *a: object, **k: object) -> Any:
        self.calls += 1
        raise AssertionError("solver was invoked after failed admission")


def clock() -> Any:
    t = itertools.count(1)
    return lambda: f"2026-04-01T00:00:{next(t) % 60:02d}+00:00"


def code_of(fn: Any) -> str:
    with pytest.raises(AdmissionError) as e:
        fn()
    return e.value.code


@pytest.fixture(scope="module")
def strict_built() -> Any:
    r = formulate(stated(strict()))
    assert r.problem is not None and r.unit_evidence is not None
    return r


@pytest.fixture(scope="module")
def legacy_built() -> Any:
    r = formulate(stated())
    assert r.problem is not None
    return r


class TestStrictRejections:
    def test_strict_rejects_legacy_formulation_without_calling_the_solver(
        self, legacy_built: Any
    ) -> None:
        spy = Spy()
        r = legacy_built
        assert (
            code_of(lambda: solve_run(spy, "BASE", r.problem, r.compiled, policy=STRICT))  # type: ignore[arg-type]
            == "UNIT_EVIDENCE_MISSING"
        )
        assert code_of(
            lambda: solve_run(spy, "BASE", r.problem, r.compiled, require_units=True)
        ) == (  # type: ignore[arg-type]
            "UNIT_EVIDENCE_MISSING"
        )
        assert spy.calls == 0

    def test_solve_run_defaults_to_strict(self, legacy_built: Any) -> None:
        spy, r = Spy(), legacy_built
        assert code_of(lambda: solve_run(spy, "BASE", r.problem, r.compiled)) == (  # type: ignore[arg-type]
            "UNIT_EVIDENCE_MISSING"
        )
        assert spy.calls == 0

    def test_governed_sessions_default_to_strict_and_reject_legacy_ledgers(
        self, tmp_path: Path
    ) -> None:
        spy = Spy()
        s = GovernedSessions(SqliteStore(tmp_path / "a.db"), spy)  # type: ignore[arg-type]
        assert s._policy is STRICT
        s.create(ANA, "S", "legacy")
        for k, v in FACTS.items():
            s.assert_fact(ANA, "S", k, v)
        assert code_of(lambda: s.run_decision(ANA, "S", [])) == "UNIT_EVIDENCE_MISSING"
        assert spy.calls == 0
        old = GovernedSessions(SqliteStore(tmp_path / "b.db"), spy, require_units=False)  # type: ignore[arg-type]
        assert old._policy is LEGACY  # legacy requires explicit opt-in
        assert (
            GovernedSessions(SqliteStore(tmp_path / "c.db"), spy, require_units=True)._policy
            is STRICT
        )  # type: ignore[arg-type]

    def test_tampered_report_hash_is_rejected(self, strict_built: Any) -> None:
        r, spy = strict_built, Spy()
        bad = r.unit_evidence.model_copy(update={"report_sha256": "0" * 64})
        assert code_of(
            lambda: solve_run(spy, "B", r.problem, r.compiled, units=bad, policy=STRICT)
        ) == (  # type: ignore[arg-type]
            "UNIT_EVIDENCE_MISMATCH"
        )
        assert spy.calls == 0

    def test_stale_model_hash_is_rejected(self, strict_built: Any) -> None:
        r, spy = strict_built, Spy()
        ev = r.unit_evidence
        assert (
            code_of(
                lambda: solve_run(
                    spy,
                    "B",
                    r.problem,
                    r.compiled,
                    units=ev,
                    policy=STRICT,
                    expected_model_sha256="0" * 64,  # type: ignore[arg-type]
                )
            )
            == "STALE_MODEL_HASH"
        )
        rec = admit_for_solve(r.problem, r.compiled, policy=STRICT, units=ev)
        scen = compile_scenario(
            r.problem,
            ScenarioSpec(
                scenario_id="M", changes=(CapacityChange(resource_id="MACHINE", capacity="120"),)
            ),
        )
        # an admission minted for the base model cannot be replayed for another model
        assert code_of(
            lambda: solve_run(
                spy, "M", scen.problem, scen.formulation, units=ev, admission=rec, policy=STRICT
            )  # type: ignore[arg-type]
        ) in {"ADMISSION_INVALID", "PROBLEM_MANIFEST_MISMATCH", "STALE_MODEL_HASH"}
        assert spy.calls == 0

    def test_modified_custom_unit_definitions_are_rejected(self) -> None:
        f = strict()
        f["product:A:bound_unit"], f["product:A:max_quantity"] = "tray", "7"
        led = convert_units(
            stated(f).assert_fact("unitdef:tray", "6|piece", "ana"),
            "bound:A",
            "piece",
            "ana",
            NOTE,
            "t",
        )
        r = formulate(led)
        assert r.problem is not None and r.unit_evidence is not None
        spy = Spy()
        bad = r.unit_evidence.model_copy(update={"custom_units": (("tray", "piece", "7/1"),)})
        assert code_of(
            lambda: solve_run(spy, "B", r.problem, r.compiled, units=bad, policy=STRICT)
        ) in {  # type: ignore[arg-type]
            "UNIT_EVIDENCE_MISMATCH",
            "UNIT_VALIDATION_FAILED",
        }
        i = next(n for n, e in enumerate(led.entries) if e.key == "unitdef:tray")
        forged = Ledger(
            (
                *led.entries[:i],
                led.entries[i].model_copy(update={"value": "60|piece"}),
                *led.entries[i + 1 :],
            )
        )
        assert code_of(lambda: admit_ledger_formulation(forged, r, STRICT)) == "LEDGER_TAMPERED"
        assert spy.calls == 0

    def test_unconfirmed_or_forged_conversion_events_are_rejected(self) -> None:
        led = stated(strict("min/piece", "120", "60"))
        good = convert_units(led, "resource:MACHINE", "min", "ana", NOTE, "t")
        r = formulate(good)
        assert r.problem is not None
        last = good.entries[-1]
        llm = last.model_copy(update={"actor": Actor(id="bot", kind=ActorKind.LLM)})
        llm = llm.model_copy(update={"entry_sha256": entry_digest(llm)})
        forged = Ledger((*good.entries[:-1], llm))
        assert code_of(lambda: admit_ledger_formulation(forged, r, STRICT)) == "LEDGER_TAMPERED"
        retracted = good.retract(len(good.entries), "ana")
        assert code_of(lambda: admit_ledger_formulation(retracted, r, STRICT)) == "NOT_READY"
        assert Op.CONVERT in {e.op for e in good.entries} and isinstance(last, Entry)

    def test_ledger_changed_after_build_is_stale(self, strict_built: Any) -> None:
        led = stated(strict()).assert_fact("problem:period_label", "Week 14", "ana")
        assert (
            code_of(lambda: admit_ledger_formulation(led, strict_built, STRICT))
            == "STALE_FORMULATION"
        )

    def test_invalid_dimensions_never_reach_the_solver(self, tmp_path: Path) -> None:
        spy = Spy()
        s = GovernedSessions(SqliteStore(tmp_path / "d.db"), spy)  # type: ignore[arg-type]
        s.create(ANA, "D", "bad dims")
        for k, v in strict(machine_unit="kg/piece").items():
            s.assert_fact(ANA, "D", k, v)
        with pytest.raises(Exception, match="NOT_READY"):
            s.run_decision(ANA, "D", [])
        assert spy.calls == 0

    def test_direct_execution_cannot_skip_admission(
        self, strict_built: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        r, spy = strict_built, Spy()

        def refuse(*a: object, **k: object) -> None:
            raise AdmissionError("ADMISSION_REJECTED_TEST", "refused")

        monkeypatch.setattr(di_service, "admit_for_solve", refuse)
        assert code_of(
            lambda: solve_run(spy, "B", r.problem, r.compiled, units=r.unit_evidence)
        ) == (  # type: ignore[arg-type]
            "ADMISSION_REJECTED_TEST"
        )
        assert spy.calls == 0
        monkeypatch.undo()
        rec = admit_for_solve(r.problem, r.compiled, policy=STRICT, units=r.unit_evidence)
        forged = rec.model_copy(update={"model_sha256": "1" * 64})
        assert code_of(
            lambda: solve_run(
                spy, "B", r.problem, r.compiled, units=r.unit_evidence, admission=forged
            )
        ) == (  # type: ignore[arg-type]
            "STALE_MODEL_HASH"
        )
        assert spy.calls == 0


@pytest.fixture(scope="module")
def strict_run(strict_built: Any) -> ScenarioRun:
    r = strict_built
    return solve_run(
        LPOrchestrator(HighsAdapter()),
        "BASE",
        r.problem,
        r.compiled,
        units=r.unit_evidence,
        policy=STRICT,
    )


class TestAdmittedPaths:
    def test_valid_unit_aware_model_reaches_real_highs_with_verified_admission(
        self, strict_run: ScenarioRun, strict_built: Any
    ) -> None:
        a = strict_run.admission
        assert a is not None and a.status is AdmissionStatus.ADMITTED_UNITS_VERIFIED
        assert a.policy is STRICT and a.warning == "" and a.units_verified
        assert a.unit_report_sha256 == strict_built.unit_evidence.report_sha256
        assert len(a.custom_registry_fingerprint) == 64 and len(a.model_sha256) == 64
        adm = admit(strict_run, require_units=True)
        assert not hasattr(adm, "code"), adm
        assert adm.admission_status == "ADMITTED_UNITS_VERIFIED"  # type: ignore[union-attr]
        assert math.isclose(float(adm.metrics["objective"]), 2600.0, abs_tol=1e-6)  # type: ignore[union-attr]

    def test_explicit_legacy_is_possible_and_visibly_unverified(self, legacy_built: Any) -> None:
        r = legacy_built
        run = solve_run(
            LPOrchestrator(HighsAdapter()), "BASE", r.problem, r.compiled, policy=LEGACY
        )
        a = run.admission
        assert a is not None and a.status is AdmissionStatus.ADMITTED_LEGACY_UNVERIFIED
        assert a.unit_status == "LEGACY_UNITS_UNVERIFIED" and "NOT established" in a.warning
        assert a.unit_report_sha256 == ""
        brief = decide(run, [], DecisionConfig())
        assert brief.recommendation.status is RecommendationStatus.CONDITIONALLY_RECOMMENDED
        assert "UNIT_VALIDATION_NOT_PERFORMED" in brief.recommendation.unresolved_assumptions
        assert any("LEGACY_UNVERIFIED" in x for x in brief.limitations)
        ev = brief.evidence[0]
        assert ev.admission_status == "ADMITTED_LEGACY_UNVERIFIED" and ev.unit_report_sha256 == ""
        acked = decide(run, [], DecisionConfig(acknowledged_assumptions=ACK_ALL))
        assert acked.recommendation.status is RecommendationStatus.RECOMMENDED
        strict_brief = decide(run, [], DecisionConfig(admission_policy=STRICT))
        assert strict_brief.baseline_status.startswith("REJECTED:")

    def test_generic_lp_workflow_is_unaffected_but_not_admitted_evidence(
        self, strict_built: Any
    ) -> None:
        r = strict_built
        out = LPOrchestrator(HighsAdapter()).solve_json(r.compiled.spec_json, job_id="generic")
        assert out.result is not None
        raw = run_from_outcome("BASE", r.problem, r.compiled, out)
        assert raw.admission is None
        ok = admit(raw)  # legacy-compatible default: allowed but labelled
        assert not hasattr(ok, "code") and ok.admission_status == "NOT_ADMITTED"  # type: ignore[union-attr]
        assert getattr(admit(raw, require_units=True), "code", "") in {
            "UNIT_EVIDENCE_MISSING",
            "ADMISSION_MISSING",
        }
        brief = decide(raw, [], DecisionConfig(admission_policy=STRICT))
        assert brief.baseline_status.startswith("REJECTED:")

    def test_decision_intelligence_refuses_improperly_admitted_results(
        self, strict_run: ScenarioRun, legacy_built: Any
    ) -> None:
        run = strict_run
        assert run.admission is not None

        def variant(**upd: Any) -> ScenarioRun:
            adm = run.admission.model_copy(update=upd) if upd else run.admission
            return ScenarioRun(
                run.scenario_id,
                run.problem,
                run.formulation,
                run.result,
                run.model_sha256,
                run.units,
                adm,
            )

        assert getattr(admit(variant(record_sha256="f" * 64)), "code", "") == "ADMISSION_INVALID"
        assert getattr(admit(variant(model_sha256="e" * 64)), "code", "") == "ADMISSION_INVALID"
        assert (
            getattr(admit(variant(unit_report_sha256="d" * 64)), "code", "") == "ADMISSION_INVALID"
        )
        assert getattr(admit(variant(warning="trust me")), "code", "") == "ADMISSION_INVALID"
        no_units = ScenarioRun(
            run.scenario_id,
            run.problem,
            run.formulation,
            run.result,
            run.model_sha256,
            None,
            run.admission,
        )
        assert getattr(admit(no_units), "code", "") == "ADMISSION_INVALID"
        # a legacy admission can never satisfy STRICT
        r = legacy_built
        leg = solve_run(
            LPOrchestrator(HighsAdapter()), "BASE", r.problem, r.compiled, policy=LEGACY
        )
        assert getattr(admit(leg, require_units=True), "code", "") in {
            "UNIT_EVIDENCE_MISSING",
            "ADMISSION_NOT_VERIFIED",
        }
        # reusing the strict admission for the legacy model is refused
        swapped = ScenarioRun(
            leg.scenario_id,
            leg.problem,
            leg.formulation,
            leg.result,
            leg.model_sha256,
            None,
            run.admission,
        )
        assert getattr(admit(swapped), "code", "") == "ADMISSION_INVALID"
        bad = variant(record_sha256="0" * 64)
        other = ScenarioRun(
            "T",
            bad.problem,
            bad.formulation,
            bad.result,
            bad.model_sha256,
            bad.units,
            bad.admission,
        )
        brief = decide(run, [other], DecisionConfig())
        assert [(r.scenario_id, r.code) for r in brief.rejected_scenarios] == [
            ("T", "ADMISSION_INVALID")
        ]

    def test_verify_admission_round_trip(self, strict_built: Any) -> None:
        r = strict_built
        rec = admit_for_solve(r.problem, r.compiled, policy=STRICT, units=r.unit_evidence)
        verify_admission(rec, r.problem, r.compiled, r.unit_evidence)
        assert (
            code_of(lambda: verify_admission(rec, r.problem, r.compiled, None))
            == "UNIT_EVIDENCE_MISSING"
        )
        assert rec == admit_for_solve(r.problem, r.compiled, policy=STRICT, units=r.unit_evidence)

    def test_strict_evidence_is_never_downgraded_or_upgraded(
        self, strict_built: Any, legacy_built: Any
    ) -> None:
        r = strict_built
        # valid evidence under the legacy policy is reported as what it is: verified
        rec = admit_for_solve(r.problem, r.compiled, policy=LEGACY, units=r.unit_evidence)
        assert rec.units_verified
        # a legacy model can never be labelled verified
        lg = legacy_built
        rec2 = admit_for_solve(lg.problem, lg.compiled, policy=LEGACY)
        assert not rec2.units_verified and rec2.unit_report_sha256 == ""
        # a formulation that claims unit lineage but ships no evidence is hostile
        assert (
            code_of(lambda: admit_for_solve(r.problem, r.compiled, policy=LEGACY))
            == "UNIT_EVIDENCE_MISSING"
        )


class TestBothPathsEndToEnd:
    def test_strict_and_explicit_legacy_paths_through_governed_sessions(
        self, tmp_path: Path
    ) -> None:
        orch = LPOrchestrator(HighsAdapter())
        scen = [
            ScenarioSpec(
                scenario_id="M", changes=(CapacityChange(resource_id="MACHINE", capacity="120"),)
            )
        ]
        # strict industrial path
        s = GovernedSessions(SqliteStore(tmp_path / "s.db"), orch, clock=clock())
        s.create(ANA, "ST", "strict")
        for k, v in strict().items():
            s.assert_fact(ANA, "ST", k, v)
        b = s.run_decision(ANA, "ST", scen)
        ev = {e.scenario_id: e for e in b.evidence}
        assert {e.admission_status for e in ev.values()} == {"ADMITTED_UNITS_VERIFIED"}
        assert all(e.unit_report_sha256 and e.admission_sha256 for e in ev.values())
        assert "UNIT_VALIDATION_NOT_PERFORMED" not in b.recommendation.unresolved_assumptions
        assert b.rejected_scenarios == ()
        # explicit legacy path: opt-in, labelled, conditional
        lg = GovernedSessions(SqliteStore(tmp_path / "l.db"), orch, clock=clock(), policy=LEGACY)
        lg.create(ANA, "LG", "legacy")
        for k, v in FACTS.items():
            lg.assert_fact(ANA, "LG", k, v)
        lb = lg.run_decision(ANA, "LG", scen)
        assert {e.admission_status for e in lb.evidence} == {"ADMITTED_LEGACY_UNVERIFIED"}
        assert lb.recommendation.status is RecommendationStatus.CONDITIONALLY_RECOMMENDED
        assert "UNIT_VALIDATION_NOT_PERFORMED" in lb.recommendation.unresolved_assumptions
        assert any("LEGACY_UNVERIFIED" in x for x in lb.limitations)
        # no silent path: the strict session refuses the very same legacy ledger
        s.create(ANA, "ST2", "legacy in strict")
        for k, v in FACTS.items():
            s.assert_fact(ANA, "ST2", k, v)
        assert code_of(lambda: s.run_decision(ANA, "ST2", scen)) == "UNIT_EVIDENCE_MISSING"
        # a legacy-policy session still reports verified evidence truthfully when it exists
        lg.create(ANA, "LG2", "strict facts under legacy policy")
        for k, v in strict().items():
            lg.assert_fact(ANA, "LG2", k, v)
        vb = lg.run_decision(ANA, "LG2", [])
        assert {e.admission_status for e in vb.evidence} == {"ADMITTED_UNITS_VERIFIED"}

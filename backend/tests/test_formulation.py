"""Autonomous problem formulation: ledger trust rules, gaps, contradictions, builder, real solve."""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any, ClassVar

import pytest

from qorvexa.industrial.decision_intelligence import solve_run
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
    apply_defaults,
    confirm_keys,
    formulate,
    propose_from_draft,
)
from qorvexa.industrial.formulation.ledger import entry_digest
from qorvexa.industrial.production_planning.admission import AdmissionPolicy
from qorvexa.solver_fabric import LPOrchestrator
from qorvexa.solver_fabric.highs_adapter import HighsAdapter

H = "planner.ana"
LLM = Actor(id="extractor-1", kind=ActorKind.LLM)

FACTS = {
    "problem:id": "MIX1",
    "problem:title": "Weekly product mix",
    "problem:mode": "MAXIMIZE_MARGIN",
    "problem:currency": "EUR",
    "product:A:name": "Product A",
    "product:A:unit": "piece",
    "product:A:value_per_unit": "40",
    "product:A:min_quantity": "0",
    "product:A:max_quantity": "40",
    "product:B:name": "Product B",
    "product:B:unit": "piece",
    "product:B:value_per_unit": "30",
    "product:B:min_quantity": "0",
    "product:B:max_quantity": "NONE",
    "resource:LABOR:name": "Labor",
    "resource:LABOR:kind": "LABOR",
    "resource:LABOR:unit": "h",
    "resource:LABOR:capacity": "80",
    "resource:MACHINE:name": "Machine",
    "resource:MACHINE:kind": "MACHINE",
    "resource:MACHINE:unit": "h",
    "resource:MACHINE:capacity": "100",
    "consumption:MACHINE:A": "2",
    "consumption:MACHINE:B": "1",
    "consumption:LABOR:A": "1",
    "consumption:LABOR:B": "1",
}


def stated(facts: dict[str, str] | None = None) -> Ledger:
    led = Ledger()
    for k, v in (facts or FACTS).items():
        led = led.assert_fact(k, v, H)
    return led


def codes(led: Ledger) -> set[str]:
    return {i.code for i in analyze(led).issues}


class TestGapsAndClarification:
    def test_empty_ledger_asks_questions_and_builds_nothing(self) -> None:
        r = formulate(Ledger())
        assert r.state.status is FormulationStatus.NEEDS_CLARIFICATION
        assert r.problem is None and r.compiled is None and r.record is None
        keys = {i.key for i in r.state.issues}
        assert {"problem:mode", "problem:currency", "product", "resource"} <= keys
        assert all(i.question for i in r.state.issues)

    def test_each_missing_required_field_is_reported_not_defaulted(self) -> None:
        for key in FACTS:
            if key.endswith(("min_quantity", "max_quantity")) or key.startswith("consumption"):
                continue
            partial = {k: v for k, v in FACTS.items() if k != key}
            r = formulate(stated(partial))
            assert r.problem is None, key
            assert key in {i.key for i in r.state.issues}, key

    def test_unstated_min_max_and_consumption_are_assumable_not_assumed(self) -> None:
        partial = {
            k: v
            for k, v in FACTS.items()
            if not k.endswith(("min_quantity", "max_quantity")) and k != "consumption:LABOR:B"
        }
        r = formulate(stated(partial))
        assert r.problem is None
        assumable = {i.key for i in r.state.issues if i.can_assume}
        assert "consumption:LABOR:B" in assumable and "product:A:min_quantity" in assumable

    def test_capacity_and_value_are_never_assumable(self) -> None:
        led = stated()
        for key in ("resource:LABOR:capacity", "product:A:value_per_unit", "problem:currency"):
            with pytest.raises(FormulationError) as e:
                led.assume(key, "0", H, "closed world assumption by planner")
            assert e.value.code == "ASSUMPTION_NOT_ALLOWED"

    def test_assumption_value_and_rationale_are_restricted(self) -> None:
        led = Ledger()
        with pytest.raises(FormulationError) as e:
            led.assume("product:A:min_quantity", "5", H, "planner believes five is typical")
        assert e.value.code == "ASSUMPTION_VALUE_NOT_ALLOWED"
        with pytest.raises(FormulationError) as e2:
            led.assume("consumption:R:A", "0", H, "short")
        assert e2.value.code == "RATIONALE_REQUIRED"

    def test_apply_defaults_is_explicit_and_recorded(self) -> None:
        partial = {
            k: v for k, v in FACTS.items() if not k.endswith(("min_quantity", "max_quantity"))
        }
        led = apply_defaults(stated(partial), H, "no commitments or caps were agreed this week")
        r = formulate(led)
        assert r.state.status is FormulationStatus.READY and r.record is not None
        keys = {a[0] for a in r.record.assumptions}
        assert keys == {
            "product:A:min_quantity",
            "product:A:max_quantity",
            "product:B:min_quantity",
            "product:B:max_quantity",
        }
        assert all(a[2] and a[3] == H for a in r.record.assumptions)


class TestProposalsAreUntrusted:
    DRAFT: ClassVar[dict[str, Any]] = {
        "problem": {"id": "MIX1", "title": "Weekly product mix", "currency": "EUR"},
        "products": [{"id": "A", "name": "Product A", "unit": "piece"}],
    }

    def test_llm_draft_never_enters_the_model(self) -> None:
        led = propose_from_draft(Ledger(), self.DRAFT, LLM, source_ref="chat:1")
        st = analyze(led)
        assert st.facts == ()
        assert {"UNCONFIRMED_PROPOSAL"} <= {i.code for i in st.issues}
        assert formulate(led).problem is None

    def test_human_confirmation_promotes_a_proposal(self) -> None:
        led = propose_from_draft(Ledger(), self.DRAFT, LLM)
        led = confirm_keys(led, ["problem:id", "problem:currency"], H)
        facts = {f.key: f for f in analyze(led).facts}
        assert facts["problem:id"].basis == "CONFIRMED_PROPOSAL"
        assert "problem:title" not in facts

    def test_confirming_a_non_proposal_or_unknown_entry_fails(self) -> None:
        led = Ledger().assert_fact("problem:id", "X1", H)
        with pytest.raises(FormulationError) as e:
            led.confirm(1, H)
        assert e.value.code == "NOT_A_PROPOSAL"
        with pytest.raises(FormulationError) as e2:
            led.confirm(9, H)
        assert e2.value.code == "UNKNOWN_ENTRY"

    def test_forged_llm_confirmation_is_detected(self) -> None:
        led = propose_from_draft(Ledger(), self.DRAFT, LLM)
        seq = led.entries[0].seq
        fake = Entry(
            seq=len(led.entries) + 1,
            prev_sha256=led.head,
            op=Op.CONFIRM,
            actor=LLM,
            key=led.entries[0].key,
            target=seq,
        )
        fake = fake.model_copy(update={"entry_sha256": entry_digest(fake)})
        forged = Ledger((*led.entries, fake))
        with pytest.raises(FormulationError) as e:
            analyze(forged)
        assert e.value.code == "LEDGER_TAMPERED"

    def test_draft_with_unsupported_or_unknown_fields_is_refused_atomically(self) -> None:
        bad = {"products": [{"id": "A", "unit": "piece", "setup_time": "3"}]}
        with pytest.raises(FormulationError) as e:
            propose_from_draft(Ledger(), bad, LLM)
        assert e.value.code == "UNSUPPORTED_FEATURE"
        with pytest.raises(FormulationError) as e2:
            propose_from_draft(Ledger(), {"products": [{"id": "A", "colour": "red"}]}, LLM)
        assert e2.value.code == "INVALID_DRAFT"
        with pytest.raises(FormulationError):
            propose_from_draft(
                Ledger(), {"products": [{"id": "A", "unit": "piece", "value_per_unit": 1.5}]}, LLM
            )


class TestContradictionsAndFeatures:
    def test_conflicting_human_statements_block_until_resolved(self) -> None:
        led = stated().assert_fact("resource:MACHINE:capacity", "120", H)
        r = formulate(led)
        assert r.state.status is FormulationStatus.CONTRADICTED and r.problem is None
        issue = next(i for i in r.state.issues if i.code == "CONTRADICTION")
        assert issue.candidates == ("100", "120")
        winner = issue.claim_seqs[-1]
        resolved = led.resolve(winner, "lead.omar")
        r2 = formulate(resolved)
        assert r2.state.status is FormulationStatus.READY and r2.problem is not None
        cap = {x.id: x.capacity for x in r2.problem.resources}["MACHINE"]
        assert cap == Decimal(120)

    def test_numerically_equal_restatement_is_not_a_contradiction(self) -> None:
        led = stated().assert_fact("resource:MACHINE:capacity", "100.00", H)
        assert formulate(led).state.status is FormulationStatus.READY

    def test_unsupported_feature_blocks_until_human_retracts(self) -> None:
        led = stated().assert_fact("feature:setup_time", "3h changeover between A and B", H)
        r = formulate(led)
        assert r.state.status is FormulationStatus.UNSUPPORTED and r.problem is None
        seq = len(led.entries)
        assert formulate(led.retract(seq, H)).state.status is FormulationStatus.READY

    def test_unknown_consideration_is_not_silently_dropped(self) -> None:
        led = stated().assert_fact("feature:operator_breaks", "operators take two breaks", H)
        r = formulate(led)
        assert r.state.status is FormulationStatus.UNSUPPORTED
        assert r.state.issues[0].code == "UNMODELED_CONSIDERATION"

    def test_assumption_is_overridden_by_a_later_statement(self) -> None:
        led = stated({k: v for k, v in FACTS.items() if k != "consumption:LABOR:B"})
        led = led.assume("consumption:LABOR:B", "0", H, "labor not used by B in this plant")
        led = led.assert_fact("consumption:LABOR:B", "1", H)
        r = formulate(led)
        assert r.record is not None and r.record.assumptions == ()

    def test_typo_in_reference_is_flagged(self) -> None:
        led = stated().assert_fact("consumption:MACHNE:A", "2", H)
        r = formulate(led)
        assert r.state.status is FormulationStatus.NEEDS_CLARIFICATION
        assert any(i.code == "UNDECLARED_REFERENCE" for i in r.state.issues)


class TestBuilder:
    def test_ready_ledger_builds_the_expected_validated_problem(self) -> None:
        r = formulate(stated())
        assert r.state.status is FormulationStatus.READY and r.state.issues == ()
        p = r.problem
        assert p is not None and r.compiled is not None and r.record is not None
        assert [x.id for x in p.products] == ["A", "B"]
        a = p.products[0]
        assert (a.value_per_unit, a.min_quantity, a.max_quantity) == (Decimal(40), 0, Decimal(40))
        assert p.products[1].max_quantity is None
        assert len(p.consumptions) == 4
        lineage = r.compiled.manifest.lineage
        assert f"formulation_ledger:{r.record.ledger_head}" in lineage

    def test_model_level_errors_surface_as_invalid_model(self) -> None:
        facts = dict(FACTS, **{"product:A:min_quantity": "50"})  # min 50 > max 40
        r = formulate(stated(facts))
        assert r.state.status is FormulationStatus.INVALID_MODEL and r.problem is None
        assert "exceeds" in r.state.issues[0].message

    def test_minimize_cost_without_commitments_is_rejected(self) -> None:
        r = formulate(stated(dict(FACTS, **{"problem:mode": "minimize_cost"})))
        assert r.state.status is FormulationStatus.INVALID_MODEL
        assert r.state.issues[0].code == "NO_COMMITMENTS"

    def test_floats_and_bad_values_are_refused(self) -> None:
        with pytest.raises(FormulationError):
            Ledger().assert_fact("resource:LABOR:capacity", 80.5, H)
        with pytest.raises(FormulationError):
            Ledger().assert_fact("resource:LABOR:capacity", "-1", H)
        with pytest.raises(FormulationError):
            Ledger().assert_fact("resource:LABOR:unit", "unknown", H)
        with pytest.raises(FormulationError):
            Ledger().assert_fact("bogus:key", "1", H)
        with pytest.raises(FormulationError):
            Ledger().assert_fact("problem:mode", "MAXIMISE", H)

    def test_state_and_record_are_deterministic_and_order_independent_of_input_keys(self) -> None:
        a = formulate(stated())
        b = formulate(stated(dict(reversed(list(FACTS.items())))))
        assert [(f.key, f.value, f.basis) for f in a.state.facts] == [
            (f.key, f.value, f.basis) for f in b.state.facts
        ]
        assert a.record is not None and b.record is not None
        assert a.record.input_sha256 == b.record.input_sha256  # same problem, different history
        assert a.record.ledger_head != b.record.ledger_head  # history is part of the record
        assert formulate(stated()).state.state_sha256 == a.state.state_sha256

    def test_tampered_ledger_is_rejected(self) -> None:
        led = stated()
        e = led.entries[3]
        forged = Ledger((*led.entries[:3], e.model_copy(update={"value": "999"}), *led.entries[4:]))
        with pytest.raises(FormulationError) as err:
            formulate(forged)
        assert err.value.code == "LEDGER_TAMPERED"
        with pytest.raises(FormulationError):
            Ledger(led.entries[1:]).verify()


class TestRealSolveFromFormulation:
    def test_formulated_problem_solves_and_is_admitted_by_decision_intelligence(self) -> None:
        r = formulate(stated())
        assert r.problem is not None and r.compiled is not None
        run = solve_run(
            LPOrchestrator(HighsAdapter()),
            "BASE",
            r.problem,
            r.compiled,
            policy=AdmissionPolicy.LEGACY_UNVERIFIED,
        )
        adm = admit(run)
        assert not hasattr(adm, "code"), adm
        obj = adm.metrics["objective"]  # type: ignore[union-attr]
        assert obj is not None and math.isclose(float(obj), 2600.0, abs_tol=1e-6)

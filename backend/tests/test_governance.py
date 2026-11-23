"""Governed session journey with real HiGHS: intake -> confirm -> solve -> decide -> approve."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest

from qorvexa.industrial.decision_intelligence.recommendation import RecommendationStatus
from qorvexa.industrial.formulation import ActorKind, FormulationError, FormulationStatus
from qorvexa.industrial.governance import (
    AuthorizationError,
    DecisionError,
    ExtractedClaim,
    GovernedSessions,
    Principal,
    Role,
    SessionError,
    SqliteStore,
    StoreError,
    Verdict,
    claims_from_json,
)
from qorvexa.industrial.production_planning import CapacityChange, ScenarioSpec
from qorvexa.industrial.production_planning.admission import AdmissionPolicy
from qorvexa.solver_fabric import LPOrchestrator
from qorvexa.solver_fabric.highs_adapter import HighsAdapter
from tests.test_formulation import FACTS

LEGACY = AdmissionPolicy.LEGACY_UNVERIFIED
LLM = Principal("extractor-svc", ActorKind.LLM, frozenset({Role.EXTRACTOR}))
ANA = Principal("ana", ActorKind.HUMAN, frozenset({Role.PLANNER}))
OMAR = Principal("omar", ActorKind.HUMAN, frozenset({Role.APPROVER}))
SOLO = Principal("solo", ActorKind.HUMAN, frozenset({Role.PLANNER, Role.APPROVER}))
ACK = frozenset({"CAPACITY_CHANGE_COST_NOT_MODELED", "UNIT_VALIDATION_NOT_PERFORMED"})
TEXT = "\n".join(f"{k} is {v}" for k, v in FACTS.items())


class Echo:
    """Deterministic stand-in for an LLM: emits whatever it is told, quotes included."""

    def __init__(self, claims: Sequence[ExtractedClaim]) -> None:
        self.claims = claims

    def extract(self, text: str) -> Sequence[ExtractedClaim]:
        return self.claims


def full_claims() -> list[ExtractedClaim]:
    return [ExtractedClaim(key=k, value=v, quote=f"{k} is {v}") for k, v in FACTS.items()]


@pytest.fixture
def svc(tmp_path: Path) -> GovernedSessions:
    store = SqliteStore(tmp_path / "gov.db")
    tick = iter(f"2026-01-01T00:00:{i:02d}+00:00" for i in range(60))
    return GovernedSessions(
        store, LPOrchestrator(HighsAdapter()), clock=lambda: next(tick), policy=LEGACY
    )


def ready_session(s: GovernedSessions, sid: str = "S1") -> None:
    s.create(ANA, sid, "Weekly mix")
    rep = s.ingest(LLM, sid, TEXT, Echo(full_claims()))
    assert len(rep.accepted) == len(FACTS) and rep.rejected == ()
    assert s.state(ANA, sid).status is FormulationStatus.NEEDS_CLARIFICATION
    st = s.confirm(ANA, sid, list(FACTS))
    assert st.status is FormulationStatus.READY


class TestIntakeAndAuthz:
    def test_proposals_stay_untrusted_until_confirmed(self, svc: GovernedSessions) -> None:
        svc.create(ANA, "S1", "t")
        svc.ingest(LLM, "S1", TEXT, Echo(full_claims()))
        st = svc.state(ANA, "S1")
        assert st.facts == () and st.status is FormulationStatus.NEEDS_CLARIFICATION
        with pytest.raises(SessionError):
            svc.run_decision(ANA, "S1", [])

    def test_llm_cannot_confirm_edit_or_approve(self, svc: GovernedSessions) -> None:
        svc.create(ANA, "S1", "t")
        for call in (
            lambda: svc.confirm(LLM, "S1", ["problem:id"]),
            lambda: svc.assert_fact(LLM, "S1", "problem:id", "X"),
            lambda: svc.decide_on(
                LLM, "S1", "0" * 64, Verdict.APPROVED, "A", frozenset(), "x" * 12
            ),
            lambda: svc.create(LLM, "S2", "t"),
        ):
            with pytest.raises(AuthorizationError) as e:
                call()
            assert e.value.code == "FORBIDDEN"

    def test_planner_cannot_ingest_or_approve(self, svc: GovernedSessions) -> None:
        svc.create(ANA, "S1", "t")
        with pytest.raises(AuthorizationError):
            svc.ingest(ANA, "S1", TEXT, Echo([]))
        with pytest.raises(AuthorizationError):
            svc.decide_on(ANA, "S1", "0" * 64, Verdict.REJECTED, None, frozenset(), "x" * 12)

    def test_principal_construction_rules(self) -> None:
        with pytest.raises(AuthorizationError):
            Principal("bot", ActorKind.LLM, frozenset({Role.APPROVER}))
        with pytest.raises(AuthorizationError):
            Principal("ana", ActorKind.HUMAN, frozenset({Role.EXTRACTOR}))
        with pytest.raises(AuthorizationError):
            Principal(" ", ActorKind.HUMAN, frozenset({Role.PLANNER}))

    def test_ungrounded_claims_are_rejected_with_reasons(self, svc: GovernedSessions) -> None:
        svc.create(ANA, "S1", "t")
        claims = [
            ExtractedClaim(key="resource:LABOR:capacity", value="80", quote="not in the text"),
            ExtractedClaim(
                key="resource:LABOR:capacity", value="90", quote="resource:LABOR:capacity is 80"
            ),
            ExtractedClaim(
                key="resource:LABOR:capacity", value="-5", quote="resource:LABOR:capacity is 80"
            ),
            ExtractedClaim(key="bogus:key", value="1", quote="resource:LABOR:capacity is 80"),
            ExtractedClaim(
                key="resource:LABOR:capacity", value="80", quote="resource:LABOR:capacity is 80"
            ),
        ]
        rep = svc.ingest(LLM, "S1", TEXT, Echo(claims))
        assert [r for _, r in rep.rejected] == [
            "QUOTE_NOT_IN_SOURCE",
            "VALUE_NOT_IN_QUOTE",
            "INVALID_VALUE",
            "INVALID_KEY",
        ]
        assert len(rep.accepted) == 1
        assert rep.ledger.entries[0].source_ref.startswith("text:")

    def test_malformed_llm_output_is_refused(self) -> None:
        with pytest.raises(FormulationError):
            claims_from_json({"not": "a list"})
        with pytest.raises(FormulationError):
            claims_from_json([{"key": "x", "value": 1.5, "quote": "q", "extra": 1}])


class TestJourney:
    def test_end_to_end_with_real_solver_and_approval(self, svc: GovernedSessions) -> None:
        ready_session(svc)
        brief = svc.run_decision(
            ANA,
            "S1",
            [
                ScenarioSpec(
                    scenario_id="MACH120",
                    changes=(CapacityChange(resource_id="MACHINE", capacity="120"),),
                )
            ],
        )
        rec = brief.recommendation
        assert rec.status is RecommendationStatus.CONDITIONALLY_RECOMMENDED
        assert rec.recommended_scenario_id == "MACH120" and rec.human_approval_required
        args = (OMAR, "S1", brief.brief_sha256, Verdict.APPROVED, "MACH120")
        with pytest.raises(DecisionError) as e:
            svc.decide_on(*args, frozenset(), "capacity purchase reviewed")
        assert e.value.code == "ASSUMPTIONS_NOT_ACKNOWLEDGED"
        with pytest.raises(DecisionError) as e2:
            svc.decide_on(
                OMAR,
                "S1",
                brief.brief_sha256,
                Verdict.APPROVED,
                "BASE",
                ACK,
                "capacity purchase reviewed",
            )
        assert e2.value.code == "WRONG_SCENARIO"
        r = svc.decide_on(*args, ACK, "capacity purchase reviewed with finance")
        assert r.verdict is Verdict.APPROVED and not r.self_approved
        assert r.recorded_at.startswith("2026-01-01") and r.approver_id == "omar"
        with pytest.raises(DecisionError) as e3:
            svc.decide_on(*args, ACK, "capacity purchase reviewed again")
        assert e3.value.code == "ALREADY_DECIDED"
        assert len(svc.history("S1").records) == 1

    def test_self_approval_blocked_unless_explicitly_allowed(self, tmp_path: Path) -> None:
        store = SqliteStore(tmp_path / "g.db")
        for allow in (False, True):
            s = GovernedSessions(
                store, LPOrchestrator(HighsAdapter()), allow_self_approval=allow, policy=LEGACY
            )
            sid = f"S{allow}"
            ready_session(s, sid)
            b = s.run_decision(
                SOLO,
                sid,
                [
                    ScenarioSpec(
                        scenario_id="M",
                        changes=(CapacityChange(resource_id="MACHINE", capacity="120"),),
                    )
                ],
            )
            args = (SOLO, sid, b.brief_sha256, Verdict.APPROVED, "M", ACK, "solo operator review")
            if allow:
                assert s.decide_on(*args).self_approved is True
            else:
                with pytest.raises(DecisionError) as e:
                    s.decide_on(*args)
                assert e.value.code == "SELF_APPROVAL"

    def test_rejection_is_recorded_and_needs_a_reason(self, svc: GovernedSessions) -> None:
        ready_session(svc)
        b = svc.run_decision(ANA, "S1", [])
        with pytest.raises(DecisionError) as e:
            svc.decide_on(OMAR, "S1", b.brief_sha256, Verdict.REJECTED, None, frozenset(), "no")
        assert e.value.code == "REASON_REQUIRED"
        r = svc.decide_on(
            OMAR, "S1", b.brief_sha256, Verdict.REJECTED, None, frozenset(), "not enough scenarios"
        )
        assert r.verdict is Verdict.REJECTED

    def test_unready_formulation_cannot_be_solved(self, svc: GovernedSessions) -> None:
        svc.create(ANA, "S1", "t")
        svc.assert_fact(ANA, "S1", "problem:id", "X1")
        with pytest.raises(SessionError) as e:
            svc.run_decision(ANA, "S1", [])
        assert e.value.code == "NOT_READY"
        ready_session(svc, "S2")
        with pytest.raises(SessionError):
            svc.run_decision(
                ANA,
                "S2",
                [
                    ScenarioSpec(
                        scenario_id="BASE",
                        changes=(CapacityChange(resource_id="MACHINE", capacity="120"),),
                    )
                ],
            )


class TestPersistenceAndTampering:
    def test_state_survives_reload_and_stale_head_is_rejected(self, tmp_path: Path) -> None:
        store = SqliteStore(tmp_path / "p.db")
        s = GovernedSessions(store, LPOrchestrator(HighsAdapter()), policy=LEGACY)
        ready_session(s)
        again = SqliteStore(tmp_path / "p.db").load_ledger("S1")
        assert len(again.entries) == 2 * len(FACTS)
        with pytest.raises(StoreError) as e:
            store.save_ledger("S1", again, "0" * 64, "t")
        assert e.value.code == "STALE_HEAD"
        with pytest.raises(StoreError) as e2:
            store.load_ledger("nope")
        assert e2.value.code == "UNKNOWN_SESSION"

    def test_tampered_ledger_row_is_detected_on_load(self, tmp_path: Path) -> None:
        store = SqliteStore(tmp_path / "t.db")
        s = GovernedSessions(store, LPOrchestrator(HighsAdapter()), policy=LEGACY)
        ready_session(s)
        with sqlite3.connect(tmp_path / "t.db") as c:
            c.execute(
                'UPDATE ledger_entries SET body=replace(body,\'"value":"80"\',\'"value":"8000"\') '
                "WHERE body LIKE '%LABOR:capacity%'"
            )
        with pytest.raises(FormulationError) as e:
            s.state(ANA, "S1")
        assert e.value.code == "LEDGER_TAMPERED"

    def test_tampered_decision_history_and_brief_are_detected(self, tmp_path: Path) -> None:
        store = SqliteStore(tmp_path / "d.db")
        s = GovernedSessions(store, LPOrchestrator(HighsAdapter()), policy=LEGACY)
        ready_session(s)
        b = s.run_decision(
            ANA,
            "S1",
            [
                ScenarioSpec(
                    scenario_id="M",
                    changes=(CapacityChange(resource_id="MACHINE", capacity="120"),),
                )
            ],
        )
        s.decide_on(OMAR, "S1", b.brief_sha256, Verdict.APPROVED, "M", ACK, "reviewed with finance")
        with sqlite3.connect(tmp_path / "d.db") as c:
            c.execute("UPDATE decisions SET body=replace(body,'omar','mallory')")
        with pytest.raises(DecisionError) as e:
            s.history("S1")
        assert e.value.code == "HISTORY_TAMPERED"
        with sqlite3.connect(tmp_path / "d.db") as c:
            c.execute("UPDATE briefs SET body=replace(body,'MACH','XXXX')")
            c.execute("DELETE FROM decisions")
        with pytest.raises(DecisionError) as e2:
            s.decide_on(
                OMAR, "S1", b.brief_sha256, Verdict.APPROVED, "M", ACK, "second attempt here"
            )
        assert e2.value.code == "BRIEF_TAMPERED"

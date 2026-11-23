"""Governed decision session: the single orchestration entry point the API layer should call.

Journey: text/claims -> proposals -> human confirmation -> READY formulation -> real solve
(existing LPOrchestrator) -> scenarios -> Decision Intelligence brief -> human approval -> audit.
Every operation checks the Principal's role; all state is persisted and re-verified on load.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from qorvexa.industrial.decision_intelligence import DecisionConfig, decide, solve_run
from qorvexa.industrial.decision_intelligence.explanation import DecisionBrief
from qorvexa.industrial.formulation import (
    FormulationResult,
    FormulationState,
    Ledger,
    analyze,
    apply_defaults,
    confirm_keys,
    convert_units,
    formulate,
)
from qorvexa.industrial.governance.admission import admit_ledger_formulation
from qorvexa.industrial.governance.authz import Principal, Role
from qorvexa.industrial.governance.decisions import DecisionHistory, DecisionRecord, Verdict
from qorvexa.industrial.governance.extraction import Extractor, IngestReport, ingest_text
from qorvexa.industrial.governance.store import SqliteStore
from qorvexa.industrial.production_planning import ScenarioSpec, compile_scenario
from qorvexa.industrial.production_planning.admission import AdmissionPolicy, admit_for_solve
from qorvexa.solver_fabric import LPOrchestrator

BASE_ID = "BASE"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class GovernedSessions:
    def __init__(
        self,
        store: SqliteStore,
        orchestrator: LPOrchestrator,
        *,
        clock: Callable[[], str] = utc_now,
        allow_self_approval: bool = False,
        policy: AdmissionPolicy = AdmissionPolicy.STRICT,
        require_units: bool | None = None,  # deprecated alias: True=STRICT, False=LEGACY
    ) -> None:
        self._store = store
        self._orch = orchestrator
        self._clock = clock
        self._self_ok = allow_self_approval
        if require_units is not None:
            policy = AdmissionPolicy.STRICT if require_units else AdmissionPolicy.LEGACY_UNVERIFIED
        self._policy = policy

    # ---- formulation
    def create(self, p: Principal, session_id: str, title: str) -> None:
        p.require(Role.PLANNER)
        self._store.create_session(session_id, title, p.id, self._clock())

    def _edit(self, p: Principal, sid: str, fn: Callable[[Ledger], Ledger]) -> FormulationState:
        p.require(Role.PLANNER)
        led = self._store.load_ledger(sid)
        new = fn(led)
        self._store.save_ledger(sid, new, led.head, self._clock())
        return analyze(new)

    def state(self, p: Principal, sid: str) -> FormulationState:
        if not p.roles & {Role.VIEWER, Role.PLANNER, Role.APPROVER}:
            p.require(Role.VIEWER)
        return analyze(self._store.load_ledger(sid))

    def ingest(self, service: Principal, sid: str, text: str, extractor: Extractor) -> IngestReport:
        service.require(Role.EXTRACTOR)
        led = self._store.load_ledger(sid)
        report = ingest_text(led, text, extractor, service)
        self._store.save_ledger(sid, report.ledger, led.head, self._clock())
        return report

    def convert_units(
        self, p: Principal, sid: str, slot: str, to_unit: str, rationale: str
    ) -> FormulationState:
        """A planner approves one exact unit conversion (recorded as an immutable event)."""
        return self._edit(
            p,
            sid,
            lambda ledger: convert_units(ledger, slot, to_unit, p.id, rationale, self._clock()),
        )

    def assert_fact(self, p: Principal, sid: str, key: str, value: str | int) -> FormulationState:
        return self._edit(p, sid, lambda ledger: ledger.assert_fact(key, value, p.id))

    def confirm(self, p: Principal, sid: str, keys: list[str]) -> FormulationState:
        return self._edit(p, sid, lambda ledger: confirm_keys(ledger, keys, p.id))

    def assume_defaults(self, p: Principal, sid: str, rationale: str) -> FormulationState:
        return self._edit(p, sid, lambda ledger: apply_defaults(ledger, p.id, rationale))

    def retract(self, p: Principal, sid: str, seq: int) -> FormulationState:
        return self._edit(p, sid, lambda ledger: ledger.retract(seq, p.id))

    def resolve(self, p: Principal, sid: str, winner_seq: int) -> FormulationState:
        return self._edit(p, sid, lambda ledger: ledger.resolve(winner_seq, p.id))

    def build(self, p: Principal, sid: str) -> FormulationResult:
        p.require(Role.PLANNER)
        return formulate(self._store.load_ledger(sid))

    # ---- decision
    def run_decision(
        self,
        p: Principal,
        sid: str,
        scenarios: Sequence[ScenarioSpec],
        config: DecisionConfig | None = None,
    ) -> DecisionBrief:
        p.require(Role.PLANNER)
        built = formulate(self._store.load_ledger(sid))
        if built.problem is None or built.compiled is None or built.record is None:
            raise SessionError("NOT_READY", f"formulation is {built.state.status.value}")
        ev, pol = built.unit_evidence, self._policy
        # fail-closed: nothing is solved unless the ledger-level admission succeeds
        adm = admit_ledger_formulation(self._store.load_ledger(sid), built, pol)
        runs = [
            solve_run(
                self._orch,
                BASE_ID,
                built.problem,
                built.compiled,
                units=ev,
                policy=pol,
                admission=adm,
            )
        ]
        for spec in scenarios:
            if spec.scenario_id == BASE_ID:
                raise SessionError("RESERVED_ID", "scenario id BASE is reserved")
            cs = compile_scenario(built.problem, spec)
            sadm = admit_for_solve(
                cs.problem, cs.formulation, policy=pol, units=ev, provenance=adm.provenance
            )
            runs.append(
                solve_run(
                    self._orch,
                    spec.scenario_id,
                    cs.problem,
                    cs.formulation,
                    units=ev,
                    policy=pol,
                    admission=sadm,
                )
            )
        cfg = (config or DecisionConfig()).model_copy(
            update={
                "admission_policy": pol,
                "require_unit_evidence": pol is AdmissionPolicy.STRICT,
            }
        )
        brief = decide(runs[0], runs[1:], cfg)
        self._store.save_brief(sid, brief, built.record.record_sha256, p.id, self._clock())
        return brief

    def decide_on(
        self,
        p: Principal,
        sid: str,
        brief_sha256: str,
        verdict: Verdict,
        scenario_id: str | None,
        acknowledged: frozenset[str],
        reason: str,
    ) -> DecisionRecord:
        p.require(Role.APPROVER)
        brief, form_sha, created_by = self._store.get_brief(sid, brief_sha256)
        hist = self._store.load_history(sid)
        new = hist.record(
            brief=brief,
            session_id=sid,
            formulation_record_sha256=form_sha,
            principal=p,
            verdict=verdict,
            scenario_id=scenario_id,
            acknowledged=acknowledged,
            reason=reason,
            recorded_at=self._clock(),
            brief_created_by=created_by,
            allow_self_approval=self._self_ok,
        )
        self._store.save_history(sid, new, hist.head)
        return new.records[-1]

    def history(self, sid: str) -> DecisionHistory:
        return self._store.load_history(sid)


class SessionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message

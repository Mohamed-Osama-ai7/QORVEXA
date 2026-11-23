"""Ledger-level admission: the formulation must be fresh, ledger-verified and human-confirmed."""

from __future__ import annotations

from qorvexa.industrial.formulation import ActorKind, FormulationResult, Ledger, Op, formulate
from qorvexa.industrial.formulation.ledger import FormulationError
from qorvexa.industrial.production_planning.admission import (
    AdmissionError,
    AdmissionPolicy,
    AdmissionProvenance,
    AdmissionRecord,
    admit_for_solve,
)


def admit_ledger_formulation(
    ledger: Ledger, built: FormulationResult, policy: AdmissionPolicy
) -> AdmissionRecord:
    """Admit a built formulation only if it is exactly what the verified ledger yields now."""
    try:
        ledger.verify()
    except FormulationError as exc:
        raise AdmissionError(exc.code, exc.message) from exc
    fresh = formulate(ledger)
    if fresh.problem is None or fresh.compiled is None or fresh.record is None:
        raise AdmissionError("NOT_READY", f"formulation is {fresh.state.status.value}")
    if (
        built.record is None
        or built.compiled is None
        or built.record.record_sha256 != fresh.record.record_sha256
        or built.compiled.spec_json != fresh.compiled.spec_json
    ):
        raise AdmissionError("STALE_FORMULATION", "the ledger changed after this build")
    rec = fresh.record
    live = {e.seq: e for e in ledger.entries if ledger.is_active(e.seq)}
    for slot, src, dst, factor, actor, _why, at in rec.conversions:
        match = [
            e
            for e in live.values()
            if e.op is Op.CONVERT
            and (e.key, e.unit_from, e.value, e.factor, e.actor.id, e.at)
            == (slot, src, dst, factor, actor, at)
            and e.actor.kind is ActorKind.HUMAN
        ]
        if not match:
            raise AdmissionError("UNCONFIRMED_CONVERSION", f"no human-approved event for {slot}")
    for key, seq, _basis in rec.unit_claims:
        entry = live.get(seq)
        if entry is None or entry.key != key:
            raise AdmissionError("UNIT_CLAIM_LINEAGE", f"claim {seq} for {key} is not active")
    prov = AdmissionProvenance(
        formulation_record_sha256=rec.record_sha256,
        ledger_head=ledger.head,
        conversions=rec.conversions,
        unit_claims=rec.unit_claims,
    )
    return admit_for_solve(
        fresh.problem,
        fresh.compiled,
        policy=policy,
        units=fresh.unit_evidence,
        provenance=prov,
    )

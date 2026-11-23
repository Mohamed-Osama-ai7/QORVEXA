"""Integrity verification: hash chain, sequence, factory binding, replayed graph invariants."""

from __future__ import annotations

from pydantic import BaseModel

from qorvexa.industrial.ontology.graph import GENESIS, GraphState, IntegrityIssue
from qorvexa.industrial.ontology.model import Event
from qorvexa.industrial.ontology.schema import Ontology


class IntegrityReport(BaseModel):
    factory_id: str
    ok: bool
    revision: int
    head_sha: str
    graph_sha256: str
    chain_errors: tuple[str, ...] = ()
    graph_issues: tuple[IntegrityIssue, ...] = ()


def verify_events(factory_id: str, events: list[Event]) -> list[str]:
    """Recompute every hash and check sequence, factory binding and linkage."""
    errors: list[str] = []
    prev = GENESIS
    for i, ev in enumerate(events, start=1):
        if ev.seq != i:
            errors.append(f"seq {ev.seq} at position {i}")
        if ev.factory_id != factory_id:
            errors.append(f"seq {ev.seq}: belongs to factory {ev.factory_id}")
        if ev.prev_sha != prev:
            errors.append(f"seq {ev.seq}: broken chain link")
        if ev.sha != ev.body_sha256():
            errors.append(f"seq {ev.seq}: content does not match its hash")
        prev = ev.sha
    return errors


def verify_factory(factory_id: str, events: list[Event], ontology: Ontology) -> IntegrityReport:
    errs = verify_events(factory_id, events)
    state = GraphState.from_events(factory_id, events)
    issues = [i for i in state.integrity_issues(ontology) if i.code != "STALE_SCHEMA"]
    return IntegrityReport(
        factory_id=factory_id,
        ok=not errs and not issues,
        revision=state.revision,
        head_sha=state.head_sha,
        graph_sha256=state.sha256(),
        chain_errors=tuple(errs),
        graph_issues=tuple(issues),
    )

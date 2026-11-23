"""Folded factory graph at one revision: typed state, deterministic queries, structural integrity.

``GraphState`` is rebuilt purely from the append-only events (``from_events``); nothing else is
a source of truth. Read access never crosses factories because a state is bound to one factory.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel

from qorvexa.industrial.ontology.model import (
    Action,
    DisputeRecord,
    EntityRecord,
    Event,
    Kind,
    RelationRecord,
    Status,
)
from qorvexa.industrial.ontology.schema import Ontology

GENESIS = "0" * 64
AUTHORITATIVE = frozenset({Status.CONFIRMED})


def ts(text: str) -> datetime:
    d = datetime.fromisoformat(text)
    return d if d.tzinfo else d.replace(tzinfo=UTC)


class Direction(StrEnum):
    OUT = "OUT"
    IN = "IN"
    BOTH = "BOTH"


class IntegrityIssue(BaseModel):
    code: str
    object_id: str
    detail: str


class GraphState:
    def __init__(self, factory_id: str) -> None:
        self.factory_id = factory_id
        self.revision = 0
        self.head_sha = GENESIS
        self.entities: dict[str, EntityRecord] = {}
        self.relations: dict[str, RelationRecord] = {}
        self.disputes: dict[str, DisputeRecord] = {}
        self._out: dict[str, list[str]] | None = None
        self._in: dict[str, list[str]] | None = None

    # ------------------------------------------------------------------ folding
    @classmethod
    def from_events(
        cls, factory_id: str, events: Iterable[Event], *, up_to: int | None = None
    ) -> GraphState:
        st = cls(factory_id)
        for ev in events:
            if up_to is not None and ev.seq > up_to:
                break
            st.apply(ev)
        return st

    def apply(self, ev: Event) -> None:
        self._out = self._in = None
        self.revision, self.head_sha = ev.seq, ev.sha
        if ev.kind is Kind.ENTITY and ev.entity is not None:
            old = self.entities.get(ev.object_id)
            self.entities[ev.object_id] = EntityRecord(
                entity_id=ev.object_id,
                factory_id=ev.factory_id,
                data=ev.entity,
                status=ev.status,
                basis=ev.basis,
                version=ev.object_version,
                created_seq=old.created_seq if old else ev.seq,
                updated_seq=ev.seq,
                created_by=old.created_by if old else ev.actor_id,
                updated_by=ev.actor_id,
                evidence=_merge(old.evidence if old else (), ev.evidence),
                disputed=old.disputed if old else False,
            )
        elif ev.kind is Kind.RELATION and ev.relation is not None:
            old_r = self.relations.get(ev.object_id)
            self.relations[ev.object_id] = RelationRecord(
                relation_id=ev.object_id,
                factory_id=ev.factory_id,
                data=ev.relation,
                status=ev.status,
                basis=ev.basis,
                version=ev.object_version,
                created_seq=old_r.created_seq if old_r else ev.seq,
                updated_seq=ev.seq,
                created_by=old_r.created_by if old_r else ev.actor_id,
                updated_by=ev.actor_id,
                evidence=_merge(old_r.evidence if old_r else (), ev.evidence),
                disputed=old_r.disputed if old_r else False,
            )
        elif ev.kind is Kind.DISPUTE and ev.dispute is not None:
            if ev.action is Action.DISPUTE:
                self.disputes[ev.object_id] = DisputeRecord(
                    dispute_id=ev.object_id,
                    factory_id=ev.factory_id,
                    subject_id=ev.dispute.subject_id,
                    reason=ev.dispute.reason,
                    raised_by=ev.actor_id,
                    raised_seq=ev.seq,
                    evidence=ev.evidence,
                )
            else:
                d = self.disputes[ev.object_id]
                self.disputes[ev.object_id] = d.model_copy(
                    update={
                        "open": False,
                        "resolved_by": ev.actor_id,
                        "resolved_seq": ev.seq,
                        "resolution": ev.dispute.resolution,
                    }
                )
            self._refresh_disputed(ev.dispute.subject_id)

    def _refresh_disputed(self, subject_id: str) -> None:
        flag = any(d.open and d.subject_id == subject_id for d in self.disputes.values())
        if subject_id in self.entities:
            self.entities[subject_id] = self.entities[subject_id].model_copy(
                update={"disputed": flag}
            )
        elif subject_id in self.relations:
            self.relations[subject_id] = self.relations[subject_id].model_copy(
                update={"disputed": flag}
            )

    # ------------------------------------------------------------------ adjacency
    def _index(self) -> None:
        out: dict[str, list[str]] = defaultdict(list)
        inn: dict[str, list[str]] = defaultdict(list)
        for rid in sorted(self.relations):
            r = self.relations[rid].data
            out[r.source].append(rid)
            inn[r.target].append(rid)
        self._out, self._in = out, inn

    def relations_of(self, entity_id: str, direction: Direction) -> list[RelationRecord]:
        if self._out is None or self._in is None:
            self._index()
        out_idx, in_idx = self._out or {}, self._in or {}
        ids: list[str] = []
        if direction in (Direction.OUT, Direction.BOTH):
            ids += out_idx.get(entity_id, [])
        if direction in (Direction.IN, Direction.BOTH):
            ids += in_idx.get(entity_id, [])
        return [self.relations[i] for i in sorted(set(ids))]

    # ------------------------------------------------------------------ temporal filters
    @staticmethod
    def window_contains(valid_from: str | None, valid_until: str | None, at: str) -> bool:
        t = ts(at)
        if valid_from is not None and ts(valid_from) > t:
            return False
        return not (valid_until is not None and ts(valid_until) <= t)

    def entity_current(
        self, rec: EntityRecord, *, valid_at: str | None, statuses: frozenset[Status]
    ) -> bool:
        return _current(rec.status, rec.data.valid_from, rec.data.valid_until, valid_at, statuses)

    def relation_current(
        self, rec: RelationRecord, *, valid_at: str | None, statuses: frozenset[Status]
    ) -> bool:
        return _current(rec.status, rec.data.valid_from, rec.data.valid_until, valid_at, statuses)

    # ------------------------------------------------------------------ structure helpers
    def ancestors_via(self, entity_id: str, relation: str) -> list[str]:
        """Chain of sources reached by following ``relation`` backwards (e.g. CONTAINS parents)."""
        out: list[str] = []
        cur, seen = entity_id, {entity_id}
        while True:
            parents = sorted(
                {
                    r.data.source
                    for r in self.relations_of(cur, Direction.IN)
                    if r.data.relation == relation and r.status is Status.CONFIRMED
                }
            )
            if len(parents) != 1 or parents[0] in seen:
                return out
            cur = parents[0]
            out.append(cur)
            seen.add(cur)

    def reaches(self, relation: str, start: str, goal: str, *, skip: str | None = None) -> bool:
        """Is ``goal`` reachable from ``start`` over CONFIRMED edges of ``relation``?"""
        stack, seen = [start], {start}
        while stack:
            cur = stack.pop()
            if cur == goal:
                return True
            for r in self.relations_of(cur, Direction.OUT):
                if (
                    r.data.relation == relation
                    and r.status is Status.CONFIRMED
                    and r.relation_id != skip
                    and r.data.target not in seen
                ):
                    seen.add(r.data.target)
                    stack.append(r.data.target)
        return False

    # ------------------------------------------------------------------ identity
    def sha256(self) -> str:
        """Content identity of the graph at this revision (recorded-at times excluded)."""
        body = {
            "entities": [self.entities[k].model_dump(mode="json") for k in sorted(self.entities)],
            "relations": [
                self.relations[k].model_dump(mode="json") for k in sorted(self.relations)
            ],
            "disputes": [self.disputes[k].model_dump(mode="json") for k in sorted(self.disputes)],
        }
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()

    # ------------------------------------------------------------------ integrity
    def integrity_issues(self, ontology: Ontology) -> list[IntegrityIssue]:
        issues: list[IntegrityIssue] = []

        def add(code: str, oid: str, detail: str) -> None:
            issues.append(IntegrityIssue(code=code, object_id=oid, detail=detail))

        for eid, e in sorted(self.entities.items()):
            if not ontology.has_concept(e.data.concept):
                add("UNKNOWN_CONCEPT", eid, e.data.concept)
                continue
            if e.authoritative and not e.evidence:
                add("NO_EVIDENCE", eid, "authoritative entity without evidence")
            for k, v in (("site_id", e.data.scope.site_id), ("line_id", e.data.scope.line_id)):
                if v is not None and v not in self.entities:
                    add("DANGLING_SCOPE", eid, f"{k}={v}")
            if ontology.concept(e.data.concept).version > e.data.schema_version:
                add("STALE_SCHEMA", eid, f"recorded at schema v{e.data.schema_version}")
        seen: dict[tuple[str, str, str], str] = {}
        for rid, r in sorted(self.relations.items()):
            d = r.data
            src, tgt = self.entities.get(d.source), self.entities.get(d.target)
            if src is None or tgt is None:
                add("ORPHAN_RELATION", rid, f"{d.source}->{d.target}")
                continue
            try:
                ok = ontology.endpoint_ok(d.relation, src.data.concept, tgt.data.concept)
            except Exception:  # unknown relation id
                add("UNKNOWN_RELATION", rid, d.relation)
                continue
            if not ok:
                add(
                    "ENDPOINT_INCOMPATIBLE",
                    rid,
                    f"{src.data.concept}-{d.relation}->{tgt.data.concept}",
                )
            if r.authoritative and not r.evidence:
                add("NO_EVIDENCE", rid, "authoritative relation without evidence")
            if r.authoritative and not (src.authoritative and tgt.authoritative):
                add("AUTHORITY_ON_NON_AUTHORITY", rid, "confirmed relation on unconfirmed endpoint")
            if r.status in (Status.CONFIRMED, Status.PROPOSED):
                key = (d.relation, d.source, d.target)
                if key in seen:
                    add("DUPLICATE_RELATION", rid, f"same as {seen[key]}")
                seen[key] = rid
        for rel in ontology.relations:
            confirmed = [
                r
                for r in self.relations.values()
                if r.data.relation == rel.relation_id and r.status is Status.CONFIRMED
            ]
            if rel.acyclic:
                for r in sorted(confirmed, key=lambda x: x.relation_id):
                    if r.data.source == r.data.target or self.reaches(
                        rel.relation_id, r.data.target, r.data.source, skip=None
                    ):
                        add("CYCLE", r.relation_id, rel.relation_id)
            for limit, attr in ((rel.max_out, "source"), (rel.max_in, "target")):
                if limit is None:
                    continue
                groups: dict[str, list[RelationRecord]] = defaultdict(list)
                for r in confirmed:
                    groups[getattr(r.data, attr)].append(r)
                for key_id, grp in sorted(groups.items()):
                    if _max_overlap(grp) > limit:
                        add("CARDINALITY", key_id, f"{rel.relation_id} exceeds {limit} ({attr})")
        return issues


def _max_overlap(rels: list[RelationRecord]) -> int:
    """Largest number of simultaneously valid edges (unbounded windows overlap everything)."""
    pts = sorted(
        {ts(x).timestamp() for r in rels for x in (r.data.valid_from, r.data.valid_until) if x}
    )
    probes = [pts[0] - 1] if pts else [0.0]
    probes += pts
    best = 0
    for t in probes:
        n = 0
        for r in rels:
            lo, hi = r.data.valid_from, r.data.valid_until
            if (lo is None or ts(lo).timestamp() <= t) and (hi is None or ts(hi).timestamp() > t):
                n += 1
        best = max(best, n)
    return best


def _merge(a: tuple, b: tuple) -> tuple:  # type: ignore[type-arg]
    out = list(a)
    for x in b:
        if x not in out:
            out.append(x)
    return tuple(out)


def _current(
    status: Status,
    valid_from: str | None,
    valid_until: str | None,
    valid_at: str | None,
    statuses: frozenset[Status],
) -> bool:
    if valid_at is None:
        return status in statuses
    eff = statuses | ({Status.RETIRED} if Status.CONFIRMED in statuses else set())
    return status in eff and GraphState.window_contains(valid_from, valid_until, valid_at)

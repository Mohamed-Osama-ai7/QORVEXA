"""Deterministic, bounded, provenance-aware context retrieval.

The engine returns a compact subgraph (never "the whole factory"): typed traversal with relation,
concept and scope filters, depth/node/edge/expansion budgets, explicit truncation flags, stable
ordering, cycle protection, and the evidence that supports every returned fact. By default only
CONFIRMED, undisputed, currently valid records are returned; what was excluded is counted so the
consumer can see the limits of the answer.

``ContextRetriever`` and ``CandidateRanker`` are the extension points for future semantic
retrieval; only the deterministic implementation exists today (no embeddings by design).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, deque
from collections.abc import Sequence
from typing import Final, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from qorvexa.industrial.ontology.graph import Direction, GraphState, ts
from qorvexa.industrial.ontology.model import (
    Basis,
    EntityRecord,
    EvidenceRef,
    RelationRecord,
    Scope,
    Status,
)
from qorvexa.industrial.ontology.schema import Ontology, Value

F = ConfigDict(frozen=True, extra="forbid")
MAX_ROOTS: Final = 50
MAX_DEPTH: Final = 6
MAX_NODES: Final = 200
MAX_EDGES: Final = 500
MAX_EXPANSIONS: Final = 5000


class RetrievalError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ContextQuery(BaseModel):
    model_config = F
    roots: tuple[str, ...] = Field(min_length=1, max_length=MAX_ROOTS)
    relations: tuple[str, ...] = Field(default=(), max_length=40)
    direction: Direction = Direction.BOTH
    max_depth: int = Field(default=2, ge=0, le=MAX_DEPTH)
    max_nodes: int = Field(default=50, ge=1, le=MAX_NODES)
    max_edges: int = Field(default=100, ge=0, le=MAX_EDGES)
    concepts: tuple[str, ...] = Field(default=(), max_length=40)
    site_id: str | None = Field(default=None, max_length=40)
    line_id: str | None = Field(default=None, max_length=40)
    department: str | None = Field(default=None, max_length=100)
    valid_at: str | None = Field(default=None, max_length=40)
    include_proposed: bool = False
    include_disputed: bool = False
    include_hypothetical: bool = False
    include_retired: bool = False

    @classmethod
    def parse(cls, raw: object) -> ContextQuery:
        try:
            q = cls.model_validate(raw)
            if q.valid_at is not None:
                ts(q.valid_at)
            return q
        except (ValidationError, ValueError) as exc:
            raise RetrievalError("INVALID_QUERY", str(exc)[:300]) from exc


class NodeView(BaseModel):
    model_config = F
    entity_id: str
    concept: str
    name: str
    status: Status
    basis: Basis
    version: int
    attrs: dict[str, Value]
    scope: Scope
    aliases: tuple[str, ...]
    valid_from: str | None
    valid_until: str | None
    disputed: bool
    evidence: tuple[EvidenceRef, ...]
    created_by: str
    depth: int


class EdgeView(BaseModel):
    model_config = F
    relation_id: str
    relation: str
    source: str
    target: str
    status: Status
    basis: Basis
    version: int
    attrs: dict[str, Value]
    valid_from: str | None
    valid_until: str | None
    disputed: bool
    evidence: tuple[EvidenceRef, ...]
    created_by: str


class ContextResult(BaseModel):
    model_config = F
    factory_id: str
    revision: int
    head_sha: str
    roots: tuple[str, ...]
    nodes: tuple[NodeView, ...]
    edges: tuple[EdgeView, ...]
    truncated: bool
    truncation_reasons: tuple[str, ...]
    excluded: dict[str, int]
    limitations: tuple[str, ...]
    result_sha256: str = ""


class Gap(BaseModel):
    model_config = F
    code: str
    entity_id: str
    detail: str


class Unresolved(BaseModel):
    model_config = F
    proposed_entities: tuple[str, ...]
    proposed_relations: tuple[str, ...]
    disputed: tuple[str, ...]
    open_disputes: tuple[str, ...]
    gaps: tuple[Gap, ...]


class ContextRetriever(Protocol):
    def retrieve(self, query: ContextQuery) -> ContextResult: ...


class CandidateRanker(Protocol):
    """Future hook (e.g. semantic ranking of candidate ids); never replaces the typed filters."""

    def rank(self, text: str, candidate_ids: Sequence[str]) -> list[str]: ...


class IdentityRanker:
    def rank(self, text: str, candidate_ids: Sequence[str]) -> list[str]:
        return sorted(candidate_ids)


def _node(rec: EntityRecord, depth: int) -> NodeView:
    d = rec.data
    return NodeView(
        entity_id=rec.entity_id,
        concept=d.concept,
        name=d.name,
        status=rec.status,
        basis=rec.basis,
        version=rec.version,
        attrs=dict(sorted(d.attrs.items())),
        scope=d.scope,
        aliases=d.aliases if rec.authoritative else (),
        valid_from=d.valid_from,
        valid_until=d.valid_until,
        disputed=rec.disputed,
        evidence=rec.evidence,
        created_by=rec.created_by,
        depth=depth,
    )


def _edge(rec: RelationRecord) -> EdgeView:
    d = rec.data
    return EdgeView(
        relation_id=rec.relation_id,
        relation=d.relation,
        source=d.source,
        target=d.target,
        status=rec.status,
        basis=rec.basis,
        version=rec.version,
        attrs=dict(sorted(d.attrs.items())),
        valid_from=d.valid_from,
        valid_until=d.valid_until,
        disputed=rec.disputed,
        evidence=rec.evidence,
        created_by=rec.created_by,
    )


class ContextEngine:
    """Retrieval over one factory state (the view an authorized principal was given)."""

    def __init__(self, state: GraphState, ontology: Ontology) -> None:
        self.state, self.ontology = state, ontology

    # ------------------------------------------------------------------ core traversal
    def _statuses(self, q: ContextQuery) -> frozenset[Status]:
        s = {Status.CONFIRMED}
        if q.include_proposed:
            s.add(Status.PROPOSED)
        if q.include_hypothetical:
            s |= {Status.HYPOTHETICAL, Status.ASSUMED}
        if q.include_retired:
            s.add(Status.RETIRED)
        return frozenset(s)

    def _scope_ok(self, e: EntityRecord, q: ContextQuery) -> bool | None:
        """True / False when scope is known; None when it cannot be verified."""
        unknown = False
        for want, have in (
            (q.site_id, self._anc(e, "Site", e.data.scope.site_id)),
            (q.line_id, self._anc(e, "ProductionLine", e.data.scope.line_id)),
        ):
            if want is None:
                continue
            if have is None:
                unknown = True
            elif have != want:
                return False
        if q.department is not None:
            if e.data.scope.department is None:
                unknown = True
            elif e.data.scope.department != q.department:
                return False
        return None if unknown else True

    def _anc(self, e: EntityRecord, concept: str, direct: str | None) -> str | None:
        if direct:
            return direct
        if self.ontology.is_a(e.data.concept, concept):
            return e.entity_id
        for a in self.state.ancestors_via(e.entity_id, "CONTAINS"):
            rec = self.state.entities.get(a)
            if rec and self.ontology.is_a(rec.data.concept, concept):
                return a
        return None

    def retrieve(self, query: ContextQuery) -> ContextResult:
        q = query
        st = self.state
        statuses = self._statuses(q)
        for r in q.relations:
            self.ontology.relation(r)  # unknown relation -> OntologyError
        for c in q.concepts:
            self.ontology.concept(c)
        excluded: Counter[str] = Counter()
        reasons: list[str] = []
        limits: list[str] = []
        roots = tuple(sorted(dict.fromkeys(q.roots)))
        nodes: dict[str, NodeView] = {}
        edges: dict[str, EdgeView] = {}
        queue: deque[tuple[str, int]] = deque()
        for rid in roots:
            rec = st.entities.get(rid)
            if rec is None:
                raise RetrievalError("UNKNOWN_ROOT", f"{rid} is not an entity of this factory")
            if rec.status is not Status.CONFIRMED:
                limits.append(f"root {rid} is {rec.status.value}, not authoritative")
            if rec.disputed:
                limits.append(f"root {rid} has an open dispute")
            nodes[rid] = _node(rec, 0)
            queue.append((rid, 0))
        expansions, truncated = 0, False
        while queue:
            cur, depth = queue.popleft()
            if depth >= q.max_depth:
                continue
            for rel in st.relations_of(cur, q.direction):
                expansions += 1
                if expansions > MAX_EXPANSIONS:
                    truncated = True
                    reasons.append("EXPANSION_BUDGET")
                    queue.clear()
                    break
                if q.relations and rel.data.relation not in q.relations:
                    continue
                if not st.relation_current(rel, valid_at=q.valid_at, statuses=statuses):
                    excluded[_why(rel.status, "relation")] += 1
                    continue
                if rel.disputed and not q.include_disputed:
                    excluded["disputed"] += 1
                    continue
                other = rel.data.target if rel.data.source == cur else rel.data.source
                oe = st.entities.get(other)
                if oe is None:
                    continue
                if other not in nodes:
                    if not st.entity_current(oe, valid_at=q.valid_at, statuses=statuses):
                        excluded[_why(oe.status, "entity")] += 1
                        continue
                    if oe.disputed and not q.include_disputed:
                        excluded["disputed"] += 1
                        continue
                    if q.concepts and not any(
                        self.ontology.is_a(oe.data.concept, c) for c in q.concepts
                    ):
                        excluded["concept_filter"] += 1
                        continue
                    ok = self._scope_ok(oe, q)
                    if ok is False:
                        excluded["out_of_scope"] += 1
                        continue
                    if ok is None:
                        excluded["scope_unverified_kept"] += 1
                    if len(nodes) >= q.max_nodes:
                        truncated = True
                        if "MAX_NODES" not in reasons:
                            reasons.append("MAX_NODES")
                        continue
                    nodes[other] = _node(oe, depth + 1)
                    queue.append((other, depth + 1))
                if rel.relation_id not in edges:
                    if len(edges) >= q.max_edges:
                        truncated = True
                        if "MAX_EDGES" not in reasons:
                            reasons.append("MAX_EDGES")
                        continue
                    edges[rel.relation_id] = _edge(rel)
        if any(n.status is not Status.CONFIRMED for n in nodes.values()) or any(
            e.status is not Status.CONFIRMED for e in edges.values()
        ):
            limits.append("result includes non-authoritative records; check each status")
        for key, msg in (
            ("proposed", "unconfirmed proposals were excluded"),
            ("disputed", "disputed records were excluded"),
            ("hypothetical", "hypothetical/assumed records were excluded"),
            ("retired", "retired or not-yet-valid records were excluded"),
            ("out_of_scope", "records outside the requested scope were excluded"),
            (
                "scope_unverified_kept",
                "some records could not be verified against the scope filter",
            ),
            ("concept_filter", "records of other concepts were excluded"),
        ):
            if excluded.get(key):
                limits.append(f"{excluded[key]} {msg}")
        if truncated:
            limits.append("result is truncated; narrow the query or raise a budget")
        ordered_nodes = tuple(sorted(nodes.values(), key=lambda n: (n.depth, n.entity_id)))
        ordered_edges = tuple(sorted(edges.values(), key=lambda e: e.relation_id))
        res = ContextResult(
            factory_id=st.factory_id,
            revision=st.revision,
            head_sha=st.head_sha,
            roots=roots,
            nodes=ordered_nodes,
            edges=ordered_edges,
            truncated=truncated,
            truncation_reasons=tuple(dict.fromkeys(reasons)),
            excluded=dict(sorted(excluded.items())),
            limitations=tuple(limits),
        )
        return res.model_copy(update={"result_sha256": _sha(res)})

    # ------------------------------------------------------------------ canned queries
    def machine_context(self, machine_id: str, *, depth: int = 2) -> ContextResult:
        return self.retrieve(
            ContextQuery(
                roots=(machine_id,),
                max_depth=depth,
                relations=(
                    "CONTAINS",
                    "LOCATED_AT",
                    "HAS_CAPACITY",
                    "PRODUCES",
                    "REQUIRES",
                    "HAS_CONSTRAINT",
                    "DEPENDS_ON",
                    "OPERATES",
                ),
            )
        )

    def line_context(self, line_id: str, *, max_nodes: int = 120) -> ContextResult:
        """The line, what it contains, and what those machines produce, consume and offer."""
        structure = self.retrieve(
            ContextQuery(
                roots=(line_id,),
                relations=("CONTAINS",),
                direction=Direction.OUT,
                max_depth=3,
                max_nodes=max_nodes,
            )
        )
        ids = tuple(n.entity_id for n in structure.nodes)[:MAX_ROOTS]
        detail = self.retrieve(
            ContextQuery(
                roots=ids,
                direction=Direction.OUT,
                max_depth=1,
                max_nodes=max_nodes,
                relations=(
                    "PRODUCES",
                    "CONSUMES",
                    "REQUIRES",
                    "HAS_CAPACITY",
                    "HAS_CONSTRAINT",
                    "DEPENDS_ON",
                    "LOCATED_AT",
                ),
            )
        )
        return _merge(
            structure, detail, roots=(line_id,), truncated_roots=len(structure.nodes) > MAX_ROOTS
        )

    def product_context(self, product_id: str) -> ContextResult:
        """Producers, consumed materials, required resources, their capacities and demand."""
        return self.retrieve(
            ContextQuery(
                roots=(product_id,),
                max_depth=2,
                relations=("PRODUCES", "CONSUMES", "REQUIRES", "HAS_CAPACITY", "HAS_DEMAND"),
            )
        )

    def resources_affecting(self, product_id: str) -> ContextResult:
        return self.retrieve(
            ContextQuery(
                roots=(product_id,),
                max_depth=2,
                direction=Direction.OUT,
                relations=("REQUIRES", "HAS_CAPACITY", "HAS_CONSTRAINT"),
            )
        )

    def dependencies(
        self, entity_id: str, *, downstream: bool = False, depth: int = 6
    ) -> ContextResult:
        """Upstream (what it depends on / what precedes it) or downstream (what depends on it)."""
        return self.retrieve(
            ContextQuery(
                roots=(entity_id,),
                max_depth=depth,
                direction=Direction.IN if downstream else Direction.OUT,
                relations=("DEPENDS_ON", "PRECEDES", "SUPPLIES"),
            )
        )

    def facts_for_problem(
        self, product_ids: Sequence[str], resource_ids: Sequence[str] = ()
    ) -> ContextResult:
        return self.retrieve(
            ContextQuery(
                roots=tuple(dict.fromkeys((*product_ids, *resource_ids))),
                max_depth=2,
                relations=(
                    "PRODUCES",
                    "CONSUMES",
                    "REQUIRES",
                    "HAS_CAPACITY",
                    "HAS_DEMAND",
                    "HAS_CONSTRAINT",
                    "CONTAINS",
                ),
            )
        )

    def evidence_for(self, object_id: str) -> tuple[EvidenceRef, ...]:
        rec = self.state.relations.get(object_id) or self.state.entities.get(object_id)
        if rec is None:
            raise RetrievalError("UNKNOWN_OBJECT", object_id)
        return rec.evidence

    # ------------------------------------------------------------------ unknown / disputed / old
    def unresolved(self) -> Unresolved:
        st = self.state
        return Unresolved(
            proposed_entities=tuple(
                sorted(i for i, e in st.entities.items() if e.status is Status.PROPOSED)
            ),
            proposed_relations=tuple(
                sorted(i for i, r in st.relations.items() if r.status is Status.PROPOSED)
            ),
            disputed=tuple(
                sorted(
                    [i for i, e in st.entities.items() if e.disputed]
                    + [i for i, r in st.relations.items() if r.disputed]
                )
            ),
            open_disputes=tuple(sorted(i for i, d in st.disputes.items() if d.open)),
            gaps=tuple(self.knowledge_gaps()),
        )

    def knowledge_gaps(self) -> list[Gap]:
        """What is NOT known (never guessed): confirmed machines without a capacity, products
        nobody is known to produce, products with no known resource requirement."""
        st, out = self.state, []

        def confirmed_edges(eid: str, rel: str, direction: Direction) -> int:
            return sum(
                1
                for r in st.relations_of(eid, direction)
                if r.data.relation == rel and r.status is Status.CONFIRMED
            )

        for eid, e in sorted(st.entities.items()):
            if e.status is not Status.CONFIRMED:
                continue
            c = e.data.concept
            if self.ontology.is_a(c, "Machine") and not confirmed_edges(
                eid, "HAS_CAPACITY", Direction.OUT
            ):
                out.append(Gap(code="NO_CAPACITY", entity_id=eid, detail="no confirmed capacity"))
            if self.ontology.is_a(c, "Product"):
                if not confirmed_edges(eid, "PRODUCES", Direction.IN):
                    out.append(Gap(code="NO_PRODUCER", entity_id=eid, detail="no known producer"))
                if not confirmed_edges(eid, "REQUIRES", Direction.OUT):
                    out.append(
                        Gap(code="NO_REQUIREMENT", entity_id=eid, detail="no known resource use")
                    )
        return out

    def outdated(self, at: str) -> tuple[str, ...]:
        """Ids whose validity ended on or before ``at``, retired records and HISTORICAL
        measurements: information that must not be read as the current operating state."""
        out: list[str] = []
        for i, e in self.state.entities.items():
            hist = e.data.attrs.get("temporal_class") == "HISTORICAL"
            ended = e.data.valid_until is not None and ts(e.data.valid_until) <= ts(at)
            if e.status is Status.RETIRED or hist or (ended and e.status is Status.CONFIRMED):
                out.append(i)
        for i, r in self.state.relations.items():
            ended = r.data.valid_until is not None and ts(r.data.valid_until) <= ts(at)
            if r.status is Status.RETIRED or (ended and r.status is Status.CONFIRMED):
                out.append(i)
        return tuple(sorted(out))


def _why(status: Status, what: str) -> str:
    if status is Status.PROPOSED:
        return "proposed"
    if status in (Status.HYPOTHETICAL, Status.ASSUMED):
        return "hypothetical"
    if status is Status.REJECTED:
        return "rejected"
    return "retired"


def _sha(res: ContextResult) -> str:
    body = res.model_dump(mode="json", exclude={"result_sha256"})
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _merge(
    a: ContextResult, b: ContextResult, *, roots: tuple[str, ...], truncated_roots: bool
) -> ContextResult:
    nodes = {n.entity_id: n for n in (*a.nodes, *b.nodes)}
    edges = {e.relation_id: e for e in (*a.edges, *b.edges)}
    reasons = [*a.truncation_reasons, *b.truncation_reasons]
    if truncated_roots:
        reasons.append("MAX_ROOTS")
    ex: Counter[str] = Counter()
    ex.update(a.excluded)
    ex.update(b.excluded)
    limits = list(dict.fromkeys((*a.limitations, *b.limitations)))
    trunc = a.truncated or b.truncated or truncated_roots
    res = ContextResult(
        factory_id=a.factory_id,
        revision=a.revision,
        head_sha=a.head_sha,
        roots=roots,
        nodes=tuple(sorted(nodes.values(), key=lambda n: (n.depth, n.entity_id))),
        edges=tuple(sorted(edges.values(), key=lambda e: e.relation_id)),
        truncated=trunc,
        truncation_reasons=tuple(dict.fromkeys(reasons)),
        excluded=dict(sorted(ex.items())),
        limitations=tuple(limits),
    )
    return res.model_copy(update={"result_sha256": _sha(res)})

"""The authenticated write/read surface for ONE factory's knowledge graph.

* Principals come only from the trusted authentication layer (``governance.authz.Principal``);
  nothing about the actor is read from a request.
* Authoritative statuses (CONFIRMED / ASSUMED / HYPOTHETICAL records, retire, reject, resolve) need
  a HUMAN principal with PLANNER or APPROVER whose ``factories`` contains this factory. LLMs and
  extraction services can only create PROPOSED records and raise disputes.
* Writes are optimistic (object version + factory revision), idempotent (key + request hash),
  schema-validated, and append one hash-chained event each.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from qorvexa.industrial.formulation import ActorKind
from qorvexa.industrial.governance.authz import AuthorizationError, Principal, Role
from qorvexa.industrial.ontology.graph import (
    GENESIS,
    GraphState,
    ts,
)
from qorvexa.industrial.ontology.model import (
    Action,
    Basis,
    DisputeData,
    DisputeRecord,
    EntityData,
    EntityRecord,
    Event,
    EvidenceRef,
    Kind,
    RelationData,
    RelationRecord,
    Scope,
    Status,
)
from qorvexa.industrial.ontology.schema import (
    ID_RE,
    MAX_STR,
    Ontology,
    OntologyError,
    Value,
    core_ontology,
)
from qorvexa.industrial.ontology.store import OntologyStore, StoreError

MAX_EVIDENCE = 20
MAX_ALIASES = 30
_STATUS_FOR_BASIS = {
    Basis.PROPOSAL: Status.PROPOSED,
    Basis.HUMAN_CONFIRMED: Status.CONFIRMED,
    Basis.IMPORTED_VERIFIED: Status.CONFIRMED,
    Basis.ASSUMPTION: Status.ASSUMED,
    Basis.HYPOTHETICAL: Status.HYPOTHETICAL,
}
_LIVE = {Status.PROPOSED, Status.CONFIRMED, Status.ASSUMED}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class FactoryOntology:
    def __init__(
        self,
        store: OntologyStore,
        factory_id: str,
        ontology: Ontology | None = None,
        *,
        clock: Callable[[], str] = _now,
    ) -> None:
        if not ID_RE.match(factory_id):
            raise OntologyError("INVALID_FACTORY", "factory id must be a valid identifier")
        self._store, self.factory_id, self._clock = store, factory_id, clock
        self.ontology = ontology or core_ontology()
        self._events: list[Event] = []
        self._state = GraphState(factory_id)
        self._idem: dict[str, tuple[str, str]] = {}

    # ------------------------------------------------------------------ authorization
    def _scoped(self, p: Principal) -> None:
        if p.factories is None or self.factory_id not in p.factories:
            raise AuthorizationError(
                "FACTORY_FORBIDDEN", f"{p.id} is not authorized for factory {self.factory_id}"
            )

    def _human(self, p: Principal) -> None:
        self._scoped(p)
        if p.kind is not ActorKind.HUMAN or not p.roles & {Role.PLANNER, Role.APPROVER}:
            raise AuthorizationError(
                "HUMAN_APPROVAL_REQUIRED", "authoritative changes need a human planner or approver"
            )

    def _proposer(self, p: Principal) -> None:
        self._scoped(p)
        if not p.roles & {Role.PLANNER, Role.APPROVER, Role.EXTRACTOR}:
            raise AuthorizationError("FORBIDDEN", f"{p.id} may not write to the ontology")

    # ------------------------------------------------------------------ state
    def _refresh(self) -> GraphState:
        rev = self._store.revision(self.factory_id)
        if rev != self._state.revision or len(self._events) != rev:
            self._events = self._store.events(self.factory_id)
            self._state = GraphState.from_events(self.factory_id, self._events)
            self._idem = {}
            for ev in self._events:
                if ev.idempotency_key and ev.idempotency_key not in self._idem:
                    self._idem[ev.idempotency_key] = (_content(ev), ev.object_id)
        return self._state

    def view(self, p: Principal, *, as_of_revision: int | None = None) -> GraphState:
        """Read access for an authorized principal; ``as_of_revision`` reconstructs the model
        exactly as it was known at that revision."""
        self._scoped(p)
        st = self._refresh()
        if as_of_revision is None or as_of_revision >= st.revision:
            return st
        if as_of_revision < 0:
            raise OntologyError("INVALID_REVISION", "revision must be >= 0")
        return GraphState.from_events(self.factory_id, self._events, up_to=as_of_revision)

    def events(self, p: Principal) -> list[Event]:
        self._scoped(p)
        self._refresh()
        return list(self._events)

    @property
    def revision(self) -> int:
        return self._refresh().revision

    @property
    def head_sha(self) -> str:
        return self._refresh().head_sha

    # ------------------------------------------------------------------ commit
    def _commit(
        self,
        p: Principal,
        *,
        action: Action,
        kind: Kind,
        object_id: str,
        version: int,
        basis: Basis,
        status: Status,
        entity: EntityData | None = None,
        relation: RelationData | None = None,
        dispute: DisputeData | None = None,
        evidence: tuple[EvidenceRef, ...] = (),
        note: str = "",
        idempotency_key: str | None = None,
        expected_revision: int | None = None,
    ) -> Event:
        st = self._state
        if expected_revision is not None and expected_revision != st.revision:
            raise StoreError(
                "REVISION_CONFLICT", f"expected revision {expected_revision}, at {st.revision}"
            )
        if len(note) > 500:
            raise OntologyError("INVALID_NOTE", "note too long")
        ev = Event(
            seq=st.revision + 1,
            factory_id=self.factory_id,
            action=action,
            kind=kind,
            object_id=object_id,
            object_version=version,
            actor_id=p.id,
            actor_kind=p.kind.value,
            at=self._clock(),
            basis=basis,
            status=status,
            entity=entity,
            relation=relation,
            dispute=dispute,
            evidence=evidence,
            note=note,
            idempotency_key=idempotency_key,
            prev_sha=st.head_sha or GENESIS,
        )
        ev = ev.model_copy(update={"sha": ev.body_sha256()})
        self._store.append(self.factory_id, [ev], st.revision)
        self._events.append(ev)
        st.apply(ev)
        if idempotency_key:
            self._idem[idempotency_key] = (_content(ev), object_id)
        return ev

    def _idempotent(self, key: str | None, probe: Event) -> str | None:
        """Existing object id when this exact request was already applied; error on key reuse."""
        if not key:
            return None
        hit = self._idem.get(key)
        if hit is None:
            return None
        if hit[0] != _content(probe, normalize_id=probe.action is Action.CREATE):
            raise StoreError("IDEMPOTENCY_CONFLICT", f"key {key!r} was used for another request")
        return hit[1]

    # ------------------------------------------------------------------ validation helpers
    def _evidence(self, ev: tuple[EvidenceRef, ...]) -> tuple[EvidenceRef, ...]:
        if len(ev) > MAX_EVIDENCE:
            raise OntologyError("TOO_MANY_EVIDENCE", f"at most {MAX_EVIDENCE} evidence refs")
        for e in ev:
            if not e.ref.strip() or len(e.ref) > 200 or len(e.quote) > 500:
                raise OntologyError("INVALID_EVIDENCE", "evidence ref/quote out of bounds")
        return ev

    def _window(self, valid_from: str | None, valid_until: str | None) -> None:
        try:
            lo = ts(valid_from) if valid_from else None
            hi = ts(valid_until) if valid_until else None
        except ValueError as exc:
            raise OntologyError("INVALID_VALIDITY", "validity needs ISO-8601 timestamps") from exc
        if lo is not None and hi is not None and hi <= lo:
            raise OntologyError("INVALID_VALIDITY", "valid_until must be after valid_from")

    def _entity_data(
        self,
        st: GraphState,
        status: Status,
        *,
        concept: str,
        name: str,
        attrs: dict[str, Value],
        scope: Scope,
        aliases: tuple[str, ...],
        valid_from: str | None,
        valid_until: str | None,
        superseded_by: str | None = None,
        creating: bool,
    ) -> EntityData:
        cs = self.ontology.concept(concept)
        if creating and cs.deprecated:
            raise OntologyError(
                "CONCEPT_DEPRECATED",
                f"{concept} is deprecated; use {cs.replaced_by or 'its successor'}",
            )
        if not name.strip() or len(name) > MAX_STR:
            raise OntologyError("INVALID_NAME", "name must be 1-300 characters")
        clean = self.ontology.validate_attrs(
            self.ontology.concept_attrs(concept), attrs, owner=concept
        )
        for spec in self.ontology.concept_attrs(concept):
            v = clean.get(spec.name)
            if spec.vtype.value == "REF" and isinstance(v, str):
                tgt = st.entities.get(v)
                if tgt is None or (
                    spec.ref_concept and not self.ontology.is_a(tgt.data.concept, spec.ref_concept)
                ):
                    raise OntologyError("DANGLING_REFERENCE", f"{spec.name} -> {v}")
        missing = [k for k in self._required_scope(concept) if getattr(scope, k) is None]
        if missing:
            raise OntologyError("MISSING_SCOPE", f"{concept} requires scope {missing}")
        for fld, want in (("site_id", "Site"), ("line_id", "ProductionLine")):
            ref = getattr(scope, fld)
            if ref is None:
                continue
            tgt = st.entities.get(ref)
            if tgt is None or not self.ontology.is_a(tgt.data.concept, want):
                raise OntologyError(
                    "DANGLING_SCOPE", f"{fld}={ref} is not a {want} of this factory"
                )
            if status is Status.CONFIRMED and not tgt.authoritative:
                raise OntologyError("SCOPE_NOT_CONFIRMED", f"{fld}={ref} is not a confirmed {want}")
        if len(aliases) > MAX_ALIASES or any(not a.strip() or len(a) > 100 for a in aliases):
            raise OntologyError("INVALID_ALIAS", "aliases must be 1-100 characters, at most 30")
        self._window(valid_from, valid_until)
        return EntityData(
            concept=concept,
            schema_version=cs.version,
            name=name.strip(),
            attrs=clean,
            scope=scope,
            aliases=tuple(dict.fromkeys(a.strip() for a in aliases)),
            valid_from=valid_from,
            valid_until=valid_until,
            superseded_by=superseded_by,
        )

    def _required_scope(self, concept: str) -> tuple[str, ...]:
        need: list[str] = []
        for cid in self.ontology.ancestors(concept):
            need += [s for s in self.ontology.concept(cid).scope_required if s not in need]
        return tuple(need)

    def _relation_data(
        self,
        st: GraphState,
        status: Status,
        *,
        relation: str,
        source: str,
        target: str,
        attrs: dict[str, Value],
        valid_from: str | None,
        valid_until: str | None,
        superseded_by: str | None = None,
        self_id: str | None,
        creating: bool,
    ) -> RelationData:
        rs = self.ontology.relation(relation)
        if creating and rs.deprecated:
            raise OntologyError(
                "RELATION_DEPRECATED",
                f"{relation} is deprecated; use {rs.replaced_by or 'successor'}",
            )
        src, tgt = st.entities.get(source), st.entities.get(target)
        if src is None or tgt is None:
            raise OntologyError("ENDPOINT_NOT_FOUND", f"{source} -> {target}: no such entity here")
        if creating and {Status.REJECTED, Status.RETIRED} & {src.status, tgt.status}:
            raise OntologyError("ENDPOINT_CLOSED", "an endpoint is rejected or retired")
        if not self.ontology.endpoint_ok(relation, src.data.concept, tgt.data.concept):
            raise OntologyError(
                "ENDPOINT_INCOMPATIBLE",
                f"{src.data.concept} -{relation}-> {tgt.data.concept} is not allowed",
            )
        clean = self.ontology.validate_attrs(rs.attrs, attrs, owner=relation)
        self._window(valid_from, valid_until)
        for o in st.relations.values():
            if (
                o.relation_id != self_id
                and (o.data.relation, o.data.source, o.data.target) == (relation, source, target)
                and o.status in _LIVE
                and _overlap(o.data.valid_from, o.data.valid_until, valid_from, valid_until)
            ):
                raise OntologyError("DUPLICATE_RELATION", f"already asserted as {o.relation_id}")
        if status is Status.CONFIRMED:
            if not (src.authoritative and tgt.authoritative):
                raise OntologyError(
                    "ENDPOINT_NOT_CONFIRMED", "a confirmed relation needs confirmed endpoints"
                )
            for limit, same, who in ((rs.max_out, "source", source), (rs.max_in, "target", target)):
                if limit is None:
                    continue
                clash = [
                    o.relation_id
                    for o in st.relations.values()
                    if o.relation_id != self_id
                    and o.data.relation == relation
                    and o.status is Status.CONFIRMED
                    and getattr(o.data, same) == who
                    and _overlap(o.data.valid_from, o.data.valid_until, valid_from, valid_until)
                ]
                if len(clash) + 1 > limit:
                    raise OntologyError(
                        "CARDINALITY_VIOLATION",
                        f"{relation} allows {limit} per {same}; conflicts with {clash}",
                    )
            if rs.acyclic and (
                source == target or st.reaches(relation, target, source, skip=self_id)
            ):
                raise OntologyError(
                    "CYCLE_DETECTED", f"{relation} {source}->{target} closes a cycle"
                )
        return RelationData(
            relation=relation,
            schema_version=rs.version,
            source=source,
            target=target,
            attrs=clean,
            valid_from=valid_from,
            valid_until=valid_until,
            superseded_by=superseded_by,
        )

    def _authority_for(self, p: Principal, basis: Basis) -> Status:
        status = _STATUS_FOR_BASIS[basis]
        if status is Status.PROPOSED:
            self._proposer(p)
        else:
            self._human(p)
        return status

    def _need_evidence(self, status: Status, evidence: tuple[EvidenceRef, ...]) -> None:
        if status is Status.CONFIRMED and not evidence:
            raise OntologyError("EVIDENCE_REQUIRED", "an authoritative assertion needs evidence")

    # ------------------------------------------------------------------ create
    def create_entity(
        self,
        p: Principal,
        concept: str,
        name: str,
        *,
        basis: Basis = Basis.PROPOSAL,
        attrs: dict[str, Value] | None = None,
        scope: Scope | None = None,
        aliases: tuple[str, ...] = (),
        evidence: tuple[EvidenceRef, ...] = (),
        entity_id: str | None = None,
        valid_from: str | None = None,
        valid_until: str | None = None,
        note: str = "",
        idempotency_key: str | None = None,
        expected_revision: int | None = None,
    ) -> EntityRecord:
        status = self._authority_for(p, basis)
        st = self._refresh()
        ev_refs = self._evidence(evidence)
        self._need_evidence(status, ev_refs)
        data = self._entity_data(
            st,
            status,
            concept=concept,
            name=name,
            attrs=attrs or {},
            scope=scope or Scope(),
            aliases=aliases,
            valid_from=valid_from,
            valid_until=valid_until,
            creating=True,
        )
        probe = Event(
            seq=0,
            factory_id=self.factory_id,
            action=Action.CREATE,
            kind=Kind.ENTITY,
            object_id=entity_id or "",
            object_version=1,
            actor_id=p.id,
            actor_kind=p.kind.value,
            at="",
            basis=basis,
            status=status,
            entity=data,
            evidence=ev_refs,
            note=note,
            idempotency_key=idempotency_key,
            prev_sha="",
        )
        again = self._idempotent(idempotency_key, probe)
        if again is not None:
            return st.entities[again]
        if entity_id is not None:
            if not ID_RE.match(entity_id):
                raise OntologyError("INVALID_ID", "entity id must be a valid identifier")
            if entity_id in st.entities:
                raise OntologyError("DUPLICATE_ENTITY", f"{entity_id} already exists")
        else:
            entity_id = _next_id("ent", st.entities)
        self._commit(
            p,
            action=Action.CREATE,
            kind=Kind.ENTITY,
            object_id=entity_id,
            version=1,
            basis=basis,
            status=status,
            entity=data,
            evidence=ev_refs,
            note=note,
            idempotency_key=idempotency_key,
            expected_revision=expected_revision,
        )
        return self._state.entities[entity_id]

    def create_relation(
        self,
        p: Principal,
        relation: str,
        source: str,
        target: str,
        *,
        basis: Basis = Basis.PROPOSAL,
        attrs: dict[str, Value] | None = None,
        evidence: tuple[EvidenceRef, ...] = (),
        valid_from: str | None = None,
        valid_until: str | None = None,
        note: str = "",
        idempotency_key: str | None = None,
        expected_revision: int | None = None,
    ) -> RelationRecord:
        status = self._authority_for(p, basis)
        st = self._refresh()
        ev_refs = self._evidence(evidence)
        self._need_evidence(status, ev_refs)
        data = self._relation_data(
            st,
            status,
            relation=relation,
            source=source,
            target=target,
            attrs=attrs or {},
            valid_from=valid_from,
            valid_until=valid_until,
            self_id=None,
            creating=True,
        )
        probe = Event(
            seq=0,
            factory_id=self.factory_id,
            action=Action.CREATE,
            kind=Kind.RELATION,
            object_id="",
            object_version=1,
            actor_id=p.id,
            actor_kind=p.kind.value,
            at="",
            basis=basis,
            status=status,
            relation=data,
            evidence=ev_refs,
            note=note,
            idempotency_key=idempotency_key,
            prev_sha="",
        )
        again = self._idempotent(idempotency_key, probe)
        if again is not None:
            return st.relations[again]
        rid = _next_id("rel", st.relations)
        self._commit(
            p,
            action=Action.CREATE,
            kind=Kind.RELATION,
            object_id=rid,
            version=1,
            basis=basis,
            status=status,
            relation=data,
            evidence=ev_refs,
            note=note,
            idempotency_key=idempotency_key,
            expected_revision=expected_revision,
        )
        return self._state.relations[rid]

    # ------------------------------------------------------------------ update / lifecycle
    def _obj(self, st: GraphState, object_id: str) -> EntityRecord | RelationRecord:
        rec: EntityRecord | RelationRecord | None = st.entities.get(object_id) or st.relations.get(
            object_id
        )
        if rec is None:
            raise OntologyError("UNKNOWN_OBJECT", f"{object_id} does not exist in this factory")
        return rec

    @staticmethod
    def _version(rec: EntityRecord | RelationRecord, expected: int) -> None:
        if rec.version != expected:
            raise StoreError(
                "VERSION_CONFLICT", f"{_id(rec)} is at version {rec.version}, not {expected}"
            )

    def update(
        self,
        p: Principal,
        object_id: str,
        *,
        expected_version: int,
        attrs: dict[str, Value] | None = None,
        name: str | None = None,
        aliases: tuple[str, ...] | None = None,
        valid_from: str | None = None,
        valid_until: str | None = None,
        evidence: tuple[EvidenceRef, ...] = (),
        note: str = "",
        expected_revision: int | None = None,
    ) -> EntityRecord | RelationRecord:
        """Correct or change an object: a new version; earlier versions remain in the log."""
        st = self._refresh()
        rec = self._obj(st, object_id)
        if rec.status in (Status.REJECTED, Status.RETIRED):
            raise OntologyError("OBJECT_CLOSED", f"{object_id} is {rec.status.value}")
        if rec.authoritative or rec.status in (Status.ASSUMED, Status.HYPOTHETICAL):
            self._human(p)
        else:
            self._proposer(p)
        self._version(rec, expected_version)
        ev_refs = self._evidence(evidence)
        if rec.status is Status.CONFIRMED and not (rec.evidence or ev_refs):
            raise OntologyError("EVIDENCE_REQUIRED", "an authoritative assertion needs evidence")
        if isinstance(rec, EntityRecord):
            d = rec.data
            data = self._entity_data(
                st,
                rec.status,
                concept=d.concept,
                name=name or d.name,
                attrs=attrs if attrs is not None else d.attrs,
                scope=d.scope,
                aliases=aliases if aliases is not None else d.aliases,
                valid_from=valid_from or d.valid_from,
                valid_until=valid_until or d.valid_until,
                superseded_by=d.superseded_by,
                creating=False,
            )
            self._commit(
                p,
                action=Action.UPDATE,
                kind=Kind.ENTITY,
                object_id=object_id,
                version=rec.version + 1,
                basis=rec.basis,
                status=rec.status,
                entity=data,
                evidence=ev_refs,
                note=note,
                expected_revision=expected_revision,
            )
            return self._state.entities[object_id]
        rd = rec.data
        rdata = self._relation_data(
            st,
            rec.status,
            relation=rd.relation,
            source=rd.source,
            target=rd.target,
            attrs=attrs if attrs is not None else rd.attrs,
            valid_from=valid_from or rd.valid_from,
            valid_until=valid_until or rd.valid_until,
            superseded_by=rd.superseded_by,
            self_id=object_id,
            creating=False,
        )
        self._commit(
            p,
            action=Action.UPDATE,
            kind=Kind.RELATION,
            object_id=object_id,
            version=rec.version + 1,
            basis=rec.basis,
            status=rec.status,
            relation=rdata,
            evidence=ev_refs,
            note=note,
            expected_revision=expected_revision,
        )
        return self._state.relations[object_id]

    def add_alias(
        self,
        p: Principal,
        entity_id: str,
        alias: str,
        *,
        expected_version: int,
        evidence: tuple[EvidenceRef, ...] = (),
    ) -> EntityRecord:
        st = self._refresh()
        rec = st.entities.get(entity_id)
        if rec is None:
            raise OntologyError("UNKNOWN_OBJECT", entity_id)
        out = self.update(
            p,
            entity_id,
            expected_version=expected_version,
            aliases=(*rec.data.aliases, alias),
            evidence=evidence,
        )
        if not isinstance(out, EntityRecord):  # pragma: no cover - update() keeps the kind
            raise OntologyError("UNKNOWN_OBJECT", entity_id)
        return out

    def confirm(
        self,
        p: Principal,
        object_id: str,
        *,
        expected_version: int,
        basis: Basis = Basis.HUMAN_CONFIRMED,
        evidence: tuple[EvidenceRef, ...] = (),
        note: str = "",
    ) -> EntityRecord | RelationRecord:
        """Human confirmation: the only way a proposal becomes authoritative."""
        if basis not in (Basis.HUMAN_CONFIRMED, Basis.IMPORTED_VERIFIED):
            raise OntologyError(
                "INVALID_BASIS", "confirm needs HUMAN_CONFIRMED or IMPORTED_VERIFIED"
            )
        self._human(p)
        st = self._refresh()
        rec = self._obj(st, object_id)
        self._version(rec, expected_version)
        if rec.status not in (Status.PROPOSED, Status.ASSUMED, Status.HYPOTHETICAL):
            raise OntologyError("NOT_CONFIRMABLE", f"{object_id} is {rec.status.value}")
        ev_refs = self._evidence(evidence)
        if not (rec.evidence or ev_refs):
            raise OntologyError("EVIDENCE_REQUIRED", "an authoritative assertion needs evidence")
        if isinstance(rec, EntityRecord):
            d = rec.data
            data = self._entity_data(
                st,
                Status.CONFIRMED,
                concept=d.concept,
                name=d.name,
                attrs=d.attrs,
                scope=d.scope,
                aliases=d.aliases,
                valid_from=d.valid_from,
                valid_until=d.valid_until,
                creating=False,
            )
            self._commit(
                p,
                action=Action.CONFIRM,
                kind=Kind.ENTITY,
                object_id=object_id,
                version=rec.version + 1,
                basis=basis,
                status=Status.CONFIRMED,
                entity=data,
                evidence=ev_refs,
                note=note,
            )
            return self._state.entities[object_id]
        rd = rec.data
        rdata = self._relation_data(
            st,
            Status.CONFIRMED,
            relation=rd.relation,
            source=rd.source,
            target=rd.target,
            attrs=rd.attrs,
            valid_from=rd.valid_from,
            valid_until=rd.valid_until,
            self_id=object_id,
            creating=False,
        )
        self._commit(
            p,
            action=Action.CONFIRM,
            kind=Kind.RELATION,
            object_id=object_id,
            version=rec.version + 1,
            basis=basis,
            status=Status.CONFIRMED,
            relation=rdata,
            evidence=ev_refs,
            note=note,
        )
        return self._state.relations[object_id]

    def reject(
        self, p: Principal, object_id: str, *, expected_version: int, note: str
    ) -> EntityRecord | RelationRecord:
        self._human(p)
        st = self._refresh()
        rec = self._obj(st, object_id)
        self._version(rec, expected_version)
        if rec.status not in (Status.PROPOSED, Status.ASSUMED, Status.HYPOTHETICAL):
            raise OntologyError("NOT_REJECTABLE", f"{object_id} is {rec.status.value}; retire it")
        if not note.strip():
            raise OntologyError("NOTE_REQUIRED", "rejecting needs a reason")
        return self._close(p, rec, Action.REJECT, Status.REJECTED, None, None, note)

    def retire(
        self,
        p: Principal,
        object_id: str,
        *,
        expected_version: int,
        valid_until: str | None = None,
        superseded_by: str | None = None,
        note: str,
    ) -> EntityRecord | RelationRecord:
        self._human(p)
        st = self._refresh()
        rec = self._obj(st, object_id)
        self._version(rec, expected_version)
        if rec.status is not Status.CONFIRMED:
            raise OntologyError("NOT_RETIRABLE", f"{object_id} is {rec.status.value}")
        if not note.strip():
            raise OntologyError("NOTE_REQUIRED", "retiring needs a reason")
        until = valid_until or self._clock()
        if rec.data.valid_from and ts(until) <= ts(rec.data.valid_from):
            raise OntologyError("INVALID_VALIDITY", "valid_until must be after valid_from")
        if isinstance(rec, EntityRecord):
            live = [
                r.relation_id
                for r in st.relations.values()
                if object_id in (r.data.source, r.data.target) and r.status in _LIVE
            ]
            if live:
                raise OntologyError("HAS_LIVE_RELATIONS", f"retire {sorted(live)} first")
        if superseded_by is not None and superseded_by not in (*st.entities, *st.relations):
            raise OntologyError("DANGLING_REFERENCE", f"superseded_by {superseded_by}")
        return self._close(p, rec, Action.RETIRE, Status.RETIRED, until, superseded_by, note)

    def _close(
        self,
        p: Principal,
        rec: EntityRecord | RelationRecord,
        action: Action,
        status: Status,
        until: str | None,
        sup: str | None,
        note: str,
    ) -> EntityRecord | RelationRecord:
        if isinstance(rec, EntityRecord):
            data = rec.data.model_copy(
                update={
                    "valid_until": until or rec.data.valid_until,
                    "superseded_by": sup or rec.data.superseded_by,
                }
            )
            self._commit(
                p,
                action=action,
                kind=Kind.ENTITY,
                object_id=rec.entity_id,
                version=rec.version + 1,
                basis=rec.basis,
                status=status,
                entity=data,
                note=note,
            )
            return self._state.entities[rec.entity_id]
        rdata = rec.data.model_copy(
            update={
                "valid_until": until or rec.data.valid_until,
                "superseded_by": sup or rec.data.superseded_by,
            }
        )
        self._commit(
            p,
            action=action,
            kind=Kind.RELATION,
            object_id=rec.relation_id,
            version=rec.version + 1,
            basis=rec.basis,
            status=status,
            relation=rdata,
            note=note,
        )
        return self._state.relations[rec.relation_id]

    # ------------------------------------------------------------------ disputes
    def dispute(
        self,
        p: Principal,
        subject_id: str,
        reason: str,
        *,
        evidence: tuple[EvidenceRef, ...] = (),
    ) -> DisputeRecord:
        """Anyone scoped may flag conflicting evidence; only a human can resolve it."""
        self._proposer(p)
        st = self._refresh()
        self._obj(st, subject_id)
        if not reason.strip() or len(reason) > 500:
            raise OntologyError("INVALID_DISPUTE", "a reason of 1-500 characters is required")
        did = _next_id("dsp", st.disputes)
        self._commit(
            p,
            action=Action.DISPUTE,
            kind=Kind.DISPUTE,
            object_id=did,
            version=1,
            basis=Basis.PROPOSAL,
            status=Status.PROPOSED,
            dispute=DisputeData(subject_id=subject_id, reason=reason.strip()),
            evidence=self._evidence(evidence),
        )
        return self._state.disputes[did]

    def resolve_dispute(self, p: Principal, dispute_id: str, resolution: str) -> DisputeRecord:
        self._human(p)
        st = self._refresh()
        d = st.disputes.get(dispute_id)
        if d is None:
            raise OntologyError("UNKNOWN_OBJECT", dispute_id)
        if not d.open:
            raise OntologyError("ALREADY_RESOLVED", dispute_id)
        if not resolution.strip() or len(resolution) > 500:
            raise OntologyError("INVALID_DISPUTE", "a resolution of 1-500 characters is required")
        self._commit(
            p,
            action=Action.RESOLVE_DISPUTE,
            kind=Kind.DISPUTE,
            object_id=dispute_id,
            version=2,
            basis=Basis.HUMAN_CONFIRMED,
            status=Status.CONFIRMED,
            dispute=DisputeData(
                subject_id=d.subject_id, reason=d.reason, resolution=resolution.strip()
            ),
        )
        return self._state.disputes[dispute_id]


# ---------------------------------------------------------------------- helpers
def _id(rec: EntityRecord | RelationRecord) -> str:
    return rec.entity_id if isinstance(rec, EntityRecord) else rec.relation_id


def _next_id(prefix: str, existing: Mapping[str, object]) -> str:
    n = len(existing) + 1
    while f"{prefix}-{n:05d}" in existing:
        n += 1
    return f"{prefix}-{n:05d}"


def _content(ev: Event, *, normalize_id: bool | None = None) -> str:
    norm = ev.action is Action.CREATE if normalize_id is None else normalize_id
    return (ev.model_copy(update={"object_id": ""}) if norm else ev).content_sha256()


def _overlap(
    a_from: str | None, a_until: str | None, b_from: str | None, b_until: str | None
) -> bool:
    def lo(x: str | None) -> float:
        return ts(x).timestamp() if x else float("-inf")

    def hi(x: str | None) -> float:
        return ts(x).timestamp() if x else float("inf")

    return lo(a_from) < hi(b_until) and lo(b_from) < hi(a_until)

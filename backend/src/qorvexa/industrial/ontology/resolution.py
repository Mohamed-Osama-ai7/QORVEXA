"""Identity resolution: surface expression -> canonical entity, with evidence and honest outcomes.

Outcomes: RESOLVED (exactly one supported candidate), AMBIGUOUS (several, or a declared
ambiguity), UNRESOLVED (none; proposals and rejected scopes are listed but never chosen) and
CONFLICTED (sources disagree or the only candidate is under dispute). An entity is never chosen
silently: every candidate carries the evidence that supports or excludes it.

Terminology is reused from the Industrial Understanding engine (``resolve_surface``); this module
only maps its confirmed ``canonical_id`` onto ontology entities and never duplicates its logic.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from qorvexa.industrial.governance.authz import Principal
from qorvexa.industrial.ontology.graph import Direction, GraphState
from qorvexa.industrial.ontology.model import EntityRecord, Status
from qorvexa.industrial.ontology.schema import Ontology
from qorvexa.industrial.understanding.terminology import (
    ResolutionContext,
    ResolutionStatus,
    TermEntry,
    TerminologyBook,
    resolve_surface,
    surface_key,
)

F = ConfigDict(frozen=True, extra="forbid")
_TYPE_FOR = {
    "Machine": "MACHINE",
    "Equipment": "MACHINE",
    "ProductionLine": "LINE",
    "Product": "PRODUCT",
    "Material": "MATERIAL",
    "Resource": "RESOURCE",
    "Shift": "SHIFT",
}


class Outcome(StrEnum):
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    UNRESOLVED = "UNRESOLVED"
    CONFLICTED = "CONFLICTED"


class ResolveContext(BaseModel):
    model_config = F
    concepts: tuple[str, ...] = ()  # allowed concepts (is-a applies); empty = any
    site_id: str | None = None
    line_id: str | None = None
    department: str | None = None
    related_entity_ids: tuple[str, ...] = ()  # entities already identified in the conversation
    text: str = ""  # surrounding text, passed to terminology cue matching
    speaker_role: str | None = None


class Candidate(BaseModel):
    model_config = F
    entity_id: str
    concept: str
    name: str
    sources: tuple[str, ...]  # OFFICIAL_ID | NAME | ALIAS | TERMINOLOGY
    evidence: tuple[str, ...] = ()
    score: int = 0
    excluded: str | None = None  # why it cannot be chosen (kept visible)


class Resolution(BaseModel):
    model_config = F
    surface: str
    outcome: Outcome
    entity_id: str | None = None
    candidates: tuple[Candidate, ...] = ()  # viable, best first
    excluded: tuple[Candidate, ...] = ()
    reasons: tuple[str, ...] = ()
    term_ids: tuple[str, ...] = ()


_POSITIVE = (
    "official_id",
    "line_match",
    "site_match",
    "department_match",
    "relation_support",
    "term",
)


class IdentityResolver:
    """Resolver bound to ONE factory state (the view a principal was allowed to read)."""

    def __init__(
        self,
        state: GraphState,
        ontology: Ontology,
        terms: list[TermEntry] | None = None,
    ) -> None:
        self.state, self.ontology = state, ontology
        self.terms = [t for t in (terms or []) if t.factory_id == state.factory_id]

    # ------------------------------------------------------------------ scope helpers
    def _line_of(self, e: EntityRecord) -> str | None:
        if e.data.scope.line_id:
            return e.data.scope.line_id
        for a in self.state.ancestors_via(e.entity_id, "CONTAINS"):
            rec = self.state.entities.get(a)
            if rec and self.ontology.is_a(rec.data.concept, "ProductionLine"):
                return a
        return None

    def _site_of(self, e: EntityRecord) -> str | None:
        if e.data.scope.site_id:
            return e.data.scope.site_id
        for a in self.state.ancestors_via(e.entity_id, "CONTAINS"):
            rec = self.state.entities.get(a)
            if rec and self.ontology.is_a(rec.data.concept, "Site"):
                return a
        return None

    # ------------------------------------------------------------------ resolution
    def resolve(self, surface: str, ctx: ResolveContext | None = None) -> Resolution:
        ctx = ctx or ResolveContext()
        key = surface_key(surface)
        if not key:
            return Resolution(
                surface=surface, outcome=Outcome.UNRESOLVED, reasons=("empty expression",)
            )
        found: dict[str, dict[str, set[str]]] = {}  # entity -> {"sources": set, "evidence": set}
        proposals: dict[str, EntityRecord] = {}

        def note(eid: str, source: str, ev: str) -> None:
            slot = found.setdefault(eid, {"sources": set(), "evidence": set()})
            slot["sources"].add(source)
            slot["evidence"].add(ev)

        for eid, e in self.state.entities.items():
            hits = self._match(e, key)
            if not hits:
                continue
            if e.status is not Status.CONFIRMED:
                if e.status is Status.PROPOSED:
                    proposals[eid] = e
                continue
            for src, how in hits:
                note(eid, src, how)

        reasons: list[str] = []
        term_ids: list[str] = []
        term_entities: set[str] = set()
        term_ambiguous = False
        excluded: dict[str, str] = {}
        tr = resolve_surface(
            self.terms,
            surface,
            ResolutionContext(
                factory_id=self.state.factory_id,
                line_id=ctx.line_id,
                department=ctx.department,
                machine_ids=ctx.related_entity_ids,
                speaker_role=ctx.speaker_role,
                surrounding_text=ctx.text,
            ),
        )
        if tr.status is ResolutionStatus.RESOLVED and tr.canonical_id:
            target = self.state.entities.get(tr.canonical_id)
            if target is None:
                excluded[tr.canonical_id] = "TERM_TARGET_MISSING: term points to no entity here"
            elif target.status is not Status.CONFIRMED:
                excluded[tr.canonical_id] = "TERM_TARGET_NOT_CONFIRMED"
            else:
                note(tr.canonical_id, "TERMINOLOGY", f"term:{tr.term_id}")
                term_entities.add(tr.canonical_id)
                term_ids.append(str(tr.term_id))
        elif tr.status is ResolutionStatus.AMBIGUOUS:
            term_ambiguous = True
            reasons.append(f"terminology is ambiguous: {list(tr.candidates)} ({tr.reason})")
        elif tr.status is ResolutionStatus.PROPOSED_ONLY:
            reasons.append("terminology exists only as an unconfirmed proposal")

        built: list[Candidate] = []
        for eid, slot in sorted(found.items()):
            e = self.state.entities[eid]
            ev = set(slot["evidence"])
            why = excluded.get(eid)
            if ctx.concepts and not any(
                self.ontology.is_a(e.data.concept, c) for c in ctx.concepts
            ):
                why = why or f"CONCEPT_MISMATCH: {e.data.concept}"
            line, site = self._line_of(e), self._site_of(e)
            if ctx.line_id:
                if line is None:
                    ev.add("line_unverified")
                elif line != ctx.line_id:
                    why = why or f"OTHER_LINE: belongs to {line}"
                else:
                    ev.add("line_match")
            if ctx.site_id:
                if site is None:
                    ev.add("site_unverified")
                elif site != ctx.site_id:
                    why = why or f"OTHER_SITE: belongs to {site}"
                else:
                    ev.add("site_match")
            if ctx.department and e.data.scope.department:
                if e.data.scope.department != ctx.department:
                    why = why or f"OTHER_DEPARTMENT: {e.data.scope.department}"
                else:
                    ev.add("department_match")
            for r in self.state.relations_of(eid, Direction.BOTH):
                other = r.data.target if r.data.source == eid else r.data.source
                if r.status is Status.CONFIRMED and other in ctx.related_entity_ids:
                    ev.add(f"relation_support:{r.relation_id}")
            if "OFFICIAL_ID" in slot["sources"]:
                ev.add("official_id")
            ev_t = tuple(sorted(ev))
            built.append(
                Candidate(
                    entity_id=eid,
                    concept=e.data.concept,
                    name=e.data.name,
                    sources=tuple(sorted(slot["sources"])),
                    evidence=ev_t,
                    score=sum(1 for x in ev_t if x.split(":")[0] in _POSITIVE),
                    excluded=why,
                )
            )
        for eid, why in excluded.items():
            if eid not in found:
                built.append(
                    Candidate(
                        entity_id=eid, concept="?", name="", sources=("TERMINOLOGY",), excluded=why
                    )
                )
        for eid, pe in sorted(proposals.items()):
            built.append(
                Candidate(
                    entity_id=eid,
                    concept=pe.data.concept,
                    name=pe.data.name,
                    sources=("NAME",),
                    excluded="NOT_CONFIRMED: only a proposal exists",
                )
            )
        viable = sorted(
            (c for c in built if c.excluded is None), key=lambda c: (-c.score, c.entity_id)
        )
        out = sorted((c for c in built if c.excluded is not None), key=lambda c: c.entity_id)
        base = {"surface": surface, "excluded": tuple(out), "term_ids": tuple(term_ids)}

        if term_ambiguous:
            return Resolution(
                **base,
                outcome=Outcome.AMBIGUOUS,
                candidates=tuple(viable),
                reasons=(*reasons, "never chosen silently: a declared ambiguity needs a cue"),
            )
        if not viable:
            if out:
                reasons.append("only excluded candidates exist (proposals / other scope)")
            else:
                reasons.append("no confirmed entity matches this expression")
            return Resolution(**base, outcome=Outcome.UNRESOLVED, reasons=tuple(reasons))
        by_ontology = {c.entity_id for c in viable if set(c.sources) - {"TERMINOLOGY"}}
        if term_entities and by_ontology and not (term_entities & by_ontology):
            return Resolution(
                **base,
                outcome=Outcome.CONFLICTED,
                candidates=tuple(viable),
                reasons=(*reasons, "terminology and ontology identify different entities"),
            )
        if len(viable) == 1:
            only = viable[0]
            if self.state.entities[only.entity_id].disputed:
                return Resolution(
                    **base,
                    outcome=Outcome.CONFLICTED,
                    candidates=tuple(viable),
                    reasons=(*reasons, "the matching entity has an open dispute"),
                )
            return Resolution(
                **base,
                outcome=Outcome.RESOLVED,
                entity_id=only.entity_id,
                candidates=tuple(viable),
                reasons=tuple(reasons),
            )
        best, second = viable[0].score, viable[1].score
        if best > second and best > 0:
            win = viable[0]
            if self.state.entities[win.entity_id].disputed:
                return Resolution(
                    **base,
                    outcome=Outcome.CONFLICTED,
                    candidates=tuple(viable),
                    reasons=(*reasons, "the best match has an open dispute"),
                )
            return Resolution(
                **base,
                outcome=Outcome.RESOLVED,
                entity_id=win.entity_id,
                candidates=tuple(viable),
                reasons=(*reasons, f"selected by evidence {list(win.evidence)}; alternatives kept"),
            )
        return Resolution(
            **base,
            outcome=Outcome.AMBIGUOUS,
            candidates=tuple(viable),
            reasons=(*reasons, "several entities fit and the context does not separate them"),
        )

    def _match(self, e: EntityRecord, key: str) -> list[tuple[str, str]]:
        hits: list[tuple[str, str]] = []
        if e.entity_id.lower() == key.lower():
            hits.append(("OFFICIAL_ID", "entity_id"))
        asset = e.data.attrs.get("asset_id")
        if isinstance(asset, str) and surface_key(asset) == key:
            hits.append(("OFFICIAL_ID", "asset_id"))
        if surface_key(e.data.name) == key:
            hits.append(("NAME", "name"))
        for a in e.data.aliases:
            if surface_key(a) == key:
                hits.append(("ALIAS", f"alias:{a}"))
        return hits


def propose_terminology_for_entities(
    book: TerminologyBook, principal: Principal, state: GraphState
) -> list[TermEntry]:
    """Offer each confirmed entity alias to the terminology book as a PROPOSED term. It never
    confirms: a human terminology reviewer still decides (ontology aliases are not language
    authority)."""
    have = {(surface_key(s), e.canonical_id) for e in book.entries() for s in e.surfaces()}
    made: list[TermEntry] = []
    for eid, e in sorted(state.entities.items()):
        if e.status is not Status.CONFIRMED:
            continue
        etype = _TYPE_FOR.get(e.data.concept)
        if etype is None:
            continue
        for alias in e.data.aliases:
            if (surface_key(alias), eid) in have:
                continue
            made.append(
                book.propose(
                    principal,
                    alias,
                    f"ontology:{eid}",
                    meaning=e.data.name,
                    source="ontology",
                    entity_type=etype,
                    canonical_id=eid,
                )
            )
    return made

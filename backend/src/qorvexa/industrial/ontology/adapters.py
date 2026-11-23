"""Integration contracts: Understanding, Formulation, Production Planning, Decision Intelligence.

Nothing here solves, validates mathematics or confirms anything. Adapters read an authorized
factory view and return typed, provenance-carrying context; proposals stay proposals.
Unit handling uses only the Industrial Units Engine (``parse_unit`` / ``Quantity``).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from qorvexa.industrial.ontology.graph import Direction, GraphState, ts
from qorvexa.industrial.ontology.model import EntityRecord, EvidenceRef, RelationRecord, Status
from qorvexa.industrial.ontology.resolution import (
    IdentityResolver,
    Outcome,
    Resolution,
    ResolveContext,
)
from qorvexa.industrial.ontology.retrieval import ContextEngine, ContextQuery
from qorvexa.industrial.ontology.schema import Ontology, QuantityValue
from qorvexa.industrial.understanding.models import Candidate as UCandidate
from qorvexa.industrial.understanding.models import UnderstandingReport
from qorvexa.industrial.units import Quantity, UnitError, parse_unit

F = ConfigDict(frozen=True, extra="forbid")


# ===================================================================== Understanding
class MentionLink(BaseModel):
    model_config = F
    mention_id: str
    text: str
    resolution: Resolution


class ClaimOntologyRefs(BaseModel):
    model_config = F
    claim_id: str
    subject: MentionLink | None = None
    related: MentionLink | None = None


class UnderstandingLinks(BaseModel):
    model_config = F
    factory_id: str
    revision: int
    mentions: tuple[MentionLink, ...]
    claims: tuple[ClaimOntologyRefs, ...]
    unresolved_mentions: tuple[str, ...]


_CONCEPTS = {
    "MACHINE": ("Machine", "Equipment"),
    "LINE": ("ProductionLine",),
    "PRODUCT": ("Product",),
    "MATERIAL": ("Material",),
    "RESOURCE": ("Resource",),
    "SHIFT": ("Shift",),
}


def link_report(
    report: UnderstandingReport,
    resolver: IdentityResolver,
    *,
    line_id: str | None = None,
    department: str | None = None,
) -> UnderstandingLinks:
    """Resolve every entity mention of an understanding report to canonical ontology references.
    Unresolved, ambiguous and conflicted identities are preserved, never guessed; the report
    itself is not modified and no proposal becomes a fact."""
    if report.factory_id != resolver.state.factory_id:
        raise ValueError("FACTORY_MISMATCH: report and ontology belong to different factories")
    by_id: dict[str, MentionLink] = {}
    known: list[str] = []
    for m in sorted(report.entities, key=lambda x: x.orig_start):
        ctx = ResolveContext(
            concepts=_CONCEPTS.get(m.entity_type, ()),
            line_id=line_id,
            department=department,
            related_entity_ids=tuple(known),
        )
        res = resolver.resolve(m.text, ctx)
        if res.outcome is Outcome.RESOLVED and res.entity_id:
            known.append(res.entity_id)
        by_id[m.mention_id] = MentionLink(mention_id=m.mention_id, text=m.text, resolution=res)
    claims = tuple(
        ClaimOntologyRefs(
            claim_id=c.claim_id,
            subject=by_id.get(c.subject_entity or ""),
            related=by_id.get(c.related_entity or ""),
        )
        for c in report.claims
    )
    return UnderstandingLinks(
        factory_id=report.factory_id,
        revision=resolver.state.revision,
        mentions=tuple(by_id.values()),
        claims=claims,
        unresolved_mentions=tuple(
            sorted(k for k, v in by_id.items() if v.resolution.outcome is not Outcome.RESOLVED)
        ),
    )


class ContextSnapshot(BaseModel):
    """Bounded, reproducible context for the Understanding engine."""

    model_config = F
    factory_id: str
    revision: int
    head_sha: str
    entities: tuple[tuple[str, str, str, tuple[str, ...]], ...]  # id, concept, name, aliases
    relations: tuple[tuple[str, str, str, str], ...]  # id, relation, source, target
    truncated: bool
    limitations: tuple[str, ...]
    snapshot_sha256: str = ""


def understanding_snapshot(
    state: GraphState, ontology: Ontology, roots: Sequence[str], *, max_nodes: int = 80
) -> ContextSnapshot:
    res = ContextEngine(state, ontology).retrieve(
        ContextQuery(roots=tuple(roots), max_depth=2, max_nodes=max_nodes, max_edges=200)
    )
    snap = ContextSnapshot(
        factory_id=res.factory_id,
        revision=res.revision,
        head_sha=res.head_sha,
        entities=tuple((n.entity_id, n.concept, n.name, n.aliases) for n in res.nodes),
        relations=tuple((e.relation_id, e.relation, e.source, e.target) for e in res.edges),
        truncated=res.truncated,
        limitations=res.limitations,
    )
    body = snap.model_dump(mode="json", exclude={"snapshot_sha256"})
    return snap.model_copy(
        update={
            "snapshot_sha256": hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        }
    )


# ===================================================================== Formulation / Planning
class Rejected(BaseModel):
    model_config = F
    key: str
    reason: str
    objects: tuple[str, ...] = ()


class PlanningCandidate(BaseModel):
    """A candidate formulation field with full lineage back to ontology objects."""

    model_config = F
    key: str  # formulation ledger key
    value: str
    objects: tuple[str, ...]  # entity / relation ids it came from
    evidence: tuple[EvidenceRef, ...]
    revision: int


class PlanningContext(BaseModel):
    model_config = F
    factory_id: str
    revision: int
    head_sha: str
    line_structure: tuple[tuple[str, str, str], ...]  # parent, relation, child
    product_resources: tuple[tuple[str, str, str, str], ...]  # product, resource, amount, unit
    capacities: tuple[tuple[str, str, str, str], ...]  # resource, amount, unit, basis
    material_consumption: tuple[tuple[str, str, str, str], ...]  # product, material, amount, unit
    process_compatibility: tuple[tuple[str, str], ...]  # producer, product
    candidates: tuple[PlanningCandidate, ...]
    rejected: tuple[Rejected, ...]
    limitations: tuple[str, ...]


def _auth(rec: EntityRecord | RelationRecord, at: str | None) -> str | None:
    """Why a record may NOT feed planning, or None when it may."""
    if rec.status is not Status.CONFIRMED:
        return f"NOT_AUTHORITATIVE ({rec.status.value})"
    if rec.disputed:
        return "DISPUTED"
    d = rec.data
    if at is not None and not GraphState.window_contains(d.valid_from, d.valid_until, at):
        return "NOT_VALID_AT_TIME"
    return None


def _qty(v: object) -> QuantityValue | None:
    return v if isinstance(v, QuantityValue) else None


def planning_context(
    state: GraphState, ontology: Ontology, *, line_id: str | None = None, at: str | None = None
) -> PlanningContext:
    """Map confirmed structure into Production Planning candidates (no solving, no validation of
    the optimisation). Anything that cannot be mapped honestly is listed in ``rejected``."""
    ents, rels = state.entities, state.relations
    cands: list[PlanningCandidate] = []
    rejected: list[Rejected] = []

    def concept(eid: str) -> str:
        return ents[eid].data.concept

    scope_ids: set[str] | None = None
    if line_id is not None:
        if line_id not in ents:
            raise ValueError(f"UNKNOWN_LINE: {line_id}")
        scope_ids = {line_id}
        frontier = [line_id]
        while frontier:
            cur = frontier.pop()
            for r in state.relations_of(cur, Direction.OUT):
                if (
                    r.data.relation == "CONTAINS"
                    and r.status is Status.CONFIRMED
                    and r.data.target not in scope_ids
                ):
                    scope_ids.add(r.data.target)
                    frontier.append(r.data.target)

    structure: list[tuple[str, str, str]] = []
    resources_by_product: list[tuple[str, str, str, str]] = []
    capacities: list[tuple[str, str, str, str]] = []
    materials: list[tuple[str, str, str, str]] = []
    compat: list[tuple[str, str]] = []

    for rid, r in sorted(rels.items()):
        d = r.data
        if d.source not in ents or d.target not in ents:
            continue
        if d.relation == "CONTAINS" and (scope_ids is None or d.source in scope_ids):
            if _auth(r, at) is None:
                structure.append((d.source, "CONTAINS", d.target))
            continue
        if d.relation == "PRODUCES":
            if scope_ids is not None and d.source not in scope_ids:
                continue
            why = _auth(r, at) or _auth(ents[d.source], at) or _auth(ents[d.target], at)
            if why:
                rejected.append(
                    Rejected(key=f"compat:{d.source}:{d.target}", reason=why, objects=(rid,))
                )
            else:
                compat.append((d.source, d.target))
            continue
        if d.relation == "REQUIRES" and concept(d.source) == "Product":
            amt = _qty(d.attrs.get("amount"))
            key = f"consumption:{d.target}:{d.source}"
            why = _auth(r, at) or _auth(ents[d.source], at) or _auth(ents[d.target], at)
            if why is None and amt is None:
                why = "MISSING_AMOUNT"
            if why is None and amt is not None and "/" not in amt.unit:
                why = "UNIT_NOT_A_RATE: state the amount per unit of product"
            if why is not None or amt is None:
                rejected.append(Rejected(key=key, reason=why or "MISSING_AMOUNT", objects=(rid,)))
                continue
            resources_by_product.append((d.source, d.target, amt.value, amt.unit))
            ev = r.evidence
            cands.append(
                PlanningCandidate(
                    key=key,
                    value=amt.value,
                    objects=(rid, d.source, d.target),
                    evidence=ev,
                    revision=state.revision,
                )
            )
            cands.append(
                PlanningCandidate(
                    key=f"{key}:unit",
                    value=amt.unit,
                    objects=(rid,),
                    evidence=ev,
                    revision=state.revision,
                )
            )
            continue
        if d.relation == "CONSUMES" and concept(d.source) == "Product":
            amt = _qty(d.attrs.get("amount"))
            why = _auth(r, at) or _auth(ents[d.source], at) or _auth(ents[d.target], at)
            if why is None and amt is None:
                why = "MISSING_AMOUNT"
            if why is not None or amt is None:
                rejected.append(
                    Rejected(
                        key=f"material:{d.target}:{d.source}",
                        reason=why or "MISSING_AMOUNT",
                        objects=(rid,),
                    )
                )
                continue
            materials.append((d.source, d.target, amt.value, amt.unit))
            continue
        if d.relation == "HAS_CAPACITY":
            if scope_ids is not None and d.source not in scope_ids:
                continue
            cap = ents[d.target]
            amt = _qty(cap.data.attrs.get("amount"))
            basis = str(cap.data.attrs.get("basis", ""))
            key = f"resource:{d.source}:capacity"
            why = _auth(r, at) or _auth(ents[d.source], at) or _auth(cap, at)
            if why is None and amt is None:
                why = "MISSING_AMOUNT"
            if why is None and basis != "AVAILABLE_TIME":
                why = (
                    f"CAPACITY_BASIS_NOT_MAPPED ({basis}): the LP slice models available time only"
                )
            if why is None and amt is not None:
                try:
                    if "/" in amt.unit:
                        why = "UNIT_IS_A_RATE: capacity must be a plain quantity"
                    elif parse_unit(amt.unit).dimension != parse_unit("h").dimension:
                        why = f"UNIT_NOT_TIME: available-time capacity cannot be in {amt.unit!r}"
                except UnitError as exc:
                    why = f"UNIT_REJECTED: {exc}"
            if why is not None or amt is None:
                rejected.append(
                    Rejected(key=key, reason=why or "MISSING_AMOUNT", objects=(rid, d.target))
                )
                continue
            capacities.append((d.source, amt.value, amt.unit, basis))
            ev = (*r.evidence, *cap.evidence)
            cands.append(
                PlanningCandidate(
                    key=key,
                    value=amt.value,
                    objects=(rid, d.source, d.target),
                    evidence=ev,
                    revision=state.revision,
                )
            )
            cands.append(
                PlanningCandidate(
                    key=f"resource:{d.source}:unit",
                    value=amt.unit,
                    objects=(rid, d.target),
                    evidence=ev,
                    revision=state.revision,
                )
            )
            continue
        if d.relation == "HAS_DEMAND" and concept(d.source) == "Product":
            dem = ents[d.target]
            amt = _qty(dem.data.attrs.get("quantity"))
            bound = str(dem.data.attrs.get("bound", ""))
            why = _auth(r, at) or _auth(ents[d.source], at) or _auth(dem, at)
            if why is None and (amt is None or bound not in ("MIN", "MAX")):
                why = "MISSING_QUANTITY_OR_BOUND"
            if why is not None or amt is None:
                rejected.append(
                    Rejected(
                        key=f"product:{d.source}:demand",
                        reason=why or "MISSING_QUANTITY",
                        objects=(rid, d.target),
                    )
                )
                continue
            k = "min_quantity" if bound == "MIN" else "max_quantity"
            ev = (*r.evidence, *dem.evidence)
            cands.append(
                PlanningCandidate(
                    key=f"product:{d.source}:{k}",
                    value=amt.value,
                    objects=(rid, d.source, d.target),
                    evidence=ev,
                    revision=state.revision,
                )
            )
            cands.append(
                PlanningCandidate(
                    key=f"product:{d.source}:bound_unit",
                    value=amt.unit,
                    objects=(rid, d.target),
                    evidence=ev,
                    revision=state.revision,
                )
            )

    # unit-bearing product margins (attribute, not a relation)
    for eid, e in sorted(ents.items()):
        if concept(eid) != "Product" or "margin_per_unit" not in e.data.attrs:
            continue
        m = _qty(e.data.attrs["margin_per_unit"])
        why = _auth(e, at)
        if why is None and m is not None and "/" not in m.unit:
            why = "MISSING_CURRENCY_OR_UNIT: state e.g. 50 EGP per piece"
        if why is not None or m is None:
            rejected.append(
                Rejected(
                    key=f"product:{eid}:value_per_unit", reason=why or "MISSING", objects=(eid,)
                )
            )
            continue
        cur = m.unit.split("/")[0].strip()
        cands.append(
            PlanningCandidate(
                key=f"product:{eid}:value_per_unit",
                value=m.value,
                objects=(eid,),
                evidence=e.evidence,
                revision=state.revision,
            )
        )
        cands.append(
            PlanningCandidate(
                key=f"product:{eid}:value_unit",
                value=m.unit,
                objects=(eid,),
                evidence=e.evidence,
                revision=state.revision,
            )
        )
        cands.append(
            PlanningCandidate(
                key="problem:currency",
                value=cur,
                objects=(eid,),
                evidence=e.evidence,
                revision=state.revision,
            )
        )

    # contradictory duplicates stay visible instead of being merged silently
    seen: dict[str, PlanningCandidate] = {}
    final: list[PlanningCandidate] = []
    for c in cands:
        old = seen.get(c.key)
        if old is not None and old.value != c.value:
            rejected.append(
                Rejected(
                    key=c.key,
                    reason=f"CONFLICTING_VALUES: {old.value} vs {c.value}",
                    objects=tuple({*old.objects, *c.objects}),
                )
            )
            continue
        if old is None:
            seen[c.key] = c
            final.append(c)
    limits = ["Context only: no solver result, no unit validation of the optimisation problem"]
    if rejected:
        limits.append(f"{len(rejected)} item(s) could not be mapped; see rejected")
    return PlanningContext(
        factory_id=state.factory_id,
        revision=state.revision,
        head_sha=state.head_sha,
        line_structure=tuple(sorted(structure)),
        product_resources=tuple(sorted(resources_by_product)),
        capacities=tuple(sorted(capacities)),
        material_consumption=tuple(sorted(materials)),
        process_compatibility=tuple(sorted(compat)),
        candidates=tuple(final),
        rejected=tuple(sorted(rejected, key=lambda x: (x.key, x.reason))),
        limitations=tuple(limits),
    )


def to_understanding_candidates(ctx: PlanningContext, *, source_label: str) -> list[UCandidate]:
    """Shape for ``understanding.emit_proposals``: still UNTRUSTED ledger proposals."""
    return [
        UCandidate(
            key=c.key,
            value=c.value,
            support=tuple(f"onto:{ctx.factory_id}@{ctx.revision}:{o}" for o in c.objects),
            quote=f"{source_label} (revision {ctx.revision})"[:500],
            status="SUPPORTED",
        )
        for c in ctx.candidates
    ]


def convert_capacity(amount: QuantityValue, target_unit: str) -> QuantityValue:
    """Exact conversion through the units engine (no conversion logic lives here)."""
    res = Quantity.of(amount.value, amount.unit).to(target_unit).result
    v = res.value
    text = str(v.numerator) if v.denominator == 1 else f"{v.numerator}/{v.denominator}"
    return QuantityValue(value=text, unit=target_unit)


# ===================================================================== Decision Intelligence
class FactContext(BaseModel):
    model_config = F
    object_id: str
    description: str
    status: Status
    valid_from: str | None
    valid_until: str | None
    recorded_revision: int
    stale: bool
    disputed: bool
    evidence: tuple[EvidenceRef, ...]


class InterpretationContext(BaseModel):
    """Time- and provenance-aware facts for interpreting a verified scenario."""

    model_config = F
    factory_id: str
    revision: int
    head_sha: str
    at: str
    current: tuple[FactContext, ...]
    historical: tuple[FactContext, ...]
    disputed: tuple[FactContext, ...]
    open_disputes: tuple[str, ...]
    limitations: tuple[str, ...]


def interpretation_context(
    state: GraphState,
    ontology: Ontology,
    object_ids: Sequence[str],
    *,
    at: str,
    max_age_days: int | None = None,
) -> InterpretationContext:
    engine = ContextEngine(state, ontology)
    old = set(engine.outdated(at))
    cur: list[FactContext] = []
    hist: list[FactContext] = []
    dis: list[FactContext] = []
    for oid in sorted(dict.fromkeys(object_ids)):
        rec = state.entities.get(oid) or state.relations.get(oid)
        if rec is None:
            raise ValueError(f"UNKNOWN_OBJECT: {oid}")
        d = rec.data
        desc = (
            f"{rec.data.concept} {rec.data.name}"
            if isinstance(rec, EntityRecord)
            else f"{rec.data.source} -{rec.data.relation}-> {rec.data.target}"
        )
        stale = oid in old
        if max_age_days is not None and not stale:
            try:
                recorded = ts(at) - ts(d.valid_from) if d.valid_from else None
            except ValueError:
                recorded = None
            stale = recorded is not None and recorded.days > max_age_days
        fc = FactContext(
            object_id=oid,
            description=desc,
            status=rec.status,
            valid_from=d.valid_from,
            valid_until=d.valid_until,
            recorded_revision=rec.updated_seq,
            stale=stale,
            disputed=rec.disputed,
            evidence=rec.evidence,
        )
        if rec.disputed:
            dis.append(fc)
        elif stale or rec.status is not Status.CONFIRMED:
            hist.append(fc)
        else:
            cur.append(fc)
    return InterpretationContext(
        factory_id=state.factory_id,
        revision=state.revision,
        head_sha=state.head_sha,
        at=at,
        current=tuple(cur),
        historical=tuple(hist),
        disputed=tuple(dis),
        open_disputes=tuple(sorted(i for i, x in state.disputes.items() if x.open)),
        limitations=(
            "Historical / retired / non-authoritative facts are separated from current ones",
            "Context only: it never changes a verified scenario or a recommendation",
        ),
    )

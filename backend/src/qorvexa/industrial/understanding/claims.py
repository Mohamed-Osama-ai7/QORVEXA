"""Atomic semantic claims (deterministic, compositional rules extractor + shared cue detection).

A claim is built from independent evidence: quantity mentions, concept hints / confirmed terms,
entities and semantic cues. It is typed (fact, actual, target, historical, hypothetical,
conditional, assumption, recommendation, question), carries negation, scope and an exact quote with
source offsets. This extractor is a transparent baseline, not a claim of general language
understanding; richer extraction comes from a provider-backed extractor whose output goes through
the same independent grounding.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from qorvexa.industrial.understanding import lexicon as lx
from qorvexa.industrial.understanding.entities import TermUse
from qorvexa.industrial.understanding.models import (
    MAX_CLAIMS,
    Claim,
    EntityMention,
    Predicate,
    QuantityMention,
    Segment,
    SemanticType,
    SourceDocument,
    UnsupportedFeature,
)
from qorvexa.industrial.understanding.numbers import find_quantities
from qorvexa.industrial.understanding.source import Normalized

_CONCEPT_PRED = {
    "concept:capacity": Predicate.CAPACITY,
    "concept:availability": Predicate.CAPACITY,
    "concept:production_output": Predicate.PRODUCES_RATE,
    "concept:throughput_actual": Predicate.PRODUCES_RATE,
    "concept:demand_order": Predicate.DEMAND,
    "concept:margin": Predicate.MARGIN_PER_UNIT,
    "concept:cost": Predicate.COST_PER_UNIT,
    "concept:price": Predicate.PRICE,
    "concept:scrap_waste": Predicate.SCRAP_LEVEL,
    "concept:defect": Predicate.DEFECT_LEVEL,
    "concept:machine_downtime": Predicate.DOWNTIME,
    "concept:consumption": Predicate.CONSUMPTION_RATE,
}
_QUALITATIVE = {
    "concept:machine_downtime": Predicate.DOWNTIME,
    "concept:intermittent_stops": Predicate.INTERMITTENT_STOPS,
    "concept:slow_operation": Predicate.SLOW_OPERATION,
    "concept:material_shortage": Predicate.MATERIAL_SHORTAGE,
    "concept:bottleneck": Predicate.BOTTLENECK,
    "concept:jam": Predicate.JAMMING,
    "concept:cannot_keep_up": Predicate.CANNOT_KEEP_UP,
    "concept:scrap_waste": Predicate.SCRAP_LEVEL,
    "concept:defect": Predicate.DEFECT_LEVEL,
}
_TIME = re.compile(
    r"(?:من الصبح|من الصبحيه|طول اليوم|since (?:the )?morning|all day|since \w+|today|النهارده|"
    r"اليوم|امبارح|yesterday)"
)


@dataclass(frozen=True)
class Cues:
    semantic_type: SemanticType
    negated: bool
    negation_cues: tuple[str, ...]
    bound: str | None
    condition_at: int | None  # normalized offset where a condition clause starts


def detect_cues(norm: str, blocked: tuple[tuple[int, int], ...] = ()) -> Cues:
    """Semantic cues of one normalized clause. ``blocked`` spans (phrases that contain a negation
    word but assert something else, e.g. "مش لاحق") are ignored for negation."""
    st = SemanticType.FACT
    cond_at: int | None = None
    if "?" in norm:
        st = SemanticType.QUESTION
    elif (hyp := lx.find_cue(norm, lx.HYPOTHETICAL_CUES)) is not None:
        st = SemanticType.HYPOTHETICAL
        cond_at = hyp[1]
    elif (cond := lx.find_cue(norm, lx.CONDITIONAL_CUES)) is not None:
        st = SemanticType.CONDITIONAL
        cond_at = cond[1]
    elif lx.find_cue(norm, lx.ASSUMPTION_CUES):
        st = SemanticType.ASSUMPTION
    elif lx.find_cue(norm, lx.RECOMMENDATION_CUES):
        st = SemanticType.RECOMMENDATION
    elif lx.find_cue(norm, lx.HISTORICAL_CUES):
        st = SemanticType.HISTORICAL
    elif lx.find_cue(norm, lx.TARGET_CUES):
        st = SemanticType.TARGET
    elif lx.find_cue(norm, lx.ACTUAL_CUES):
        st = SemanticType.ACTUAL_MEASUREMENT
    negs: list[str] = []
    for cue in lx.NEGATION_CUES:
        pat = r"n't\b" if cue == "n't" else rf"(?<![a-z0-9؀-ۿ']){re.escape(cue)}(?![a-z0-9؀-ۿ])"
        for m in re.finditer(pat, norm):
            if not any(a <= m.start() < b for a, b in blocked):
                negs.append(cue)
    bound = None
    if lx.find_cue(norm, lx.MIN_CUES):
        bound = "MIN"
    elif lx.find_cue(norm, lx.MAX_CUES):
        bound = "MAX"
    return Cues(st, bool(negs), tuple(sorted(set(negs))), bound, cond_at)


def _claim_id(source_id: str, start: int, predicate: str, value: str, subject: str) -> str:
    h = hashlib.sha256(f"{source_id}|{start}|{predicate}|{value}|{subject}".encode()).hexdigest()
    return h[:16]


def _nearest(items: list[tuple[int, object]], pos: int, before: bool = False) -> object | None:
    """Closest item preceding ``pos`` (the usual "<concept> ... <number>" order); if none
    precedes, the closest following one."""
    prior = [(pos - p, o) for p, o in items if p <= pos]
    if prior:
        return min(prior, key=lambda c: c[0])[1]
    cand = [(p - pos, o) for p, o in items]
    return min(cand, key=lambda c: c[0])[1] if cand else None


def extract_segment_claims(
    doc: SourceDocument,
    seg: Segment,
    norm: Normalized,
    entities: list[EntityMention],
    uses: list[TermUse],
    prev_subject: EntityMention | None,
    provenance: str = "rules/1",
) -> tuple[list[Claim], list[UnsupportedFeature]]:
    text = doc.text
    nspans = {e.mention_id: _nspan(e, seg, norm) for e in entities}
    # a factory-confirmed (or declared-ambiguous) term overrides the generic hint on its span:
    # the factory's meaning wins, and an ambiguous term never falls back to a generic guess
    term_spans = tuple(
        (u.start, u.end) for u in uses if u.resolution.status.value in {"RESOLVED", "AMBIGUOUS"}
    )
    hints = lx.find_hints(seg.norm, term_spans)
    # confirmed-term concepts count as hints (trusted meaning), generic hints stay hints
    term_hints = [
        lx.HintMatch(u.resolution.surface, u.resolution.concept, u.start, u.end)
        for u in uses
        if u.resolution.concept and u.resolution.status.value == "RESOLVED"
    ]
    all_hints = sorted([*hints, *term_hints], key=lambda h: h.start)
    feats = [h for h in all_hints if h.ref.startswith("feature:")]
    concepts = [h for h in all_hints if not h.ref.startswith("feature:")]
    unsupported: list[UnsupportedFeature] = []
    for h in feats:
        a, b = norm.orig_span(seg.norm_start + h.start, seg.norm_start + h.end)
        unsupported.append(
            UnsupportedFeature(
                feature=h.ref.split(":", 1)[1], quote=text[a:b], orig_start=a, orig_end=b,
                source_id=doc.source_id,
            )
        )  # fmt: skip
    blocked = tuple((h.start, h.end) for h in concepts if h.ref == "concept:cannot_keep_up"
                    or h.expression.startswith(("مفيش", "مش")))  # fmt: skip
    cues = detect_cues(seg.norm, blocked)
    quantities = find_quantities(seg.norm, norm.to_orig, text, seg.norm_start, doc.default_currency)
    spans = [(e.orig_start, e.orig_end) for e in entities]
    quantities = [
        q for q in quantities if not any(a <= q.orig_start < b for a, b in spans)
    ]  # numbers inside an entity name ("ماكينة 3") are identifiers, not quantities
    q_pos = {id(q): _npos(q, seg, norm) for q in quantities}

    shift = next((e for e in entities if e.entity_type == "SHIFT"), None)
    line = next((e for e in entities if e.entity_type == "LINE" and e.canonical_id), None)
    non_shift = [e for e in entities if e.entity_type not in {"SHIFT", "OPERATION"}]
    time_m = _TIME.search(seg.norm)
    time_text = None
    if time_m:
        a, b = norm.orig_span(seg.norm_start + time_m.start(), seg.norm_start + time_m.end())
        time_text = text[a:b]
    condition = None
    if cues.condition_at is not None:
        a, _ = norm.orig_span(seg.norm_start + cues.condition_at, seg.norm_end)
        condition = text[a : seg.orig_end]

    def subject_for(pos: int) -> tuple[EntityMention | None, bool]:
        pre = [(nspans[e.mention_id], e) for e in non_shift if nspans[e.mention_id] <= pos]
        if pre:
            return max(pre, key=lambda x: x[0])[1], False
        if non_shift:
            return non_shift[0], False
        if prev_subject is not None:
            return prev_subject, True
        return None, False

    claims: list[Claim] = []
    used_hints: set[int] = set()

    def build(
        pred: Predicate, pos: int, value: QuantityMention | None, qualitative: bool,
        refs: tuple[str, ...],
    ) -> None:  # fmt: skip
        subj, ctx_flag = subject_for(pos)
        related = next(
            (e for e in non_shift if subj is not None and e.mention_id != subj.mention_id), None
        )
        if pred is Predicate.CONSUMPTION_RATE and subj is None:
            ctx_flag = False
        stype = cues.semantic_type
        vtxt = (
            "none"
            if value is None
            else f"{value.low}-{value.high}"
            if value and value.high
            else (value.low if value else "")
        )
        cid = _claim_id(
            doc.source_id,
            seg.orig_start,
            pred.value,
            f"{vtxt}@{pos}",
            subj.mention_id if subj else "",
        )
        term_ids = tuple(sorted({u.resolution.term_id for u in uses if u.resolution.term_id}))
        subj_text = subj.text if subj else None
        claims.append(
            Claim(
                claim_id=cid, source_id=doc.source_id, turn=doc.turn, subject_text=subj_text,
                subject_entity=subj.mention_id if subj else None, subject_from_context=ctx_flag,
                related_text=related.text if related else None,
                related_entity=related.mention_id if related else None, predicate=pred,
                value=value, qualitative=qualitative, semantic_type=stype, negated=cues.negated,
                modality="ASSERTED" if stype is SemanticType.FACT else "UNKNOWN",
                bound=cues.bound, condition=condition,
                scope_shift=shift.canonical_id if shift else None,
                scope_line=line.canonical_id if line else None, time_text=time_text,
                normalized=f"{subj_text or '?'} | {pred.value} | {vtxt or '-'} | {stype.value}"
                f"{' | NEGATED' if cues.negated else ''}",
                quote=seg.text, orig_start=seg.orig_start, orig_end=seg.orig_end, term_ids=term_ids,
                concept_refs=refs, provenance=provenance, speaker_role=doc.speaker_role,
            )
        )  # fmt: skip

    for q in quantities:
        qp = q_pos[id(q)]
        best = _nearest([(h.start, h) for h in concepts if h.ref in _CONCEPT_PRED], qp)
        pred = Predicate.UNKNOWN
        refs: tuple[str, ...] = ()
        if best is not None and isinstance(best, lx.HintMatch):
            pred, refs = _CONCEPT_PRED[best.ref], (best.ref,)
            used_hints.add(id(best))
        elif q.unit == "piece" and cues.semantic_type is SemanticType.TARGET:
            pred = Predicate.PRODUCES_RATE
        elif q.kind == "PERCENT" and any(
            h.ref in {"concept:scrap_waste", "concept:defect"} for h in concepts
        ):
            pred = Predicate.SCRAP_LEVEL
        build(pred, qp, q, False, refs)
    above = any(h.ref == "concept:above_normal" for h in concepts)
    consuming = any(h.ref == "concept:consumption" for h in concepts)
    for h in concepts:
        if id(h) in used_hints or (h.ref not in _QUALITATIVE and h.ref != "concept:consumption"):
            continue
        if h.ref == "concept:consumption":
            if above and not quantities:
                build(
                    Predicate.EXCESS_CONSUMPTION,
                    h.start,
                    None,
                    True,
                    (h.ref, "concept:above_normal"),
                )
            continue
        if h.ref == "concept:material_shortage" and consuming and above:
            continue
        build(_QUALITATIVE[h.ref], h.start, None, True, (h.ref,))
    return claims[:MAX_CLAIMS], unsupported


def _nspan(e: EntityMention, seg: Segment, norm: Normalized) -> int:
    """Position of an entity mention inside the segment's normalized text."""
    try:
        return norm.to_orig.index(e.orig_start) - seg.norm_start
    except ValueError:
        return 0


def _npos(q: QuantityMention, seg: Segment, norm: Normalized) -> int:
    try:
        return norm.to_orig.index(q.orig_start) - seg.norm_start
    except ValueError:
        return 0

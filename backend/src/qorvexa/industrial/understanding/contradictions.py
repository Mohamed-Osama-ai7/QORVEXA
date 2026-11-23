"""Contradiction analysis. Conflicts are preserved, never resolved.

Two claims conflict only when they describe the SAME subject, predicate, scope (shift/line/time),
semantic class and bound but disagree on the exact value or unit dimension. Different machines,
shifts, scenarios, targets-vs-actuals, historical, conditional and hypothetical statements are
distinct and never contradict one another.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from qorvexa.industrial.understanding.models import (
    Claim,
    Contradiction,
    EntityMention,
    Grounding,
    GroundingResult,
    Predicate,
    SemanticType,
)
from qorvexa.industrial.units import Compat, Quantity, UnitError, classify, parse_unit

_COMPARABLE = {SemanticType.FACT, SemanticType.ACTUAL_MEASUREMENT, SemanticType.TARGET}


def _subject_key(c: Claim, ents: dict[str, EntityMention]) -> str:
    e = ents.get(c.subject_entity or "")
    if e is not None and e.canonical_id:
        return f"id:{e.canonical_id}"
    return "txt:" + (c.subject_text or "?").lower()


def _scope(c: Claim) -> tuple[str | None, str | None, str | None, str | None]:
    return (c.scope_shift, c.scope_line, c.time_text, c.bound)


def find_contradictions(
    claims: list[Claim],
    grounding: dict[str, GroundingResult],
    entities: list[EntityMention],
) -> list[Contradiction]:
    ents = {e.mention_id: e for e in entities}
    usable = [
        c
        for c in claims
        if grounding[c.claim_id].outcome in (Grounding.GROUNDED, Grounding.PARTIALLY_GROUNDED)
        and c.value is not None
        and c.predicate is not Predicate.UNKNOWN
        and not c.negated
        and c.semantic_type in _COMPARABLE
    ]
    groups: dict[tuple[object, ...], list[Claim]] = defaultdict(list)
    for c in usable:
        # actual measurements and plain facts are the same class; targets are separate
        cls = "TARGET" if c.semantic_type is SemanticType.TARGET else "ACTUAL"
        groups[(_subject_key(c, ents), c.predicate, cls, _scope(c))].append(c)
    out: list[Contradiction] = []
    for key, members in groups.items():
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                if a.value is None or b.value is None:
                    continue
                same = (a.value.kind, a.value.low, a.value.high, a.value.unit, a.value.per) == (
                    b.value.kind, b.value.low, b.value.high, b.value.unit, b.value.per
                )  # fmt: skip
                if same:
                    out.append(
                        Contradiction(
                            code="DUPLICATE", claim_ids=(a.claim_id, b.claim_id),
                            description=f"{key[0]} {key[1]}: the same statement appears twice",
                        )
                    )  # fmt: skip
                    continue
                code, desc = _compare(a, b, str(key[0]), str(key[1]))
                if code:
                    out.append(
                        Contradiction(
                            code=code, claim_ids=(a.claim_id, b.claim_id), description=desc
                        )
                    )
    return sorted(out, key=lambda x: (x.code, x.claim_ids))


def _compare(a: Claim, b: Claim, subj: str, pred: str) -> tuple[str, str]:
    va, vb = a.value, b.value
    if va is None or vb is None:  # pragma: no cover - callers guarantee values
        return "", ""
    if va.per != vb.per:
        return "UNIT_CONFLICT", f"{subj} {pred}: different period bases ({va.per} vs {vb.per})"
    if va.unit is None or vb.unit is None:
        if va.unit != vb.unit:
            return "UNIT_CONFLICT", f"{subj} {pred}: one value has no unit"
        return _numbers(va, vb, subj, pred)
    try:
        ua, ub = parse_unit(va.unit), parse_unit(vb.unit)
        compat = classify(ua, ub)
        if compat not in (Compat.SAME_UNIT, Compat.CONVERTIBLE):
            return "UNIT_CONFLICT", f"{subj} {pred}: incompatible units {va.unit} vs {vb.unit}"
        if va.kind == "RANGE" or vb.kind == "RANGE":
            return _numbers(va, vb, subj, pred)
        qa, qb = Quantity.of(va.low, ua), Quantity.of(vb.low, ub)
        if qa.compare(qb) != 0:
            return "QUANTITY_CONFLICT", (
                f"{subj} {pred}: {va.low} {va.unit} vs {vb.low} {vb.unit} (same scope)"
            )
    except UnitError as exc:  # pragma: no cover - defensive
        return "UNIT_CONFLICT", f"{subj} {pred}: {exc.message}"
    return "", ""


def _numbers(va: object, vb: object, subj: str, pred: str) -> tuple[str, str]:
    a, b = va, vb
    lo_a, hi_a = Decimal(a.low), Decimal(a.high or a.low)  # type: ignore[attr-defined]
    lo_b, hi_b = Decimal(b.low), Decimal(b.high or b.low)  # type: ignore[attr-defined]
    if hi_a < lo_b or hi_b < lo_a:
        return "QUANTITY_CONFLICT", f"{subj} {pred}: disjoint values/ranges (same scope)"
    if (lo_a, hi_a) != (lo_b, hi_b):
        return "QUANTITY_CONFLICT", f"{subj} {pred}: overlapping but different ranges"
    return "", ""

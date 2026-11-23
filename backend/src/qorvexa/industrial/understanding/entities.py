# ruff: noqa: E501
"""Entity recognition: confirmed factory terms first, explicit designators second, generic nouns last.

No authoritative entity is ever created from an uncertain expression: only a CONFIRMED terminology
entry with an entity type yields a canonical id. Explicit designators ("Machine A", "line 2",
"second shift") are PATTERN_UNCONFIRMED; generic nouns ("the filling machine") stay
UNRESOLVED_GENERIC; ambiguous or proposal-only terms are surfaced as such.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from qorvexa.industrial.understanding.lexicon import n
from qorvexa.industrial.understanding.models import EntityMention, Segment, SourceDocument
from qorvexa.industrial.understanding.source import Normalized
from qorvexa.industrial.understanding.terminology import (
    ResolutionContext,
    ResolutionStatus,
    TermEntry,
    TermResolution,
    TermStatus,
    resolve_surface,
)

_AR = r"(?<![؀-ۿ])(?:وال|بال|لل|ال|و|ب|ل|ف)?"
_ORD = {
    "first": "1", "1st": "1", "second": "2", "2nd": "2", "third": "3", "3rd": "3", "night": "3",
    "الاول": "1", "الاولي": "1", "الاولى": "1", "التاني": "2", "الثاني": "2", "التانيه": "2",
    "التالت": "3", "الثالث": "3", "الليلي": "3", "الليلية": "3",
}  # fmt: skip
_SHIFT_EN = re.compile(r"(?<![a-z0-9])(first|1st|second|2nd|third|3rd|night)\s+shift(?![a-z])")
_SHIFT_EN_N = re.compile(r"(?<![a-z0-9])shift\s*#?\s*(\d)(?![0-9])")
_SHIFT_AR = re.compile(
    _AR
    + r"(?:شيفت|وردية|ورديه)\s+(الاول|الاولي|الاولى|التاني|الثاني|التانيه|التالت|الثالث|الليلي|الليلية|\d)"
)
_ID = r"([a-z]|[a-z]-?\d{1,4}|\d{1,4})(?![a-z0-9])"
_MACHINE_EN = re.compile(r"(?<![a-z0-9])machine\s+" + _ID)
_MACHINE_CODE = re.compile(r"(?<![a-z0-9])(m-?\d{2,4})(?![a-z0-9])")
_MACHINE_AR = re.compile(_AR + r"(?:ماكينه|ماكينة|مكنه|مكنة)\s+" + _ID)
_LINE = re.compile(
    r"(?<![a-z0-9])line\s*#?\s*(\d{1,3}|[a-z])(?![a-z0-9])|" + _AR + r"(?:الخط|خط)\s*(\d{1,3})"
)
_GENERIC_EN = re.compile(
    r"(?<![a-z0-9])((?:[a-z]{3,}\s+)?(?:machine|line|press|oven|mixer|conveyor))(?![a-z])"
)
_GENERIC_AR = re.compile(_AR + r"(ماكينه|ماكينة|مكنه|مكنة|الخط|خط)(?![؀-ۿ\w])")
_STAGE = re.compile(_AR + r"(?:مرحله|مرحلة)\s+([ء-ي]+)|(?<![a-z])(\w+)\s+stage(?![a-z])")


@dataclass(frozen=True)
class TermUse:
    """A terminology match in a segment (concept evidence, possibly entity-typed)."""

    resolution: TermResolution
    start: int  # normalized offsets inside the segment
    end: int


def _ctx(doc: SourceDocument, machines: tuple[str, ...], around: str) -> ResolutionContext:
    return ResolutionContext(
        factory_id=doc.factory_id,
        line_id=doc.line_id,
        department=doc.department,
        machine_ids=machines,
        speaker_role=doc.speaker_role,
        surrounding_text=around,
    )


def _term_matches(seg: Segment, entries: list[TermEntry]) -> list[tuple[str, int, int]]:
    """(surface, start, end), longest first and non-overlapping, for every entry surface."""
    cand: list[tuple[str, int, int]] = []
    surfaces = sorted(
        {
            s
            for e in entries
            if e.status in (TermStatus.CONFIRMED, TermStatus.PROPOSED)
            for s in e.surfaces()
            if s
        },
        key=lambda s: -len(s),
    )
    taken: list[tuple[int, int]] = []
    for s in surfaces:
        if s.isascii() and re.match(r"[a-z0-9]", s):
            pat = rf"(?<![a-z0-9]){re.escape(s)}(?![a-z0-9])"
        else:
            pat = rf"(?<![؀-ۿ]){re.escape(s)}(?![؀-ۿ])"
        for m in re.finditer(pat, seg.norm):
            if any(m.start() < b and a < m.end() for a, b in taken):
                continue
            taken.append((m.start(), m.end()))
            cand.append((s, m.start(), m.end()))
    return sorted(cand, key=lambda c: c[1])


def find_entities(
    doc: SourceDocument,
    seg: Segment,
    norm: Normalized,
    entries: list[TermEntry],
    machine_ids: tuple[str, ...] = (),
) -> tuple[list[EntityMention], list[TermUse]]:
    mentions: list[EntityMention] = []
    uses: list[TermUse] = []
    taken: list[tuple[int, int]] = []

    def add(
        a: int, b: int, etype: str, status: str, *, canonical: str | None = None,
        term_id: str | None = None, alts: tuple[str, ...] = (),
    ) -> None:  # fmt: skip
        if any(a < y and x < b for x, y in taken):
            return
        taken.append((a, b))
        o0, o1 = norm.orig_span(seg.norm_start + a, seg.norm_start + b)
        mentions.append(
            EntityMention(
                mention_id=f"{seg.seg_id}:{a}",
                text=doc.text[o0:o1],
                orig_start=o0,
                orig_end=o1,
                entity_type=etype,
                status=status,
                canonical_id=canonical,
                term_id=term_id,
                alternatives=alts,
            )
        )

    for surface, a, b in _term_matches(seg, entries):
        res = resolve_surface(entries, surface, _ctx(doc, machine_ids, seg.norm))
        uses.append(TermUse(res, a, b))
        etype = res.entity_type or next(
            (e.entity_type for e in entries if surface in e.surfaces() and e.entity_type), None
        )
        if res.status is ResolutionStatus.RESOLVED and res.entity_type:
            add(
                a,
                b,
                res.entity_type,
                "CONFIRMED_TERM",
                canonical=res.canonical_id,
                term_id=res.term_id,
            )
        elif res.status is ResolutionStatus.AMBIGUOUS:
            add(a, b, etype or "UNKNOWN", "AMBIGUOUS", term_id=res.term_id, alts=res.candidates)
        elif res.status is ResolutionStatus.PROPOSED_ONLY:
            add(a, b, etype or "UNKNOWN", "PROPOSED_ONLY", alts=res.candidates)
        elif res.status is ResolutionStatus.RESOLVED:
            taken.append((a, b))  # concept term: evidence only, not an entity

    s = seg.norm
    for m in _SHIFT_EN.finditer(s):
        add(
            m.start(),
            m.end(),
            "SHIFT",
            "PATTERN_UNCONFIRMED",
            canonical=f"shift:{_ORD[m.group(1)]}",
        )
    for m in _SHIFT_EN_N.finditer(s):
        add(m.start(), m.end(), "SHIFT", "PATTERN_UNCONFIRMED", canonical=f"shift:{m.group(1)}")
    for m in _SHIFT_AR.finditer(s):
        g = m.group(1)
        add(m.start(), m.end(), "SHIFT", "PATTERN_UNCONFIRMED", canonical=f"shift:{_ORD.get(g, g)}")
    for m in _MACHINE_EN.finditer(s):
        add(m.start(), m.end(), "MACHINE", "PATTERN_UNCONFIRMED")
    for m in _MACHINE_AR.finditer(s):
        add(m.start(), m.end(), "MACHINE", "PATTERN_UNCONFIRMED")
    for m in _MACHINE_CODE.finditer(s):
        add(m.start(), m.end(), "MACHINE", "PATTERN_UNCONFIRMED")
    for m in _LINE.finditer(s):
        add(m.start(), m.end(), "LINE", "PATTERN_UNCONFIRMED")
    for m in _STAGE.finditer(s):
        add(m.start(), m.end(), "OPERATION", "UNRESOLVED_GENERIC")
    for m in _GENERIC_EN.finditer(s):
        t = "LINE" if m.group(1).endswith("line") else "MACHINE"
        add(m.start(), m.end(), t, "UNRESOLVED_GENERIC")
    for m in _GENERIC_AR.finditer(s):
        t = "LINE" if "خط" in m.group(1) else "MACHINE"
        add(m.start(), m.end(), t, "UNRESOLVED_GENERIC")
    mentions.sort(key=lambda e: e.orig_start)
    return mentions, uses


__all__ = ["TermUse", "find_entities", "n"]

# ruff: noqa: RUF001
"""Source processing: verbatim preservation, search-only normalization with exact offset maps,
clause segmentation and (heuristic) language/script/Arabizi detection.

Normalization NEVER replaces the source. Every normalized span maps back to original offsets.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from qorvexa.industrial.understanding.models import (
    MAX_CHARS,
    MAX_SEGMENTS,
    LanguageInfo,
    Segment,
    SourceDocument,
)


class UnderstandingInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


_DIGITS = {c: str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")} | {
    c: str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")
}
_ALEF = dict.fromkeys("أإآٱ", "ا")
_PUNCT = {"،": ",", "؟": "?", "؛": ";", "٫": ".", "٬": ",", "٪": "%"}
_DIACRITICS = {chr(c) for c in range(0x064B, 0x0660)} | {"ٰ", "ـ"}  # incl. tatweel


@dataclass(frozen=True)
class Normalized:
    text: str
    to_orig: tuple[int, ...]  # normalized index -> original index

    def orig_span(self, start: int, end: int) -> tuple[int, int]:
        if not 0 <= start < end <= len(self.text):
            raise UnderstandingInputError("INVALID_SPAN", f"normalized span {start}:{end}")
        return self.to_orig[start], self.to_orig[end - 1] + 1


def is_arabic(ch: str) -> bool:
    return "؀" <= ch <= "ۿ" or "ݐ" <= ch <= "ݿ"


def normalize(text: str) -> Normalized:
    out: list[str] = []
    idx: list[int] = []
    prev_space = False
    for i, ch in enumerate(text):
        if ch in _DIACRITICS:
            continue
        c = _DIGITS.get(ch) or _ALEF.get(ch) or _PUNCT.get(ch) or ch
        if c == "ى":
            c = "ي"
        c = c.lower()
        if c.isspace():
            if prev_space or not out:
                continue
            c, prev_space = " ", True
        else:
            prev_space = False
        out.append(c)
        idx.append(i)
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    return Normalized("".join(out), tuple(idx))


_TERM = re.compile(r"(?<!\d)[.!?;\n]+(?!\d)|[.!?;\n]+$")
_CONTRAST = re.compile(r"\s(?:but|however|while|whereas|لكن|بس|بينما)\s")


def validate_document(doc: SourceDocument) -> None:
    if not doc.text.strip():
        raise UnderstandingInputError("EMPTY_INPUT", "source text is empty")
    if len(doc.text) > MAX_CHARS:
        raise UnderstandingInputError("INPUT_TOO_LARGE", f"more than {MAX_CHARS} characters")
    if "\x00" in doc.text:
        raise UnderstandingInputError("INVALID_INPUT", "NUL character in source")
    if not doc.factory_id.strip() or not doc.source_id.strip():
        raise UnderstandingInputError("INVALID_INPUT", "factory_id and source_id are required")


def source_sha256(doc: SourceDocument) -> str:
    return hashlib.sha256(doc.text.encode("utf-8")).hexdigest()


def segment(doc: SourceDocument) -> tuple[Normalized, tuple[Segment, ...]]:
    """Split into clauses; each keeps its exact original slice and normalized offsets."""
    validate_document(doc)
    norm = normalize(doc.text)
    # sentence terminators stay INSIDE their clause (a trailing "?" marks a question);
    # contrast words are separators and are dropped
    seps = sorted(
        [(m.end(), m.end()) for m in _TERM.finditer(norm.text)]
        + [(m.start(), m.end()) for m in _CONTRAST.finditer(norm.text)]
    )
    cuts = [0]
    for a, b in seps:
        cuts.extend([a, b])
    cuts.append(len(norm.text))
    segs: list[Segment] = []
    for a, b in zip(cuts[::2], cuts[1::2], strict=False):
        piece = norm.text[a:b]
        lead = len(piece) - len(piece.lstrip(" ,.;"))
        a2, b2 = a + lead, b - (len(piece) - len(piece.rstrip(" ,")))
        if a2 >= b2:
            continue
        o_start, o_end = norm.orig_span(a2, b2)
        segs.append(
            Segment(
                seg_id=f"{doc.source_id}#{len(segs)}",
                orig_start=o_start,
                orig_end=o_end,
                text=doc.text[o_start:o_end],
                norm=norm.text[a2:b2],
                norm_start=a2,
                norm_end=b2,
            )
        )
    if len(segs) > MAX_SEGMENTS:
        raise UnderstandingInputError("INPUT_TOO_LARGE", f"more than {MAX_SEGMENTS} segments")
    return norm, tuple(segs)


# ---------------------------------------------------------------------------- language

COLLOQUIAL = frozenset(
    [
        "مش",
        "بتاع",
        "بتاعت",
        "ده",
        "دي",
        "دول",
        "كده",
        "لسه",
        "عندنا",
        "بتسحب",
        "بيعمل",
        "بتقف",
        "مفيش",
        "ازاي",
        "ايه",
        "كام",
        "عشان",
        "برضه",
        "الاوردر",
        "الشيفت",
        "التاني",
        "التانيه",
        "النهارده",
        "دلوقتي",
        "اللي",
        "فيه",
        "هالك",
        "واقفه",
        "واقفة",
        "لاحق",
    ]
)
FORMAL = frozenset(["يتم", "حيث", "لذلك", "ينبغي", "وفقا", "بموجب", "يجب", "علاوه", "علاوة"])
_ARABIZI_LEX = frozenset(
    [
        "el",
        "ba2a",
        "3ayez",
        "3ayz",
        "bet2af",
        "bet2ef",
        "shwaya",
        "kol",
        "7aga",
        "ma3",
        "msh",
        "mesh",
        "ya",
    ]
)
_ARABIZI = re.compile(r"(?:[a-z]+[235789][a-z]+|[3579][a-z]{2,})")
_UNIT_SUFFIX = re.compile(r"^\d+(?:pm|am|kg|mm|cm|hr|min|st|nd|rd|th|kw|mw|tons?|l)$")
_WORD = re.compile(r"[a-z0-9]+|[؀-ۿ]+")


def detect_language(seg: Segment) -> LanguageInfo:
    ar = sum(1 for c in seg.text if is_arabic(c))
    lat = sum(1 for c in seg.text if c.isascii() and c.isalpha())
    words = _WORD.findall(seg.norm)
    arabizi_hits = [w for w in words if w in _ARABIZI_LEX and w.isascii()]
    arabizi_hits += [
        w for w in _ARABIZI.findall(seg.norm) if w not in arabizi_hits and not _UNIT_SUFFIX.match(w)
    ]
    arabizi = bool(arabizi_hits) and (
        bool([w for w in arabizi_hits if any(d in w for d in "235789") or w in {"shwaya", "el"}])
        or len(arabizi_hits) >= 2
    )
    if ar and lat:
        primary = "MIXED"
    elif ar:
        primary = "ARABIC"
    elif lat:
        primary = "LATIN"
    else:
        primary = "OTHER"
    col = sorted(set(words) & COLLOQUIAL) + (["<arabizi>"] if arabizi else [])
    frm = sorted(set(words) & {w.replace("ـ", "") for w in FORMAL})
    register = "UNDETERMINED"
    if col and len(col) >= len(frm):
        register = "EGYPTIAN_COLLOQUIAL_MARKERS"
    elif frm:
        register = "FORMAL_MARKERS"
    return LanguageInfo(
        seg_id=seg.seg_id,
        arabic_chars=ar,
        latin_chars=lat,
        primary=primary,
        code_switching=bool(ar and lat),
        arabizi=arabizi,
        register_hint=register,
        markers=tuple(col or frm),
    )

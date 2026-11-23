# ruff: noqa: RUF001
"""Numeric understanding: digits, Arabic/English spoken numbers, ranges, percentages, rates, units.

Exact decimals only (never floats). Ambiguity is reported, not resolved: ``1,500`` / ``1.500``
carry alternatives; an absent number is not a zero; an explicit zero is flagged. Unit text is
mapped through a small explicit table and then parsed by the exact Industrial Units Engine; unknown
units stay unknown. Currency words are accepted only with factory context.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from qorvexa.industrial.understanding.models import QuantityMention
from qorvexa.industrial.units import UnitError, parse_unit

_AR_UNITS = {
    1: "واحد", 2: "اتنين", 3: "تلاته", 4: "اربعه", 5: "خمسه", 6: "سته", 7: "سبعه", 8: "تمانيه",
    9: "تسعه", 10: "عشره",
}  # fmt: skip
_WORDS: dict[str, int] = {
    **{w: v for v, w in _AR_UNITS.items()},
    "اثنين": 2, "اثنان": 2, "ثلاثه": 3, "ثلاثة": 3, "تلاتة": 3, "تلات": 3, "اربعة": 4, "اربع": 4,
    "خمسة": 5, "خمس": 5, "ستة": 6, "ست": 6, "سبعة": 7, "سبع": 7, "تمانية": 8, "تمان": 8,
    "ثمانية": 8, "ثمانيه": 8, "تسعة": 9, "تسع": 9, "عشرة": 10, "حداشر": 11, "اتناشر": 12,
    "عشرين": 20, "تلاتين": 30, "ثلاثين": 30, "اربعين": 40, "خمسين": 50, "ستين": 60, "سبعين": 70,
    "تمانين": 80, "ثمانين": 80, "تسعين": 90, "مية": 100, "ميه": 100, "مائه": 100, "مائة": 100,
    "مئة": 100, "مئه": 100, "ميتين": 200, "مئتين": 200, "مائتين": 200, "تلتمية": 300, "تلتميه": 300,
    "ثلاثمائة": 300, "اربعمية": 400, "خمسمية": 500, "خمسميه": 500, "ستمية": 600, "سبعمية": 700,
    "تمنمية": 800, "تسعمية": 900, "صفر": 0,
}  # fmt: skip
_THOUSAND = {"الف": 1000, "الاف": 1000, "آلاف": 1000}
_DUAL_THOUSAND = {"الفين": 2000}
_EN = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}  # fmt: skip
_NUM_RE = re.compile(r"(?<![\w.,])\d[\d.,]*\d|\d")
_RANGE_CONN = re.compile(
    r"^\s*(?:-|–|to|and|الي|الى|لـ|ل|و|until)\s*$|^\s*(?:من|from|between)?\s*$", re.IGNORECASE
)

UNIT_WORDS = {
    "units": "piece", "unit": "piece", "pcs": "piece", "pc": "piece", "pieces": "piece",
    "piece": "piece", "قطعه": "piece", "قطعة": "piece", "قطع": "piece", "وحده": "piece",
    "وحدة": "piece", "وحدات": "piece", "حبه": "piece", "حبة": "piece",
    "kg": "kg", "كجم": "kg", "كيلو": "kg", "كيلوجرام": "kg", "kilo": "kg", "kilos": "kg",
    "طن": "tonne", "tons": "AMBIGUOUS", "ton": "AMBIGUOUS", "tonne": "tonne", "tonnes": "tonne",
    "ساعه": "h", "ساعة": "h", "ساعات": "h", "hours": "h", "hour": "h", "hr": "h", "h": "h",
    "دقيقه": "min", "دقيقة": "min", "دقايق": "min", "minutes": "min", "min": "min",
    "يوم": "day", "ايام": "day", "days": "day", "day": "day", "لتر": "L", "liters": "L",
    "liter": "L", "l": "L", "متر": "m", "meters": "m", "m": "m",
}  # fmt: skip
CURRENCY_WORDS = {  # word -> (ISO, needs factory-context confirmation)
    "جنيه": ("EGP", True), "جنيهات": ("EGP", True), "egp": ("EGP", False), "usd": ("USD", False),
    "دولار": ("USD", True), "eur": ("EUR", False), "يورو": ("EUR", False), "sar": ("SAR", False),
}  # fmt: skip
PER_WORDS = {
    "shift": "shift", "شيفت": "shift", "الشيفت": "shift", "وردية": "shift", "hour": "hour",
    "ساعة": "hour", "ساعه": "hour", "day": "day", "يوم": "day", "اليوم": "day", "week": "week",
    "اسبوع": "week", "الاسبوع": "week", "month": "month", "شهر": "month",
}  # fmt: skip
_PER_CUE = {"per", "/", "في", "كل", "لكل", "ب", "بال", "each", "a"}
_ZERO = {"0", "صفر", "zero"}


def _digits_value(tok: str) -> tuple[str, bool, tuple[str, ...], tuple[str, ...]]:
    """(canonical decimal, ambiguous, alternatives, notes) for a digit token."""
    notes: list[str] = []
    if re.fullmatch(r"\d+", tok):
        return tok.lstrip("0") or "0", False, (), ()
    for sep in (",", "."):
        parts = tok.split(sep)
        if len(parts) > 2 and all(p.isdigit() for p in parts):
            if all(len(p) == 3 for p in parts[1:]) and 1 <= len(parts[0]) <= 3:
                return "".join(parts), False, (), ("thousands grouping",)
            return tok, True, (), ("malformed grouping",)
    if tok.count(",") and tok.count("."):
        last = max(tok.rfind(","), tok.rfind("."))
        whole = tok[:last].replace(",", "").replace(".", "")
        return f"{whole}.{tok[last + 1 :]}", False, (), ()
    sep = "," if "," in tok else "."
    a, b = tok.split(sep)
    if len(b) == 3 and 1 <= len(a) <= 3:
        thousands, decimal = f"{a}{b}", f"{a}.{b}"
        return thousands, True, (thousands, decimal), ("separator could be thousands or decimal",)
    notes.append("decimal separator")
    return f"{a}.{b}", False, (), tuple(notes)


def spoken_number(words: list[str]) -> tuple[int | None, int]:
    """Parse the longest spoken number at the start of ``words``; returns (value, tokens used)."""
    total = current = used = 0
    seen = False
    i = 0
    words = [
        w[1:] if w.startswith("و") and len(w) > 2 and (w[1:] in _WORDS or w[1:] in _THOUSAND) else w
        for w in words
    ]  # attached conjunction: وخمسين -> خمسين
    while i < len(words):
        w = words[i]
        if w in {"و", "and"} and seen and i + 1 < len(words):
            if words[i + 1] in _WORDS or words[i + 1] in _EN or words[i + 1] in _THOUSAND:
                i += 1
                continue
            break
        if w in _DUAL_THOUSAND:
            total += _DUAL_THOUSAND[w] + current * 0
            seen, used = True, i + 1
        elif w in _THOUSAND or w == "thousand":
            total += max(current, 1) * 1000
            current, seen, used = 0, True, i + 1
        elif w in _WORDS or w in _EN:
            v = _WORDS.get(w, _EN.get(w, 0))
            current += v
            seen, used = True, i + 1
        elif w == "hundred" and seen:
            current = (current or 1) * 100
            used = i + 1
        else:
            break
        i += 1
    return (total + current if seen else None), used


def _unit_info(
    words: list[str], ctx_currency: str | None
) -> tuple[str, str | None, str | None, str | None, int, tuple[str, ...]]:
    """(unit_text, unit, currency, per, tokens used, notes)"""
    notes: list[str] = []
    unit_text, unit, currency, per, used = "", None, None, None, 0
    i = 0
    words = [w for w in words]
    if i < len(words) and words[i] in CURRENCY_WORDS:
        iso, needs = CURRENCY_WORDS[words[i]]
        unit_text = words[i]
        if needs and ctx_currency != iso:
            notes.append(f"currency word {words[i]!r} needs factory context ({iso}) - not assumed")
        else:
            currency = iso
        i += 1
        used = i
    elif i < len(words) and words[i] in UNIT_WORDS:
        unit_text = words[i]
        mapped = UNIT_WORDS[words[i]]
        if mapped == "AMBIGUOUS":
            notes.append(f"unit {words[i]!r} is ambiguous (short/long/metric ton)")
        else:
            try:
                parse_unit(mapped)
                unit = mapped
                if mapped == "piece" and words[i] in {"units", "unit", "وحده", "وحدة", "وحدات"}:
                    notes.append("count word mapped to piece")
            except UnitError:  # pragma: no cover - table is static
                notes.append("unit not recognized by the units engine")
        i += 1
        used = i
    if i < len(words) and words[i] in {"من", "of"} and unit_text:
        for k in range(i + 1, min(i + 5, len(words))):  # "2 hours OF <resource> per piece"
            if words[k] in _PER_CUE:
                i = k
                break
    if i < len(words) and (words[i] in _PER_CUE or words[i] == "/"):
        j = i + 1
        if j < len(words) and words[j] in PER_WORDS:
            per, used = PER_WORDS[words[j]], j + 1
        elif j < len(words) and UNIT_WORDS.get(words[j], "AMBIGUOUS") != "AMBIGUOUS":
            per, used = UNIT_WORDS[words[j]], j + 1
            if currency and unit is None:
                unit = f"{currency}/{per}"
    return unit_text, unit, currency, per, used, tuple(notes)


def _token_spans(norm: str) -> list[tuple[str, int, int]]:
    pat = r"[^\s.!?;,:()]+(?:[.,][^\s.!?;,:()]+)*"
    return [(m.group(), m.start(), m.end()) for m in re.finditer(pat, norm)]


def find_quantities(
    norm: str,
    to_orig: tuple[int, ...],
    text: str,
    norm_offset: int,
    default_currency: str | None = None,
) -> list[QuantityMention]:
    """Quantity mentions in one normalized segment; offsets are mapped to the ORIGINAL source.

    ``norm_offset`` is the segment start inside the document-level normalized string, and
    ``to_orig`` is the document-level map; ``text`` is the full original source.
    """
    toks = _token_spans(norm)
    out: list[QuantityMention] = []
    i = 0

    def orig(a: int, b: int) -> tuple[int, int]:
        return to_orig[norm_offset + a], to_orig[norm_offset + b - 1] + 1

    while i < len(toks):
        tok, s, e = toks[i]
        t = tok.strip(",;:()")
        value: str | None = None
        amb = False
        alts: tuple[str, ...] = ()
        notes: tuple[str, ...] = ()
        used = 1
        if _NUM_RE.fullmatch(t.rstrip("%")) and not t.startswith("/"):
            try:
                value, amb, alts, notes = _digits_value(t.rstrip("%"))
            except ValueError:
                value, amb = None, True
            end_char = e
            percent = t.endswith("%")
        else:
            words = [w.strip(",;:()") for w, _, _ in toks[i:]]
            num, n_used = spoken_number(words)
            if num is None or n_used == 0:
                i += 1
                continue
            value, used = str(num), n_used
            end_char = toks[i + used - 1][2]
            percent = False
        if value is None:
            i += 1
            continue
        nxt = [w.strip(",;:()") for w, _, _ in toks[i + used :]]
        pct_words = nxt[:2]
        if not percent and (
            nxt[:1] in (["%"], ["percent"], ["بالمية"], ["بالميه"], ["بالمائة"])
            or pct_words in (["في", "المية"], ["في", "الميه"], ["per", "cent"])
        ):
            percent = True
            consumed = (
                1 if nxt[:1] in (["%"], ["percent"], ["بالمية"], ["بالميه"], ["بالمائة"]) else 2
            )
            used += consumed
            end_char = toks[i + used - 1][2]
            nxt = nxt[consumed:]
        unit_text = ""
        unit = currency = per = None
        unotes: tuple[str, ...] = ()
        if not percent:
            unit_text, unit, currency, per, uused, unotes = _unit_info(nxt, default_currency)
            if uused:
                used += uused
                end_char = toks[i + used - 1][2]
        try:
            is_zero = Decimal(value) == 0 and (t in _ZERO or value == "0")
        except InvalidOperation:
            is_zero = False
        o_start, o_end = orig(s, end_char)
        out.append(
            QuantityMention(
                kind="PERCENT" if percent else "EXACT",
                low=value,
                explicit_zero=is_zero,
                unit_text=unit_text,
                unit=unit,
                currency=currency,
                per=per,
                text=text[o_start:o_end],
                orig_start=o_start,
                orig_end=o_end,
                ambiguous=amb,
                alternatives=alts,
                notes=(*notes, *unotes),
            )
        )
        i += used
    return _merge_ranges(out, norm, norm_offset, to_orig, text)


def _merge_ranges(
    ms: list[QuantityMention], norm: str, norm_offset: int, to_orig: tuple[int, ...], text: str
) -> list[QuantityMention]:
    if len(ms) < 2:
        return ms
    out: list[QuantityMention] = []
    skip = False
    for a, b in zip(ms, [*ms[1:], None], strict=False):
        if skip:
            skip = False
            continue
        if b is not None and a.kind == b.kind == "EXACT" and not a.unit_text:
            between = text[a.orig_end : b.orig_start]
            if _RANGE_CONN.match(between.lower()) and between.strip():
                try:
                    lo, hi = Decimal(a.low), Decimal(b.low)
                except InvalidOperation:  # pragma: no cover
                    lo = hi = Decimal(0)
                if lo <= hi:
                    out.append(
                        b.model_copy(
                            update={
                                "kind": "RANGE",
                                "low": a.low,
                                "high": b.low,
                                "text": text[a.orig_start : b.orig_end],
                                "orig_start": a.orig_start,
                                "ambiguous": a.ambiguous or b.ambiguous,
                            }
                        )
                    )
                    skip = True
                    continue
        out.append(a)
    return out

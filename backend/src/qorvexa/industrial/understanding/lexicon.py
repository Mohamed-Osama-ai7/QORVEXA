"""Generic industrial concept hints and semantic cue lexicons.

Hints are curated EXACT-phrase matches, never fuzzy/similarity matches, and they are
GENERIC_UNCONFIRMED: only a factory-confirmed terminology entry is a trusted meaning. A hint
produces a candidate concept plus a terminology gap, never a silently trusted mapping.
Expressions are written in source form and normalized with the same function as the text.
This is a seed vocabulary, not a claim of dialect coverage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache, lru_cache

from qorvexa.industrial.understanding.source import normalize

_AR_PREFIX = r"(?:وال|بال|لل|ال|و|ب|ل|ف|ك)?"


def n(s: str) -> str:
    return normalize(s).text


# concept ref -> expressions (Arabic, Egyptian, English, Arabizi)
_HINTS: dict[str, tuple[str, ...]] = {
    "concept:machine_downtime": (
        "واقفة",
        "واقفه",
        "واقف",
        "وقفت",
        "متوقفة",
        "متوقفه",
        "متوقف",
        "توقفت",
        "عطلانة",
        "عطلانه",
        "معطلة",
        "معطله",
        "stopped",
        "is down",
        "downtime",
        "breakdown",
        "broke down",
        "توقف الماكينة",
        "توقف",
    ),
    "concept:intermittent_stops": (
        "بتقف كل شوية",
        "بتقف كل شويه",
        "bet2af",
        "bet2ef",
        "keeps stopping",
        "stops frequently",
        "بتقف كتير",
    ),
    "concept:slow_operation": (
        "بطيئة",
        "بطيئه",
        "بطيء",
        "slow",
        "slowly",
        "running slow",
        "تعمل ببطء",
    ),
    "concept:production_output": (
        "بتنتج",
        "بينتج",
        "تنتج",
        "ينتج",
        "produces",
        "producing",
        "produce",
        "يصنع",
        "بتطلع",
    ),
    "concept:throughput_actual": ("throughput", "معدل الانتاج", "معدل التشغيل"),
    "concept:capacity": (
        "capacity",
        "طاقتها",
        "طاقته",
        "سعتها",
        "سعته",
        "الطاقة الانتاجية",
        "الطاقة الإنتاجية",
        "الطاقه الانتاجيه",
        "الطاقة",
        "السعة",
        "السعه",
        "سعة",
        "can produce",
        "max output",
        "اقصى انتاج",
    ),
    "concept:availability": (
        "available",
        "availability",
        "متاح",
        "متاحة",
        "متاحه",
        "المتاح",
        "المتاحة",
    ),
    "concept:scrap_waste": ("هالك", "الهالك", "scrap", "waste", "wastage", "تالف"),
    "concept:defect": (
        "عيوب",
        "defects",
        "defect",
        "defective",
        "rejects",
        "rejected",
        "مرفوض",
        "رفض جودة",
    ),
    "concept:material_shortage": (
        "نقص خامة",
        "نقص في الخامة",
        "الخامة خلصت",
        "خلصت الخامة",
        "مفيش خامة",
        "out of material",
        "material shortage",
        "shortage of material",
        "نقص",
    ),
    "concept:above_normal": (
        "اكتر من الطبيعي",
        "أكتر من الطبيعي",
        "اكتر من المعتاد",
        "more than normal",
        "than usual",
        "more than usual",
        "than normal",
    ),
    "concept:consumption": (
        "بتسحب",
        "تسحب",
        "بيسحب",
        "يسحب",
        "بتستهلك",
        "تستهلك",
        "يستهلك",
        "بيستهلك",
        "consumes",
        "uses",
        "requires",
        "needs",
        "consume",
        "بتحتاج",
        "يحتاج",
    ),
    "concept:bottleneck": (
        "bottleneck",
        "عنق الزجاجة",
        "عنق الزجاجه",
        "نقطة الاختناق",
        "نقطه الاختناق",
    ),
    "concept:jam": (
        "jam",
        "jams",
        "jamming",
        "jammed",
        "بتعلق",
        "بيعلق",
        "علقت",
        "انحشار",
        "انحشرت",
    ),
    "concept:cannot_keep_up": (
        "مش لاحق",
        "مش لاحقة",
        "مش لاحقه",
        "مش بتلحق",
        "مش لحقت",
        "cannot keep up",
        "can't keep up",
        "not keeping up",
        "behind schedule",
        "متاخر",
    ),
    "concept:demand_order": (
        "order",
        "orders",
        "اوردر",
        "أوردر",
        "الاوردر",
        "اوردرات",
        "طلبية",
        "طلبيه",
        "demand",
        "الطلبية",
        "الطلبيه",
    ),
    "concept:margin": ("profit", "margin", "contribution", "ربح", "الربح", "هامش", "هامش الربح"),
    "concept:cost": ("cost", "costs", "تكلفة", "تكلفه", "التكلفة", "التكلفه"),
    "concept:price": ("price", "سعر", "السعر", "selling price", "سعر البيع"),
}
# production-planning features this slice cannot model (must stay visible and block)
_FEATURES: dict[str, tuple[str, ...]] = {
    "setup_time": (
        "setup",
        "set-up",
        "changeover",
        "change over",
        "تغيير القالب",
        "تغيير الصنف",
        "تغيير المنتج",
        "سيت اب",
        "ضبط القالب",
    ),
    "sequence": ("sequence", "sequencing", "precedence", "ترتيب التشغيل", "ترتيب الاوردرات"),
    "batch_size": ("batch", "batches", "lot size", "دفعة", "دفعات", "حجم الدفعة", "حجم الدفعه"),
    "integer": ("integer", "whole units only", "عدد صحيح"),
    "schedule": ("schedule", "scheduling", "جدول", "جدولة", "جدول الشيفتات", "shift schedule"),
    "maintenance": (
        "scheduled maintenance",
        "planned maintenance",
        "صيانة دورية",
        "صيانة مجدولة",
        "صيانه دوريه",
    ),
}

TARGET_CUES = tuple(
    n(x) for x in ("target", "goal", "planned", "التارجت", "تارجت", "المستهدف", "هدفنا", "الهدف")
)
HISTORICAL_CUES = tuple(
    n(x)
    for x in (
        "used to",
        "previously",
        "last month",
        "last week",
        "last year",
        "historically",
        "was producing",
        "were producing",
        "كانت",
        "كان",
        "كنا",
        "زمان",
        "الشهر اللي فات",
        "الشهر الماضي",
        "الاسبوع اللي فات",
        "سابقا",
        "قبل كده",
    )
)
HYPOTHETICAL_CUES = tuple(
    n(x)
    for x in (
        "what if",
        "suppose",
        "imagine",
        "ماذا لو",
        "لو افترضنا",
        "لو فرضنا",
        "لو زودنا",
        "لو قللنا",
        "افترض",
    )
)
CONDITIONAL_CUES = tuple(
    n(x) for x in ("if", "unless", "provided", "when", "لو", "اذا", "إذا", "في حالة", "طالما")
)
ASSUMPTION_CUES = tuple(n(x) for x in ("assume", "assuming", "نفترض", "بافتراض", "على افتراض"))
RECOMMENDATION_CUES = tuple(
    n(x) for x in ("recommend", "suggest", "we should", "better to", "اقترح", "انصح", "ينصح")
)
ACTUAL_CUES = tuple(
    n(x)
    for x in ("measured", "actual", "actually", "today", "الفعلي", "فعليا", "فعلي", "النهارده",
              "اتقاس", "حققنا", "سجلنا")
)  # fmt: skip
MIN_CUES = tuple(
    n(x) for x in ("at least", "minimum", "no less than", "على الاقل", "الحد الادنى", "لا يقل عن")
)
MAX_CUES = tuple(
    n(x)
    for x in ("at most", "maximum", "no more than", "up to", "على الاكثر", "الحد الاقصى",
              "لا يزيد عن", "لا يزيد")
)  # fmt: skip
NEGATION_CUES = tuple(
    n(x)
    for x in ("not", "n't", "never", "without", "no", "مش", "مفيش", "ليس", "لم", "لن", "بدون", "لا")
)


@cache
def _rx(expr: str) -> re.Pattern[str]:
    e = re.escape(n(expr))
    if expr.isascii() and re.match(r"[a-z0-9]", expr.lower()):
        return re.compile(rf"(?<![a-z0-9]){e}(?![a-z0-9])")
    return re.compile(rf"(?<![؀-ۿ]){_AR_PREFIX}{e}(?![؀-ۿ])")


@dataclass(frozen=True)
class HintMatch:
    expression: str
    ref: str  # concept ref or "feature:<key>"
    start: int  # normalized offsets inside the segment
    end: int


@lru_cache(maxsize=1)
def _all_hints() -> tuple[tuple[str, str], ...]:
    pairs = [(e, c) for c, es in _HINTS.items() for e in es]
    pairs += [(e, f"feature:{k}") for k, es in _FEATURES.items() for e in es]
    return tuple(sorted(pairs, key=lambda p: -len(n(p[0]))))


def find_hints(norm: str, blocked: tuple[tuple[int, int], ...] = ()) -> list[HintMatch]:
    """Longest-first, non-overlapping exact-phrase hint matches (never fuzzy)."""
    taken: list[tuple[int, int]] = list(blocked)
    out: list[HintMatch] = []
    for expr, ref in _all_hints():
        for m in _rx(expr).finditer(norm):
            core_start = m.start() + (len(m.group()) - len(n(expr)))
            span = (core_start, m.end())
            if any(span[0] < b and a < span[1] for a, b in taken):
                continue
            taken.append(span)
            out.append(HintMatch(expr, ref, span[0], span[1]))
    return sorted(out, key=lambda h: h.start)


def find_cue(norm: str, cues: tuple[str, ...]) -> tuple[str, int] | None:
    best: tuple[str, int] | None = None
    for c in cues:
        if c.isascii():
            pat = rf"(?<![a-z0-9']){re.escape(c)}(?![a-z0-9])" if c != "n't" else r"n't\b"
        else:
            pat = rf"(?<![؀-ۿ]){re.escape(c)}(?![؀-ۿ])"
        m = re.search(pat, norm)
        if m and (best is None or m.start() < best[1]):
            best = (c, m.start())
    return best

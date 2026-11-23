# ruff: noqa: E501
"""Gap analysis and multi-turn clarification planning (no invented business weights).

Gaps are derived from evidence only. Questions are produced in the language of the speaker
(English, formal Arabic, or natural Egyptian Arabic), state what is missing, why it matters, the
affected fields and whether it blocks formulation. Priority is a dependency order
(terms/entities -> conflicts and unsupported features -> units -> values), not a business weight.
Resolved gaps disappear; still-open gaps are re-asked (counted), never duplicated.
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict

from qorvexa.industrial.understanding.models import (
    Candidate,
    Claim,
    Contradiction,
    EntityMention,
    Gap,
    Grounding,
    GroundingResult,
    LanguageInfo,
    Predicate,
    Question,
    UnsupportedFeature,
)
from qorvexa.industrial.understanding.terminology import TermResolution

_MAPPABLE = {Predicate.CAPACITY, Predicate.DEMAND, Predicate.CONSUMPTION_RATE,
             Predicate.MARGIN_PER_UNIT, Predicate.COST_PER_UNIT, Predicate.PRICE}  # fmt: skip
_QUAL_NEEDS_NUMBER = {
    Predicate.SCRAP_LEVEL: "scrap/waste percentage or quantity",
    Predicate.DEFECT_LEVEL: "defect rate",
    Predicate.EXCESS_CONSUMPTION: "normal vs actual consumption per unit",
    Predicate.DOWNTIME: "downtime duration",
    Predicate.SLOW_OPERATION: "actual vs rated speed",
}


def _gid(code: str, affected: tuple[str, ...]) -> str:
    return f"{code}:{hashlib.sha256('|'.join(sorted(affected)).encode()).hexdigest()[:10]}"


def _gap(
    code: str, message: str, affected: tuple[str, ...], tier: int, why: str, blocking: bool = True
) -> Gap:
    return Gap(
        gap_id=_gid(code, affected),
        code=code,
        message=message,
        affected=affected,
        blocking=blocking,
        why=why,
        tier=tier,
    )


def analyze_gaps(
    claims: list[Claim],
    grounding: dict[str, GroundingResult],
    entities: list[EntityMention],
    resolutions: list[TermResolution],
    contradictions: list[Contradiction],
    unsupported: list[UnsupportedFeature],
    candidates: list[Candidate],
    default_currency: str | None,
    unknown_concepts: tuple[str, ...] = (),
) -> list[Gap]:
    gaps: dict[str, Gap] = {}

    def add(g: Gap) -> None:
        gaps.setdefault(g.gap_id, g)

    for tr in resolutions:
        if tr.status.value == "AMBIGUOUS":
            add(
                _gap(
                    "TERM_AMBIGUOUS",
                    f"'{tr.surface}' has several possible meanings {list(tr.candidates)}",
                    (tr.surface,),
                    1,
                    "the meaning decides which quantity or process is described",
                )
            )
        elif tr.status.value == "PROPOSED_ONLY":
            add(
                _gap(
                    "TERM_UNCONFIRMED",
                    f"'{tr.surface}' is only a proposed term",
                    (tr.surface,),
                    1,
                    "unconfirmed terminology is never trusted",
                    blocking=False,
                )
            )
    for concept_ref in unknown_concepts:
        add(
            _gap(
                "TERM_CONCEPT_UNKNOWN",
                f"concept {concept_ref} is not in the ontology",
                (concept_ref,),
                1,
                "terms must map to known concepts",
            )
        )
    mappable_subjects = {
        c.subject_entity for c in claims if c.predicate in _MAPPABLE and c.value is not None
    }
    for e in entities:
        if (
            e.mention_id in mappable_subjects | {c.related_entity for c in claims}
            and e.status != "CONFIRMED_TERM"
            and e.entity_type != "SHIFT"
        ):
            add(
                _gap(
                    "ENTITY_NOT_CONFIRMED",
                    f"'{e.text}' is not a confirmed {e.entity_type.lower()} for this factory",
                    (e.text,),
                    1,
                    "no model entity may be created from an unconfirmed expression",
                )
            )
    for k in contradictions:
        if k.code != "DUPLICATE":
            add(
                _gap(
                    "CONTRADICTION",
                    k.description,
                    k.claim_ids,
                    2,
                    "conflicting evidence is preserved until a human decides",
                )
            )
    for u in unsupported:
        add(
            _gap(
                "UNSUPPORTED_FEATURE",
                f"'{u.quote}' implies {u.feature}, which the LP slice cannot model",
                (f"feature:{u.feature}",),
                2,
                "an unmodeled constraint would silently change the answer",
            )
        )
    for c in claims:
        g = grounding[c.claim_id].outcome
        if c.value is not None and g is Grounding.AMBIGUOUS:
            add(
                _gap(
                    "AMBIGUOUS_NUMBER",
                    f"the number in '{c.quote}' can be read several ways {list(c.value.alternatives)}",
                    (c.claim_id,),
                    3,
                    "a wrong reading changes the model by orders of magnitude",
                )
            )
        if (
            c.value is not None
            and c.predicate in _MAPPABLE
            and c.value.unit is None
            and g is Grounding.GROUNDED
        ):
            add(
                _gap(
                    "MISSING_UNIT",
                    f"no known unit for '{c.value.text}'",
                    (c.claim_id,),
                    3,
                    "units are never assumed",
                )
            )
        if (
            c.qualitative
            and c.predicate in _QUAL_NEEDS_NUMBER
            and g in {Grounding.GROUNDED, Grounding.PARTIALLY_GROUNDED}
        ):
            add(
                _gap(
                    "QUALITATIVE_ONLY",
                    f"'{c.quote}' gives no figure: {_QUAL_NEEDS_NUMBER[c.predicate]}",
                    (c.claim_id,),
                    4,
                    "a qualitative remark cannot enter a model",
                    blocking=False,
                )
            )
    for cand in candidates:
        if cand.status == "NEEDS_CLARIFICATION":
            add(
                _gap(
                    "CANDIDATE_BLOCKED",
                    f"{cand.key}: {cand.note}",
                    (cand.key,),
                    4,
                    "the statement is not yet usable as model input",
                )
            )
    # required Production Planning information
    keys = {c.key for c in candidates if c.status == "SUPPORTED"}
    products = sorted({k.split(":")[1] for k in keys if k.startswith("product:")})
    resources = sorted({k.split(":")[1] for k in keys if k.startswith("resource:")})
    all_ents = {
        e.canonical_id: e for e in entities if e.status == "CONFIRMED_TERM" and e.canonical_id
    }
    products = sorted(
        set(products) | {i for i, e in all_ents.items() if e.entity_type == "PRODUCT"}
    )
    resources = sorted(
        set(resources)
        | {i for i, e in all_ents.items() if e.entity_type in {"MACHINE", "RESOURCE", "MATERIAL"}}
    )
    if not all_ents and not keys:
        add(
            _gap(
                "NO_CONFIRMED_ENTITIES",
                "no confirmed product or resource is mentioned",
                ("product", "resource"),
                1,
                "a model needs confirmed products and resources",
            )
        )
    elif not products:
        add(
            _gap(
                "MISSING_REQUIRED",
                "no product is described",
                ("product",),
                5,
                "the model needs at least one product",
            )
        )
    elif not resources:
        add(
            _gap(
                "MISSING_REQUIRED",
                "no limiting resource is described",
                ("resource",),
                5,
                "the model needs at least one resource",
            )
        )
    for p in products:
        if f"product:{p}:value_per_unit" not in keys:
            add(
                _gap(
                    "MISSING_REQUIRED",
                    f"margin or cost per unit of {p} is unknown",
                    (f"product:{p}:value_per_unit",),
                    5,
                    "the objective needs a value per product",
                )
            )
    for r in resources:
        if f"resource:{r}:capacity" not in keys:
            add(
                _gap(
                    "MISSING_REQUIRED",
                    f"capacity of {r} is unknown",
                    (f"resource:{r}:capacity",),
                    5,
                    "capacity bounds production",
                )
            )
        for p in products:
            if f"consumption:{r}:{p}" not in keys:
                add(
                    _gap(
                        "MISSING_REQUIRED",
                        f"how much of {r} one unit of {p} uses is unknown",
                        (f"consumption:{r}:{p}",),
                        5,
                        "consumption links products to resources; zero is only valid if stated",
                    )
                )
    if products and "problem:mode" not in keys:
        add(
            _gap(
                "MISSING_REQUIRED",
                "the objective (maximize margin or minimize cost) is unknown",
                ("problem:mode",),
                5,
                "the solver needs a direction",
            )
        )
    if products and "problem:currency" not in keys and not default_currency:
        add(
            _gap(
                "MISSING_REQUIRED",
                "the currency is unknown",
                ("problem:currency",),
                5,
                "money values need one explicit currency",
            )
        )
    return sorted(gaps.values(), key=lambda g: (g.tier, g.gap_id))


# ----------------------------------------------------------------------------- questions

_T: dict[str, dict[str, str]] = {
    "TERM_AMBIGUOUS": {
        "en": "In this factory, does '{x}' mean {x_alt}? Please pick one.",
        "ar": "ما المقصود بالمصطلح '{x}' في هذا المصنع؟ الاحتمالات: {x_alt}. يرجى التحديد.",
        "ar-EG": "'{x}' عندكم في المصنع يعني إيه بالظبط؟ الاحتمالات: {x_alt}. اختار واحد.",
    },
    "TERM_UNCONFIRMED": {
        "en": "Should '{x}' be added to the factory dictionary? A reviewer must confirm it.",
        "ar": "هل يُضاف المصطلح '{x}' إلى قاموس المصنع؟ يلزم اعتماده من مسؤول.",
        "ar-EG": "نضيف '{x}' لقاموس المصنع؟ لازم مسؤول يأكده الأول.",
    },
    "TERM_CONCEPT_UNKNOWN": {
        "en": "The concept '{x}' is unknown. Which known concept does it match?",
        "ar": "المفهوم '{x}' غير معروف. ما المفهوم المعروف المقابل له؟",
        "ar-EG": "المفهوم '{x}' مش معروف. بيقابل أنهي مفهوم؟",
    },
    "ENTITY_NOT_CONFIRMED": {
        "en": "Which exact machine/product is '{x}' (ID or confirmed name)?",
        "ar": "ما المعرّف أو الاسم المعتمد لـ '{x}'؟",
        "ar-EG": "'{x}' دي أنهي ماكينة أو منتج بالظبط؟ (الكود أو الاسم المعتمد)",
    },
    "CONTRADICTION": {
        "en": "Two statements disagree: {x}. Which one is correct for this period/shift?",
        "ar": "يوجد تعارض بين بيانين: {x}. أيهما الصحيح لهذه الفترة أو الوردية؟",
        "ar-EG": "فيه كلامين مش متفقين: {x}. أنهي واحد صح للفترة أو الشيفت دي؟",
    },
    "UNSUPPORTED_FEATURE": {
        "en": "{x}. This cannot be modeled yet. Is it essential, or can it be set aside?",
        "ar": "{x}. لا يمكن نمذجته حالياً. هل هو أساسي أم يمكن استبعاده؟",
        "ar-EG": "{x}. ده لسه مش بنقدر نحطه في الموديل. ده أساسي ولا نسيبه على جنب؟",
    },
    "MISSING_UNIT": {
        "en": "What unit is '{x}' in?",
        "ar": "ما وحدة القياس لـ '{x}'؟",
        "ar-EG": "'{x}' بأنهي وحدة؟ (قطعة، كجم، ساعة...)",
    },
    "AMBIGUOUS_NUMBER": {
        "en": "Which number do you mean: {x_alt}?",
        "ar": "أي رقم تقصد: {x_alt}؟",
        "ar-EG": "تقصد أنهي رقم: {x_alt}؟",
    },
    "QUALITATIVE_ONLY": {
        "en": "Can you give a number? {x}",
        "ar": "هل يمكن ذكر رقم؟ {x}",
        "ar-EG": "ممكن رقم تقريبي؟ {x}",
    },
    "CANDIDATE_BLOCKED": {
        "en": "I could not use this yet: {x}",
        "ar": "تعذّر استخدام هذه المعلومة بعد: {x}",
        "ar-EG": "لسه معرفتش أستخدم المعلومة دي: {x}",
    },
    "NO_CONFIRMED_ENTITIES": {
        "en": "Which products do you make and which machines/resources limit them?",
        "ar": "ما المنتجات التي تُصنَّع وما الماكينات أو الموارد المحدِّدة للإنتاج؟",
        "ar-EG": "بتصنعوا إيه، وإيه الماكينات أو الموارد اللي بتحدد الإنتاج؟",
    },
    "MISSING_REQUIRED": {
        "en": "Missing: {x}.",
        "ar": "معلومة ناقصة: {x}.",
        "ar-EG": "ناقص: {x}.",
    },
}


_WHY_AR = {
    "TERM_AMBIGUOUS": "لأن المعنى يحدد أي كمية أو عملية يقصدها الكلام",
    "TERM_UNCONFIRMED": "لأن المصطلح غير المعتمد لا يُعتمد عليه",
    "TERM_CONCEPT_UNKNOWN": "لأن المصطلحات لازم ترتبط بمفاهيم معروفة",
    "ENTITY_NOT_CONFIRMED": "لأن الموديل لا يقبل كيانًا من اسم غير مؤكد",
    "CONTRADICTION": "لأن التعارض يفضل ظاهرًا لحد ما حد يقرر",
    "UNSUPPORTED_FEATURE": "لأن قيد غير مدعوم هيغيّر النتيجة بدون ما نعرف",
    "MISSING_UNIT": "لأن الوحدات لا تُفترض أبدًا",
    "AMBIGUOUS_NUMBER": "لأن قراءة الرقم الغلط بتغيّر الموديل بشكل كبير",
    "QUALITATIVE_ONLY": "لأن الكلام الوصفي لا يدخل في الموديل",
    "CANDIDATE_BLOCKED": "لأن المعلومة لسه مش صالحة كمدخل للموديل",
    "NO_CONFIRMED_ENTITIES": "لأن الموديل يحتاج منتجات وموارد مؤكدة",
    "MISSING_REQUIRED": "لأن الموديل يحتاج المعلومة دي",
}


def choose_language(langs: list[LanguageInfo]) -> str:
    """Reply language from the speakers' own language: Egyptian when colloquial/Arabizi markers."""
    if not langs:
        return "en"
    arabic = [x for x in langs if x.primary in {"ARABIC", "MIXED"} or x.arabizi]
    if not arabic:
        return "en"
    colloquial = sum(
        1 for x in arabic if x.register_hint == "EGYPTIAN_COLLOQUIAL_MARKERS" or x.arabizi
    )
    formal = sum(1 for x in arabic if x.register_hint == "FORMAL_MARKERS")
    return "ar" if formal > colloquial else "ar-EG"


class ClarificationState(BaseModel):
    """Carried across turns: which gaps were asked how often, and which are resolved."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    asked: tuple[tuple[str, int], ...] = ()
    resolved: tuple[str, ...] = ()


def plan_questions(
    gaps: list[Gap], state: ClarificationState, language: str
) -> tuple[list[Question], ClarificationState]:
    open_ids = {g.gap_id for g in gaps}
    asked = dict(state.asked)
    resolved = sorted(set(state.resolved) | {g for g in asked if g not in open_ids})
    qs: list[Question] = []
    for g in sorted(gaps, key=lambda x: (x.tier, x.gap_id)):
        count = asked.get(g.gap_id, 0) + 1
        asked[g.gap_id] = count
        tmpl = _T.get(g.code, _T["MISSING_REQUIRED"])[language]
        alt = (
            " / ".join(g.message.split("[")[-1].rstrip("]").split(", "))
            if "[" in g.message
            else g.message
        )
        text = tmpl.format(
            x=g.message
            if g.code
            not in {
                "TERM_AMBIGUOUS",
                "TERM_UNCONFIRMED",
                "ENTITY_NOT_CONFIRMED",
                "MISSING_UNIT",
                "TERM_CONCEPT_UNKNOWN",
            }
            else (g.affected[0] if g.affected else g.message),
            x_alt=alt,
        )
        why = g.why if language == "en" else _WHY_AR.get(g.code, g.why)
        qs.append(
            Question(
                gap_id=g.gap_id,
                language=language,
                text=text,
                why=why,
                affected=g.affected,
                blocking=g.blocking,
                asked_count=count,
            )
        )
    new = ClarificationState(
        asked=tuple(sorted((k, v) for k, v in asked.items() if k in open_ids)),
        resolved=tuple(resolved),
    )
    return qs, new

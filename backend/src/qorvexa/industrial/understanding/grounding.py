"""Independent evidence verification. A claim is checked against the ORIGINAL source only;
the extractor (rules or LLM) never certifies itself.

Checks: quote exists at the stated offsets, numeric and unit grounding (re-parsed from the quote),
subject reference, negation, condition, target/actual/historical cues, predicate evidence.
Outcome priority: UNSUPPORTED > CONFLICTED > AMBIGUOUS > PARTIALLY_GROUNDED > GROUNDED.
"""

from __future__ import annotations

from qorvexa.industrial.understanding import lexicon as lx
from qorvexa.industrial.understanding.claims import _CONCEPT_PRED, _QUALITATIVE, detect_cues
from qorvexa.industrial.understanding.models import (
    Claim,
    Grounding,
    GroundingResult,
    Predicate,
    SemanticType,
    SourceDocument,
)
from qorvexa.industrial.understanding.numbers import find_quantities
from qorvexa.industrial.understanding.source import normalize

_COND_TYPES = {SemanticType.CONDITIONAL, SemanticType.HYPOTHETICAL}


def verify_claim(doc: SourceDocument, claim: Claim) -> GroundingResult:
    checks: list[tuple[str, bool]] = []
    reasons: list[str] = []
    outcome = Grounding.GROUNDED

    def worse(o: Grounding) -> None:
        nonlocal outcome
        order = [
            Grounding.GROUNDED, Grounding.PARTIALLY_GROUNDED, Grounding.AMBIGUOUS,
            Grounding.CONFLICTED, Grounding.UNSUPPORTED,
        ]  # fmt: skip
        if order.index(o) > order.index(outcome):
            outcome = o

    def check(name: str, ok: bool, o: Grounding, why: str) -> bool:
        checks.append((name, ok))
        if not ok:
            reasons.append(why)
            worse(o)
        return ok

    text = doc.text
    in_range = (
        0 <= claim.orig_start < claim.orig_end <= len(text) and claim.source_id == doc.source_id
    )
    check("offsets_valid", in_range, Grounding.UNSUPPORTED, "offsets outside the source")
    exists = (
        in_range
        and bool(claim.quote.strip())
        and text[claim.orig_start : claim.orig_end] == claim.quote
    )
    if not check(
        "quote_exists", exists, Grounding.UNSUPPORTED, "quote does not occur at the offsets"
    ):
        return GroundingResult(
            claim_id=claim.claim_id, outcome=Grounding.UNSUPPORTED, checks=tuple(checks),
            reasons=tuple(reasons),
        )  # fmt: skip
    nq = normalize(claim.quote)
    quantities = find_quantities(nq.text, nq.to_orig, claim.quote, 0, doc.default_currency)
    if claim.value is not None:
        v = claim.value
        match = [
            q for q in quantities
            if (q.kind, q.low, q.high) == (v.kind, v.low, v.high)
        ]  # fmt: skip
        has = check(
            "numeric_grounded", bool(match), Grounding.UNSUPPORTED,
            f"value {v.low}{'-' + v.high if v.high else ''} not found in the quote",
        )  # fmt: skip
        if has:
            q = match[0]
            check(
                "unit_grounded",
                (q.unit_text == v.unit_text and q.unit == v.unit),
                Grounding.UNSUPPORTED,
                "unit differs from the quote",
            )
            check(
                "number_unambiguous",
                not q.ambiguous,
                Grounding.AMBIGUOUS,
                "number format is ambiguous",
            )
            if q.explicit_zero != v.explicit_zero:
                check("zero_explicit", False, Grounding.UNSUPPORTED, "zero/absence mismatch")
    elif not claim.qualitative:
        check("value_present", False, Grounding.UNSUPPORTED, "quantitative claim without value")

    if claim.subject_text:
        in_quote = normalize(claim.subject_text).text in nq.text
        if claim.subject_from_context:
            checks.append(("subject_in_quote", False))
            reasons.append("subject comes from earlier context, not this quote")
            worse(Grounding.PARTIALLY_GROUNDED)
        else:
            check(
                "subject_in_quote",
                in_quote,
                Grounding.UNSUPPORTED,
                "subject not present in the quote",
            )
    else:
        checks.append(("subject_in_quote", False))
        reasons.append("no subject identified")
        worse(Grounding.PARTIALLY_GROUNDED)

    hints = lx.find_hints(nq.text)
    blocked = tuple(
        (h.start, h.end)
        for h in hints
        if h.ref == "concept:cannot_keep_up" or h.expression.startswith(("مفيش", "مش"))
    )
    cues = detect_cues(nq.text, blocked)
    check(
        "negation_consistent",
        cues.negated == claim.negated,
        Grounding.CONFLICTED,
        "negation differs from the quote",
    )
    claim_cond = claim.semantic_type in _COND_TYPES
    src_cond = cues.semantic_type in _COND_TYPES
    check(
        "condition_consistent",
        claim_cond == src_cond,
        Grounding.CONFLICTED,
        "conditional/hypothetical status differs from the quote",
    )
    for st in (SemanticType.TARGET, SemanticType.HISTORICAL, SemanticType.QUESTION):
        if (
            not claim_cond
            and not src_cond
            and (claim.semantic_type is st) != (cues.semantic_type is st)
        ):
            check(
                f"{st.value.lower()}_consistent",
                False,
                Grounding.CONFLICTED,
                f"{st.value} status differs from the quote",
            )
    if claim.predicate is not Predicate.UNKNOWN and claim.provenance.startswith("llm"):
        evidenced = {h.ref for h in hints}
        wanted = {r for r, p in {**_CONCEPT_PRED, **_QUALITATIVE}.items() if p is claim.predicate}
        check(
            "predicate_evidenced",
            bool(evidenced & wanted),
            Grounding.PARTIALLY_GROUNDED,
            "predicate is not evidenced by the wording",
        )
    return GroundingResult(
        claim_id=claim.claim_id, outcome=outcome, checks=tuple(checks), reasons=tuple(reasons)
    )

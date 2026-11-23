"""Run retrieval benchmark cases through an injected search function.

Relevance: a case lists chunk-id prefixes; the relevant set is every chunk in the evaluated
corpus (for the case's asset) whose id starts with one of them. Recall@K is therefore true
chunk recall (its ceiling is K / |relevant| when many chunks match). If no corpus chunk matches,
the case is ``no_relevant_in_corpus`` and its metrics are ``unavailable``, never 0.

Hit Rate@K and Recall@K reuse ``knowledge.eval``; reciprocal rank is computed here because no
reusable function exists for it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

from qorvexa.evaluation.schemas import CaseResult, CaseStatus, MetricOutcome, RetrievalCase
from qorvexa.knowledge.eval import hit_rate_at_k, recall_at_k
from qorvexa.knowledge.rag import HybridRagIndex, SearchMode, extract_content_tokens

RETRIEVAL_METRICS: tuple[str, ...] = (
    "hit_rate_at_5",
    "recall_at_5",
    "recall_at_10",
    "reciprocal_rank_at_10",
    "out_of_scope_abstention",
)
TOP_K = 10

SearchFn = Callable[[str, str, int], Sequence[object]]
"""``(query, asset_id, top_k) -> ranked chunk ids`` for that asset (may contain junk)."""
CorpusFn = Callable[[str], Collection[str]]
"""``asset_id -> chunk ids`` in the evaluated corpus."""


TokenizeFn = Callable[[str], Sequence[str]]
"""The system's query tokenizer; used to detect queries it cannot represent at all."""
ChunkHashFn = Callable[[str, str], str | None]
"""``(asset_id, chunk_id) -> sha256 of the chunk text`` (``None`` if the chunk is absent)."""


@dataclass(frozen=True)
class RetrievalSystem:
    name: str
    search: SearchFn
    corpus: CorpusFn
    tokenize: TokenizeFn | None = None
    chunk_sha256: ChunkHashFn | None = None


def system_from_index(
    index: HybridRagIndex, *, mode: SearchMode = "hybrid_rrf", tenant_id: str = "evaluation"
) -> RetrievalSystem:
    """Wrap an in-process ``HybridRagIndex`` (no API, no auth path) for one search mode."""

    def search(query: str, asset_id: str, top_k: int) -> list[object]:
        resp = index.search(
            query, tenant_id=tenant_id, allowed_asset_ids={asset_id}, top_k=top_k, mode=mode
        )
        return [h.chunk_id for h in resp.items if h.asset_id == asset_id]

    def corpus(asset_id: str) -> set[str]:
        return {
            c.chunk_id
            for c in index.accessible_chunks(tenant_id=tenant_id, allowed_asset_ids={asset_id})
        }

    def chunk_sha256(asset_id: str, chunk_id: str) -> str | None:
        for c in index.accessible_chunks(tenant_id=tenant_id, allowed_asset_ids={asset_id}):
            if c.chunk_id == chunk_id:
                return hashlib.sha256(c.text.encode("utf-8")).hexdigest()
        return None

    return RetrievalSystem(
        name=f"hybrid_rag_index:{mode}",
        search=search,
        corpus=corpus,
        tokenize=extract_content_tokens,
        chunk_sha256=chunk_sha256,
    )


def corpus_fingerprint(index: HybridRagIndex) -> str:
    """SHA-256 over every (chunk id, chunk-text hash) pair: identifies the evaluated corpus."""
    digest = hashlib.sha256()
    for chunk in sorted(index.chunks, key=lambda c: (c.asset_id, c.chunk_id)):
        text_hash = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
        line = "|".join((chunk.asset_id, chunk.chunk_id, text_hash))
        digest.update(line.encode("utf-8") + b"\n")
    return digest.hexdigest()


def reciprocal_rank_at_k(
    ranked_ids: Sequence[str], relevant_ids: Collection[str], k: int
) -> float | None:
    """1/rank of the first relevant id within the first ``k`` distinct ids; ``None`` if no
    relevant ids exist (undefined, like Recall@K)."""
    relevant = set(relevant_ids)
    if not relevant:
        return None
    seen: list[str] = []
    for item in ranked_ids:
        if item in seen:
            continue
        seen.append(item)
        if len(seen) > k:
            break
        if item in relevant:
            return 1.0 / len(seen)
    return 0.0


def _metric(value: float | None) -> MetricOutcome:
    if value is None:
        return MetricOutcome(value=None, state="not_applicable")
    return MetricOutcome(value=round(value, 6), state="scored")


def run_retrieval_case(case: RetrievalCase, system: RetrievalSystem) -> CaseResult:
    result = _run_retrieval_case(case, system)
    return result.model_copy(
        update={
            "split": case.effective_split,
            "cluster_id": case.cluster_id,
            "details": {
                **result.details,
                "language": case.language,
                "content_origin": case.content_origin,
                "asset_id": case.asset_id,
            },
        }
    )


def _unavailable_result(
    case: RetrievalCase, status: CaseStatus, reason: str, details: dict[str, object]
) -> CaseResult:
    return CaseResult(
        case_id=case.case_id,
        task="retrieval",
        provenance=case.provenance,
        status=status,
        errors=[reason],
        details=details,
        metrics={
            m: MetricOutcome(value=None, state="unavailable", reason=reason)
            for m in RETRIEVAL_METRICS
        },
    )


def _run_retrieval_case(case: RetrievalCase, system: RetrievalSystem) -> CaseResult:
    if system.tokenize is not None and not list(system.tokenize(case.query)):
        # Without this gate an unrepresentable (e.g. Arabic) query would "retrieve nothing",
        # which would score as 0 recall or, for out-of-scope probes, as a false abstention success.
        return _unavailable_result(
            case,
            "unsupported_language",
            f"the system's tokenizer yields no tokens for this {case.language!r} query",
            {"system": system.name, "language": case.language},
        )
    raw = list(system.search(case.query, case.asset_id, TOP_K))
    ranked = [h.strip() for h in raw if isinstance(h, str) and h.strip()]
    discarded = len(raw) - len(ranked)
    na = MetricOutcome(value=None, state="not_applicable")
    details: dict[str, object] = {
        "system": system.name,
        "returned": len(ranked),
        "top_ids": ranked[:5],
    }

    if case.provenance == "out_of_scope_probe":
        metrics = {m: na for m in RETRIEVAL_METRICS[:-1]}
        metrics["out_of_scope_abstention"] = MetricOutcome(
            value=1.0 if not ranked else 0.0,
            state="scored",
            reason="index-level only; copilot scope gating is not evaluated here",
        )
        return CaseResult(
            case_id=case.case_id,
            task="retrieval",
            provenance=case.provenance,
            status="scored",
            discarded_keys=discarded,
            details=details,
            metrics=metrics,
        )

    corpus = set(system.corpus(case.asset_id))
    relevant = {
        cid for cid in corpus if any(cid.startswith(p) for p in case.relevant_chunk_prefixes)
    }
    stale: list[str] = []
    for cid in case.source_chunk_ids:
        expected = case.source_chunk_sha256.get(cid)
        if cid not in corpus:
            stale.append(f"{cid}: not in corpus")
        elif (
            expected is not None
            and system.chunk_sha256 is not None
            and system.chunk_sha256(case.asset_id, cid) != expected
        ):
            stale.append(f"{cid}: chunk text changed since the label was written")
        relevant.add(cid)
    if stale:
        return _unavailable_result(
            case, "stale_label", "stale label: " + "; ".join(sorted(stale)), details
        )
    details["relevant_in_corpus"] = len(relevant)
    if not relevant:
        reason = "no corpus chunk matches the relevant prefixes (source not indexed?)"
        return CaseResult(
            case_id=case.case_id,
            task="retrieval",
            provenance=case.provenance,
            status="no_relevant_in_corpus",
            discarded_keys=discarded,
            details=details,
            metrics={
                m: MetricOutcome(value=None, state="unavailable", reason=reason)
                for m in RETRIEVAL_METRICS
            },
        )
    metrics = {
        "hit_rate_at_5": _metric(hit_rate_at_k(ranked, relevant, 5)),
        "recall_at_5": _metric(recall_at_k(ranked, relevant, 5)),
        "recall_at_10": _metric(recall_at_k(ranked, relevant, 10)),
        "reciprocal_rank_at_10": _metric(reciprocal_rank_at_k(ranked, relevant, 10)),
        "out_of_scope_abstention": na,
    }
    return CaseResult(
        case_id=case.case_id,
        task="retrieval",
        provenance=case.provenance,
        status="scored",
        discarded_keys=discarded,
        details=details,
        metrics=metrics,
    )


def run_retrieval_cases(
    cases: Sequence[RetrievalCase], system: RetrievalSystem
) -> list[CaseResult]:
    return [run_retrieval_case(c, system) for c in cases]


@dataclass(frozen=True)
class ArabicDiagnosticResult:
    """Diagnostic audit of Arabic query text and tokenizer readiness.

    Industrial Arabic claims remain disabled until native technical references (D-1/D-2)
    and a verified bilingual tokenizer are available.
    """

    text: str
    has_arabic: bool
    arabic_char_count: int
    tokens: list[str]
    is_supported: bool
    has_diacritics: bool
    has_tatweel: bool
    has_alif_variants: bool
    has_taa_marbuta: bool
    has_alif_maqsura: bool
    has_presentation_forms: bool
    issues: list[str]


_ARABIC_RANGES = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)
_TASHKEEL_CODEPOINTS = frozenset(range(0x064B, 0x0660)) | {0x0670}
_ALIF_VARIANTS = frozenset({"\u0622", "\u0623", "\u0625", "\u0671"})  # آ, أ, إ, ٱ
_PRESENTATION_RANGES = ((0xFB50, 0xFDFF), (0xFE70, 0xFEFF))


def _is_arabic_codepoint(cp: int) -> bool:
    return any(start <= cp <= end for start, end in _ARABIC_RANGES)


def _is_presentation_codepoint(cp: int) -> bool:
    return any(start <= cp <= end for start, end in _PRESENTATION_RANGES)


def diagnose_arabic_tokenization(
    text: str, tokenizer_fn: Callable[[str], Sequence[str]] | None = None
) -> ArabicDiagnosticResult:
    """Diagnose Arabic orthography, normalization requirements, and tokenizer compatibility.

    Checks:
    * Presence and count of Arabic script codepoints.
    * Diacritics (tashkeel/harakat) requiring normalization or striping.
    * Tatweel (kashida lengthening '\u0640').
    * Orthographic variants (hamzated alif variants: أ, إ, آ, ٱ).
    * Taa marbuta ('\u0629') and Alif maqsura ('\u0649').
    * Presentation form ligatures requiring canonical decomposition.
    * Tokenizer output: whether the given tokenizer produces valid tokens for the Arabic text.
    """
    tok = tokenizer_fn if tokenizer_fn is not None else extract_content_tokens
    produced_tokens = list(tok(text))

    arabic_chars = [ch for ch in text if _is_arabic_codepoint(ord(ch))]
    arabic_count = len(arabic_chars)
    has_ar = arabic_count > 0

    has_diacritics = any(ord(ch) in _TASHKEEL_CODEPOINTS for ch in text)
    has_tatweel = "\u0640" in text
    has_alif_variants = any(ch in _ALIF_VARIANTS for ch in text)
    has_taa_marbuta = "\u0629" in text
    has_alif_maqsura = "\u0649" in text
    has_pres_forms = any(_is_presentation_codepoint(ord(ch)) for ch in text)

    # A tokenizer supports Arabic text only if it produces tokens when Arabic text is given.
    # The default ASCII regex `[a-z0-9_]+` returns empty list on pure Arabic.
    tokens_contain_arabic = any(
        any(_is_arabic_codepoint(ord(c)) for c in token) for token in produced_tokens
    )
    is_supported = (not has_ar) or (tokens_contain_arabic and len(produced_tokens) > 0)

    issues: list[str] = []
    if has_ar and not is_supported:
        issues.append(
            "Tokenizer yielded 0 Arabic tokens: current tokenizer uses ASCII [a-z0-9_]+ "
            "and drops Arabic script."
        )
    if has_diacritics:
        issues.append(
            "Text contains Arabic tashkeel/diacritics; non-stripped diacritics "
            "cause exact-match failures."
        )
    if has_tatweel:
        issues.append("Text contains kashida/tatweel lengthening characters (U+0640).")
    if has_alif_variants:
        issues.append("Text contains non-normalized Alif hamza forms (أ/إ/آ/ٱ).")
    if has_pres_forms:
        issues.append(
            "Text contains Arabic presentation forms (ligatures) requiring normalization."
        )

    return ArabicDiagnosticResult(
        text=text,
        has_arabic=has_ar,
        arabic_char_count=arabic_count,
        tokens=produced_tokens,
        is_supported=is_supported,
        has_diacritics=has_diacritics,
        has_tatweel=has_tatweel,
        has_alif_variants=has_alif_variants,
        has_taa_marbuta=has_taa_marbuta,
        has_alif_maqsura=has_alif_maqsura,
        has_presentation_forms=has_pres_forms,
        issues=issues,
    )

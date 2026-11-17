"""Run retrieval benchmark cases through an injected search function.

Relevance: a case lists chunk-id prefixes; the relevant set is every chunk in the evaluated
corpus (for the case's asset) whose id starts with one of them. Recall@K is therefore true
chunk recall (its ceiling is K / |relevant| when many chunks match). If no corpus chunk matches,
the case is ``no_relevant_in_corpus`` and its metrics are ``unavailable``, never 0.

Hit Rate@K and Recall@K reuse ``knowledge.eval``; reciprocal rank is computed here because no
reusable function exists for it.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

from factorymind.evaluation.schemas import CaseResult, MetricOutcome, RetrievalCase
from factorymind.knowledge.eval import hit_rate_at_k, recall_at_k
from factorymind.knowledge.rag import HybridRagIndex, SearchMode

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


@dataclass(frozen=True)
class RetrievalSystem:
    name: str
    search: SearchFn
    corpus: CorpusFn


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

    return RetrievalSystem(name=f"hybrid_rag_index:{mode}", search=search, corpus=corpus)


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

    relevant = {
        cid
        for cid in system.corpus(case.asset_id)
        if any(cid.startswith(p) for p in case.relevant_chunk_prefixes)
    }
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

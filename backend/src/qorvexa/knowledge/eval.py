"""Reproducible retrieval ablation evaluator comparing BM25, Dense SVD, and Hybrid RRF."""

from __future__ import annotations

import math
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from qorvexa.contracts.knowledge import RetrievalEvalResponse, RetrievalModeMetrics
from qorvexa.knowledge.rag import HybridRagIndex, SearchMode


@dataclass(frozen=True)
class RetrievalBenchmarkCase:
    query: str
    asset_id: str
    relevant_chunk_prefixes: tuple[str, ...]


DEFAULT_EVAL_CASES: Final[tuple[RetrievalBenchmarkCase, ...]] = (
    RetrievalBenchmarkCase(
        query="unloaded TP3 pressure decay slope air leakage duty_rule",
        asset_id="metropt3-apu-1",
        relevant_chunk_prefixes=("passport:mode-air_leakage", "m2-wp4-explainable-rca", "m2-wp0"),
    ),
    RetrievalBenchmarkCase(
        query="pca_spe non-leak statistical warning tier separation",
        asset_id="metropt3-apu-1",
        relevant_chunk_prefixes=("m2-wp2", "passport:model-pca_spe"),
    ),
    RetrievalBenchmarkCase(
        query="COMP DV_eletric intake outlet solenoid valve contradiction stale telemetry",
        asset_id="metropt3-apu-1",
        relevant_chunk_prefixes=(
            "passport:mode-telemetry_inconsistency",
            "passport:metropt3-sensors",
            "m2-wp2",
            "metropt3-pdf",
        ),
    ),
    RetrievalBenchmarkCase(
        query="downstream air demand flowmeter unobserved quantities competition",
        asset_id="metropt3-apu-1",
        relevant_chunk_prefixes=(
            "passport:metropt3-regimes",
            "passport:mode-abnormal_high_demand",
            "m2-wp4",
            "m3-wp0",
        ),
    ),
    RetrievalBenchmarkCase(
        query="MPG 8.2 bar LPS 7 bar cyclonic filter H1 motor phase current",
        asset_id="metropt3-apu-1",
        relevant_chunk_prefixes=("passport:metropt3-sensors", "metropt3-pdf"),
    ),
    RetrievalBenchmarkCase(
        query="causal vs correlation evidence origin human observation verification",
        asset_id="metropt3-apu-1",
        relevant_chunk_prefixes=("m2-wp3", "m2-wp4", "passport:"),
    ),
    RetrievalBenchmarkCase(
        query="cross_asset_transfer calibration boundary OOD Mahalanobis",
        asset_id="metropt3-apu-1",
        relevant_chunk_prefixes=("passport:model-cross_asset_transfer", "m3-wp0"),
    ),
    RetrievalBenchmarkCase(
        query="AI4I 2020 Brier ECE Mahalanobis abstention insufficient evidence",
        asset_id="ai4i-milling-bench",
        relevant_chunk_prefixes=("m3-wp1",),
    ),
    RetrievalBenchmarkCase(
        query="compressor duty limit",
        asset_id="metropt3-apu-1",
        relevant_chunk_prefixes=(
            "passport:mode-air_leakage",
            "passport:mode-abnormal_high_demand",
            "passport:mode-telemetry_inconsistency",
            "passport:model-duty_rule",
        ),
    ),
    RetrievalBenchmarkCase(
        query="tp3 pressure limits",
        asset_id="metropt3-apu-1",
        relevant_chunk_prefixes=(
            "passport:mode-air_leakage",
            "passport:metropt3-sensors",
            "m2-wp4",
            "m1-anomaly-detection",
        ),
    ),
    RetrievalBenchmarkCase(
        query="Explain pca_spe warning limits",
        asset_id="metropt3-apu-1",
        relevant_chunk_prefixes=("m2-wp2", "passport:model-pca_spe"),
    ),
)

DEFAULT_OOS_EVAL_QUERIES: Final[tuple[str, ...]] = (
    "What is the capital of France?",
    "Chocolate cake recipe",
    "What is the price of oil and air?",
    "Recipe for oil pressure",
    "What is 3 + 7?",
    "Give me 1 chocolate",
    "Description of banana",
    "Capital of compressor",
    "Write a Python binary search algorithm.",
    "What is the turbine blade temperature limit?",
)


def _top_k_unique(ranked_ids: Sequence[str], k: int) -> list[str]:
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    seen: dict[str, None] = {}
    for item in ranked_ids:
        if len(seen) == k:
            break
        seen.setdefault(item, None)
    return list(seen)


def hit_rate_at_k(ranked_ids: Sequence[str], relevant_ids: Collection[str], k: int) -> float | None:
    """1.0 if at least one relevant passage is in the top ``k``, else 0.0.

    Returns ``None`` when the item has no relevant passages: Hit Rate is undefined for it and
    the item belongs to the abstention metrics instead."""
    relevant = set(relevant_ids)
    top = _top_k_unique(ranked_ids, k)
    if not relevant:
        return None
    return 1.0 if relevant.intersection(top) else 0.0


def recall_at_k(ranked_ids: Sequence[str], relevant_ids: Collection[str], k: int) -> float | None:
    """|top-k retrieved ∩ relevant| / |relevant| for one item; duplicates count once.

    Returns ``None`` when the item has no relevant passages (0/0 is undefined, not 0 or 1)."""
    relevant = set(relevant_ids)
    top = _top_k_unique(ranked_ids, k)
    if not relevant:
        return None
    return len(relevant.intersection(top)) / len(relevant)


def macro_average(values: Iterable[float | None]) -> float | None:
    """Mean over the items where the metric is defined; ``None`` if it is defined for none."""
    defined = [v for v in values if v is not None]
    if not defined:
        return None
    return sum(defined) / len(defined)


def macro_recall_at_k(
    items: Iterable[tuple[Sequence[str], Collection[str]]], k: int
) -> float | None:
    """Macro-averaged Recall@K over ``(ranked_ids, relevant_ids)`` pairs.

    Items with no relevant passages are excluded from the average."""
    return macro_average(recall_at_k(ranked, relevant, k) for ranked, relevant in items)


def macro_hit_rate_at_k(
    items: Iterable[tuple[Sequence[str], Collection[str]]], k: int
) -> float | None:
    """Macro-averaged Hit Rate@K, with the same exclusion rule as ``macro_recall_at_k``."""
    return macro_average(hit_rate_at_k(ranked, relevant, k) for ranked, relevant in items)


def _is_relevant(chunk_id: str, prefixes: tuple[str, ...]) -> bool:
    return any(chunk_id.startswith(p) for p in prefixes)


def evaluate_retrieval_modes(
    index: HybridRagIndex,
    *,
    tenant_id: str,
    allowed_asset_ids: set[str],
) -> RetrievalEvalResponse:
    """Evaluate Hit Rate@5, MRR@10, and nDCG@5 strictly over caller-accessible benchmark cases.

    The response field ``recall_at_5`` holds Hit Rate@5 (share of cases with at least one
    relevant chunk in the top 5), not passage recall; the field name is kept for API
    compatibility. Use ``recall_at_k`` / ``macro_recall_at_k`` for true Recall@K."""
    scoped_chunks = index.accessible_chunks(
        tenant_id=tenant_id, allowed_asset_ids=allowed_asset_ids
    )
    cases = [c for c in DEFAULT_EVAL_CASES if c.asset_id in allowed_asset_ids]
    if not cases:
        return RetrievalEvalResponse(
            corpus_chunks=len(scoped_chunks),
            modes=[],
            notes=(
                "No ground-truth retrieval benchmark queries match the caller's authorized "
                "asset grants."
            ),
        )

    modes: tuple[SearchMode, ...] = ("bm25_only", "dense_svd_only", "hybrid_rrf")
    results: list[RetrievalModeMetrics] = []

    for mode in modes:
        recall_sum = 0.0
        mrr_sum = 0.0
        ndcg_sum = 0.0

        for case in cases:
            resp = index.search(
                case.query,
                tenant_id=tenant_id,
                allowed_asset_ids=allowed_asset_ids,
                top_k=10,
                mode=mode,
            )
            rel_flags = [
                1
                if h.asset_id == case.asset_id
                and _is_relevant(h.chunk_id, case.relevant_chunk_prefixes)
                else 0
                for h in resp.items
            ]
            top5 = rel_flags[:5]
            recall_sum += 1.0 if any(top5) else 0.0

            rr = 0.0
            for rank, flag in enumerate(rel_flags[:10], start=1):
                if flag == 1:
                    rr = 1.0 / rank
                    break
            mrr_sum += rr

            dcg = sum(flag / math.log2(rank + 1) for rank, flag in enumerate(top5, start=1))
            total_rel = sum(
                1
                for c in scoped_chunks
                if c.asset_id == case.asset_id
                and _is_relevant(c.chunk_id, case.relevant_chunk_prefixes)
            )
            ideal_k = min(5, max(total_rel, 1))
            idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_k + 1))
            ndcg_sum += dcg / idcg if idcg > 0 else 0.0

        n = len(cases)
        results.append(
            RetrievalModeMetrics(
                mode=mode,
                query_count=n,
                recall_at_5=round(recall_sum / n, 4),
                mrr_at_10=round(mrr_sum / n, 4),
                ndcg_at_5=round(ndcg_sum / n, 4),
            )
        )

    return RetrievalEvalResponse(
        corpus_chunks=len(scoped_chunks),
        modes=results,
        notes=(
            f"Controlled retrieval ablation across {len(cases)} benchmark queries "
            "evaluated strictly across chunks authorized for the caller's asset grants."
        ),
    )


def evaluate_abstention_rate(
    index: HybridRagIndex,
    *,
    tenant_id: str,
    allowed_asset_ids: set[str],
    queries: tuple[str, ...] = DEFAULT_OOS_EVAL_QUERIES,
) -> dict[SearchMode, float]:
    """Measure out-of-scope abstention rate (1.0 == zero spurious hits) across search modes."""
    modes: tuple[SearchMode, ...] = ("bm25_only", "dense_svd_only", "hybrid_rrf")
    rates: dict[SearchMode, float] = {}
    total = len(queries) or 1
    for mode in modes:
        abstained = sum(
            1
            for q in queries
            if not index.search(
                q,
                tenant_id=tenant_id,
                allowed_asset_ids=allowed_asset_ids,
                top_k=5,
                mode=mode,
            ).items
        )
        rates[mode] = round(abstained / total, 4)
    return rates

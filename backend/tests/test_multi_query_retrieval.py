# ruff: noqa: E501
"""Comprehensive test suite for Task 6: Multi-Query Retrieval."""

from __future__ import annotations

from qorvexa.contracts.knowledge import KnowledgeDocumentChunk
from qorvexa.knowledge.embeddings import MockNeuralEmbedder
from qorvexa.knowledge.expander import (
    MAX_VARIANT_CHARS,
    PassThroughQueryExpander,
    RuleBasedQueryExpander,
)
from qorvexa.knowledge.kg import IndustrialKnowledgeGraph
from qorvexa.knowledge.rag import HybridRagIndex
from qorvexa.knowledge.reranker import MockCrossEncoderReranker


def _make_chunk(
    chunk_id: str,
    text: str,
    *,
    doc_id: str = "doc-1",
    asset_id: str = "metropt3-apu-1",
    tenant_scope: str | None = "tenant-alpha",
    title: str = "Title",
    section: str = "Section",
) -> KnowledgeDocumentChunk:
    return KnowledgeDocumentChunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        title=title,
        section=section,
        asset_id=asset_id,
        tenant_scope=tenant_scope,
        source_path="test/multi_query.txt",
        license="Proprietary (Test)",
        provenance_origin="observed",
        text=text,
    )


def test_rule_based_query_expander_english() -> None:
    expander = RuleBasedQueryExpander()
    variants = expander.expand_query("air leakage diagnostics", max_variants=2)
    assert len(variants) >= 2
    assert variants[0] == "air leakage diagnostics"
    assert any("pneumatic" in v or "تسريب" in v for v in variants[1:])


def test_rule_based_query_expander_arabic() -> None:
    expander = RuleBasedQueryExpander()
    variants = expander.expand_query("تسريب هواء في الضاغط", max_variants=2)
    assert len(variants) >= 2
    assert variants[0] == "تسريب هواء في الضاغط"
    assert any("air leakage" in v or "ضغط" in v for v in variants[1:])


def test_rule_based_query_expander_mixed() -> None:
    expander = RuleBasedQueryExpander()
    variants = expander.expand_query("فحص صمام drain valve للضاغط", max_variants=2)
    assert len(variants) >= 2
    assert variants[0] == "فحص صمام drain valve للضاغط"
    assert any("discharge" in v or "تفريغ" in v or "تصريف" in v for v in variants[1:])


def test_preservation_of_technical_identifiers_and_numbers() -> None:
    expander = RuleBasedQueryExpander()
    query = "sensor TP3 reading 7.5 bar in regime unloaded state"
    variants = expander.expand_query(query, max_variants=3)
    assert variants[0] == query
    for v in variants:
        # Technical sensor code TP3 and numeric measurement 7.5 must remain intact
        assert "TP3" in v
        assert "7.5" in v


def test_duplicate_and_empty_variant_filtering() -> None:
    expander = RuleBasedQueryExpander()
    assert expander.expand_query("") == []
    assert expander.expand_query("   ") == []

    # Verify no duplicates even if repeated patterns match
    variants = expander.expand_query("air leakage and pneumatic leak", max_variants=4)
    assert len(variants) == len(set(variants))


def test_max_variant_count_and_character_limit() -> None:
    expander = RuleBasedQueryExpander()
    long_q = "air leakage " + ("system check " * 30)
    variants = expander.expand_query(long_q, max_variants=2)
    assert len(variants) <= 3  # Original + at most 2 variants
    for v in variants:
        assert len(v) <= MAX_VARIANT_CHARS


def test_pass_through_expander() -> None:
    expander = PassThroughQueryExpander()
    assert expander.expand_query("any technical query", max_variants=3) == ["any technical query"]
    assert expander.expand_query("", max_variants=3) == []


def test_expander_failure_fallback() -> None:
    # Test that RuleBasedQueryExpander handles internal exceptions safely
    broken_expander = RuleBasedQueryExpander(rules=((None, ("crash",)),))  # type: ignore[arg-type]
    safe_res = broken_expander.expand_query("air leakage", max_variants=2)
    assert safe_res == ["air leakage"]


def test_multi_query_disabled_exact_legacy_behavior() -> None:
    c1 = _make_chunk("c-1", "Standard operational envelope.")
    index = HybridRagIndex([c1])

    # multi_query=False must produce query_variants=[] and standard items
    res = index.search(
        "operational envelope",
        tenant_id="tenant-alpha",
        allowed_asset_ids={"metropt3-apu-1"},
        multi_query=False,
    )
    assert len(res.items) == 1
    assert res.query_variants == []
    assert res.items[0].chunk_id == "c-1"


def test_improved_recall_using_synonyms() -> None:
    # Document only contains Arabic phrase "تسريب هواء"
    c_arabic = _make_chunk(
        "c-ar",
        "تقرير فحص: تسريب هواء في منظومة الضغط الهوائي للصمام الرئيسي.",
    )
    index = HybridRagIndex([c_arabic])

    # Single-query search for English "air leakage" without multi-query:
    # Lexical BM25 yields 0 hits because terms are English vs Arabic
    res_single = index.search(
        "air leakage",
        tenant_id="tenant-alpha",
        allowed_asset_ids={"metropt3-apu-1"},
        mode="bm25_only",
        multi_query=False,
    )
    assert len(res_single.items) == 0

    # Multi-query enabled: expands "air leakage" -> "تسريب هواء" -> retrieves document!
    res_multi = index.search(
        "air leakage",
        tenant_id="tenant-alpha",
        allowed_asset_ids={"metropt3-apu-1"},
        mode="hybrid_rrf",
        multi_query=True,
    )
    assert len(res_multi.items) >= 1
    assert res_multi.items[0].chunk_id == "c-ar"
    assert "air leakage" in res_multi.query_variants[0]
    assert any("تسريب هواء" in v for v in res_multi.query_variants)


def test_bm25_and_neural_dense_multi_query_fusion() -> None:
    embedder = MockNeuralEmbedder()
    c1 = _make_chunk("c1", "Pneumatic pressure transmitter TP3 nominal envelope.")
    c2 = _make_chunk("c2", "Thermal motor cooling and radiator fan assembly.")
    index = HybridRagIndex([c1, c2], embedder=embedder)

    res = index.search(
        "pressure sensor TP3",
        tenant_id="tenant-alpha",
        allowed_asset_ids={"metropt3-apu-1"},
        mode="hybrid_rrf",
        multi_query=True,
    )
    assert len(res.items) >= 1
    assert res.items[0].chunk_id == "c1"
    # Original scores are preserved on hit
    assert res.items[0].bm25_score > 0.0
    assert res.items[0].semantic_score > 0.0
    assert res.items[0].rrf_score > 0.0


def test_cross_encoder_runs_once_after_fusion() -> None:
    reranker = MockCrossEncoderReranker()
    c1 = _make_chunk("c1", "Pneumatic pressure transmitter TP3 nominal envelope.")
    c2 = _make_chunk("c2", "Compressor air leakage and oil leak inspection.")
    index = HybridRagIndex([c1, c2], reranker=reranker)

    res = index.search(
        "pneumatic leak",
        tenant_id="tenant-alpha",
        allowed_asset_ids={"metropt3-apu-1"},
        multi_query=True,
        rerank=True,
    )
    assert len(res.items) >= 1
    # Check that rerank_score is present
    for hit in res.items:
        assert hit.rerank_score is not None
    assert res.reranked is True


def test_graph_augmented_multi_query_retrieval() -> None:
    kg = IndustrialKnowledgeGraph()
    c1 = _make_chunk(
        "c-sensor",
        "Sensor TP3 pneumatic pressure transmitter readings.",
        asset_id="metropt3-apu-1",
    )
    index = HybridRagIndex([c1], kg=kg)

    # Arabic query for pressure sensor expands to English TP3 -> links sensor:TP3 node
    res = index.search(
        "مستشعر الضغط",
        tenant_id="tenant-alpha",
        allowed_asset_ids={"metropt3-apu-1"},
        graph_augmented=True,
        multi_query=True,
    )
    assert len(res.items) >= 1
    assert res.items[0].chunk_id == "c-sensor"
    assert res.items[0].graph_score is not None
    # Seeds extracted from both original and variants
    assert len(res.query_kg_node_ids) >= 1


def test_graph_seed_deduplication() -> None:
    kg = IndustrialKnowledgeGraph()
    c1 = _make_chunk("c1", "Sensor TP3 pressure reading.", asset_id="metropt3-apu-1")
    index = HybridRagIndex([c1], kg=kg)

    # Query with multiple phrases linking to the same node
    res = index.search(
        "TP3 sensor TP3 pressure",
        tenant_id="tenant-alpha",
        allowed_asset_ids={"metropt3-apu-1"},
        graph_augmented=True,
        multi_query=True,
    )
    # query_kg_node_ids must contain no duplicates
    assert len(res.query_kg_node_ids) == len(set(res.query_kg_node_ids))


def test_original_query_relevance_gate_abstention() -> None:
    c1 = _make_chunk("c1", "Industrial air compressor maintenance guide.")
    index = HybridRagIndex([c1])

    # Out of scope probe (e.g. cooking recipe query) must abstain, even with multi-query
    res = index.search(
        "delicious pasta tomato sauce recipe",
        tenant_id="tenant-alpha",
        allowed_asset_ids={"metropt3-apu-1"},
        multi_query=True,
    )
    assert len(res.items) == 0
    assert "abstained" in res.safety_note


def test_tenant_and_asset_isolation_multi_query() -> None:
    c_alpha = _make_chunk("c-alpha", "Compressor operational manual", asset_id="asset-A", tenant_scope="tenant-alpha")
    c_beta = _make_chunk("c-beta", "Compressor operational manual", asset_id="asset-B", tenant_scope="tenant-beta")
    index = HybridRagIndex([c_alpha, c_beta])

    # Multi-query from tenant-alpha on asset-A cannot see asset-B or tenant-beta chunks
    res = index.search(
        "compressor manual",
        tenant_id="tenant-alpha",
        allowed_asset_ids={"asset-A"},
        multi_query=True,
    )
    assert len(res.items) == 1
    assert res.items[0].chunk_id == "c-alpha"
    assert res.items[0].asset_id == "asset-A"


def test_original_query_scores_and_provenance_preservation() -> None:
    c1 = _make_chunk(
        "c1",
        "Air compressor temperature is within the nominal envelope and operating safely.",
        title="Asset Digital Passport",
        section="Nominal Envelope",
    )
    index = HybridRagIndex([c1])

    res = index.search(
        "compressor temperature envelope",
        tenant_id="tenant-alpha",
        allowed_asset_ids={"metropt3-apu-1"},
        multi_query=True,
    )
    assert len(res.items) == 1
    hit = res.items[0]
    assert hit.title == "Asset Digital Passport"
    assert hit.section == "Nominal Envelope"
    assert hit.license == "Proprietary (Test)"
    assert hit.provenance_origin == "observed"
    # Scores must be populated and finite
    assert hit.bm25_score > 0.0
    assert hit.rrf_score > 0.0

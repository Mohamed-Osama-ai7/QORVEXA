# ruff: noqa: E501
"""Tests for Task 4 (Member 3): Graph-Augmented Retrieval for QORVEXA.

Validates:
1. Arabic query entity linking.
2. English query entity linking.
3. Mixed Arabic-English query entity linking.
4. One-hop and two-hop graph expansion from seed entities.
5. Epistemic relationship handling:
   - physical_mechanism is prioritized (highest weight)
   - causal_hypothesis is included with uncertainty weight
   - correlational_indicator is not treated as causal evidence
6. Retrieval of a sensor-related document from a failure-mode query via causal graph link.
7. Graph score integration into Hybrid RRF alongside BM25 and neural retrieval.
8. Compatibility with Cross-Encoder reranking.
9. Duplicate candidate prevention in graph-augmented candidate selection.
10. Strict tenant isolation.
11. Strict asset isolation (no leakage across ungranted assets during entity linking or traversal).
12. graph_augmented=False preserving previous behavior.
13. Backward compatibility of response contracts (query_kg_node_ids and graph_score).
"""

from __future__ import annotations

import pytest

from qorvexa.contracts.knowledge import KnowledgeDocumentChunk
from qorvexa.knowledge.embeddings import MockNeuralEmbedder
from qorvexa.knowledge.kg import IndustrialKnowledgeGraph, infer_linked_kg_nodes
from qorvexa.knowledge.rag import HybridRagIndex
from qorvexa.knowledge.reranker import MockCrossEncoderReranker

# ============================================================================
# 1. Query Entity Linking (Arabic, English, Mixed)
# ============================================================================


def test_query_entity_linking_english() -> None:
    kg = IndustrialKnowledgeGraph()
    allowed = {"metropt3-apu-1"}

    # English technical terms
    assert infer_linked_kg_nodes("TP3 pressure decay", allowed_asset_ids=allowed, kg=kg) == ["sensor:TP3"]
    assert infer_linked_kg_nodes("air leak across compressor stage", allowed_asset_ids=allowed, kg=kg) == [
        "mode:air_leakage",
        "comp:compressor-stage",
    ]
    assert infer_linked_kg_nodes("check oil temperature and motor current", allowed_asset_ids=allowed, kg=kg) == [
        "sensor:Motor_current",
        "sensor:Oil_temperature",
    ]


def test_query_entity_linking_arabic() -> None:
    kg = IndustrialKnowledgeGraph()
    allowed = {"metropt3-apu-1"}

    # Arabic technical terms
    assert infer_linked_kg_nodes("تسريب الهواء في الضاغط", allowed_asset_ids=allowed, kg=kg) == [
        "mode:air_leakage",
        "comp:compressor-stage",
    ]
    assert infer_linked_kg_nodes("ارتفاع حرارة الزيت في المحرك", allowed_asset_ids=allowed, kg=kg) == [
        "sensor:Oil_temperature",
    ]
    assert infer_linked_kg_nodes("انخفاض ضغط اللوحة وفحص صمام تفريغ", allowed_asset_ids=allowed, kg=kg) == [
        "sensor:TP3",
        "sensor:COMP_DV_eletric",
    ]


def test_query_entity_linking_mixed() -> None:
    kg = IndustrialKnowledgeGraph()
    allowed = {"metropt3-apu-1"}

    # Mixed Arabic/English query
    mixed_hits = infer_linked_kg_nodes(
        "فحص صمام DV_eletric عند حدوث تسريب هواء وارتفاع motor current",
        allowed_asset_ids=allowed,
        kg=kg,
    )
    assert "sensor:COMP_DV_eletric" in mixed_hits
    assert "mode:air_leakage" in mixed_hits
    assert "sensor:Motor_current" in mixed_hits


# ============================================================================
# 2. Graph Expansion & Epistemic Relationship Handling
# ============================================================================


def test_graph_expansion_one_hop_and_two_hop() -> None:
    kg = IndustrialKnowledgeGraph()
    allowed = {"metropt3-apu-1"}

    # 1-hop expansion from mode:air_leakage
    one_hop = kg.expand_seeds(["mode:air_leakage"], allowed_asset_ids=allowed, max_depth=1)
    assert one_hop["mode:air_leakage"] == 1.0
    # Causal hypothesis edges to sensor:TP3 and sensor:LPS
    assert one_hop["sensor:TP3"] == 0.7
    assert one_hop["sensor:LPS"] == 0.7
    assert one_hop["regime:loaded"] == 0.7

    # 2-hop expansion reaches components connected to TP3
    two_hop = kg.expand_seeds(["mode:air_leakage"], allowed_asset_ids=allowed, max_depth=2)
    assert "comp:pneumatic-reservoirs" in two_hop
    # comp:pneumatic-reservoirs is connected to sensor:TP3 via physical_mechanism (0.7 * 0.9 = 0.63)
    assert two_hop["comp:pneumatic-reservoirs"] == pytest.approx(0.63, abs=1e-3)


def test_epistemic_relationship_prioritization() -> None:
    kg = IndustrialKnowledgeGraph()
    allowed = {"metropt3-apu-1"}

    # By default, correlational_indicator edges (e.g. mode:air_leakage -> sensor:Oil_temperature)
    # are NOT treated as causal evidence and receive 0.0 factor (excluded)
    expanded = kg.expand_seeds(["mode:air_leakage"], allowed_asset_ids=allowed, max_depth=1)
    assert "sensor:Oil_temperature" not in expanded

    # Physical mechanism vs causal hypothesis weighting:
    # comp:compressor-stage -> sensor:TP2 is physical_mechanism (weight: 0.9)
    comp_expanded = kg.expand_seeds(["comp:compressor-stage"], allowed_asset_ids=allowed, max_depth=1)
    assert comp_expanded["sensor:TP2"] == 0.9
    assert comp_expanded["sensor:COMP_DV_eletric"] == 0.9


# ============================================================================
# 3. Graph-Augmented Candidate Retrieval & Score Integration
# ============================================================================


@pytest.fixture
def asset_corpus() -> list[KnowledgeDocumentChunk]:
    return [
        KnowledgeDocumentChunk(
            chunk_id="chunk-sensor-tp3",
            doc_id="doc-tp3",
            title="Panel Pressure Telemetry Calibration",
            section="TP3 Baseline and Sensor Diagnostics",
            asset_id="metropt3-apu-1",
            tenant_scope="acme",
            source_path="tp3_calib.md",
            license="MIT",
            provenance_origin="observed",
            text="TP3 monitors pneumatic panel pressure between 8.2 and 10.2 bar across service cycles.",
        ),
        KnowledgeDocumentChunk(
            chunk_id="chunk-leak-overview",
            doc_id="doc-leak",
            title="Pneumatic Loss Guide",
            section="Loss Summary",
            asset_id="metropt3-apu-1",
            tenant_scope="acme",
            source_path="leak.md",
            license="Internal",
            provenance_origin="observed",
            text="Unloaded hold pressure loss raises active compressor cycle frequency.",
        ),
        KnowledgeDocumentChunk(
            chunk_id="chunk-oil-thermal",
            doc_id="doc-oil",
            title="Oil Temperature Management",
            section="Thermal Envelope",
            asset_id="metropt3-apu-1",
            tenant_scope="acme",
            source_path="oil.md",
            license="MIT",
            provenance_origin="observed",
            text="Oil_temperature rises smoothly during extended loaded runtime.",
        ),
    ]


def test_graph_retrieval_finds_sensor_doc_from_failure_mode_query(
    asset_corpus: list[KnowledgeDocumentChunk],
) -> None:
    index = HybridRagIndex(asset_corpus)

    # Query names the failure mode ("air leakage") but NOT "TP3"
    # Because mode:air_leakage causally induces pressure decay on sensor:TP3,
    # graph augmentation links the query to mode:air_leakage and expands to sensor:TP3,
    # retrieving chunk-sensor-tp3 through the graph link!
    resp = index.search(
        "air leakage diagnostics",
        tenant_id="acme",
        allowed_asset_ids={"metropt3-apu-1"},
        top_k=3,
        graph_augmented=True,
    )
    assert len(resp.items) >= 1
    chunk_ids = [h.chunk_id for h in resp.items]
    assert "chunk-sensor-tp3" in chunk_ids

    tp3_hit = next(h for h in resp.items if h.chunk_id == "chunk-sensor-tp3")
    assert tp3_hit.graph_score is not None
    assert tp3_hit.graph_score > 0.0
    assert "sensor:TP3" in tp3_hit.linked_node_ids
    assert "mode:air_leakage" in resp.query_kg_node_ids


def test_graph_augmented_false_preserves_strict_lexical_behavior(
    asset_corpus: list[KnowledgeDocumentChunk],
) -> None:
    index = HybridRagIndex(asset_corpus)

    # When graph_augmented=False, query without literal mention of "TP3"
    # will not receive graph score or query_kg_node_ids
    resp_no_graph = index.search(
        "air leakage diagnostics",
        tenant_id="acme",
        allowed_asset_ids={"metropt3-apu-1"},
        top_k=3,
        graph_augmented=False,
    )
    assert resp_no_graph.query_kg_node_ids == []
    for hit in resp_no_graph.items:
        assert hit.graph_score is None


def test_graph_retrieval_with_neural_and_reranking(
    asset_corpus: list[KnowledgeDocumentChunk],
) -> None:
    embedder = MockNeuralEmbedder()
    reranker = MockCrossEncoderReranker()
    index = HybridRagIndex(asset_corpus, embedder=embedder, reranker=reranker)

    resp = index.search(
        "تسريب هواء في الضاغط",
        tenant_id="acme",
        allowed_asset_ids={"metropt3-apu-1"},
        top_k=3,
        graph_augmented=True,
        rerank=True,
    )
    assert resp.reranked is True
    assert "mode:air_leakage" in resp.query_kg_node_ids
    assert len(resp.items) >= 1
    top_hit = resp.items[0]
    # Reranker score, RRF score, semantic score, and graph score coexist cleanly
    assert top_hit.rrf_score > 0.0
    assert top_hit.semantic_score >= 0.0
    assert top_hit.rerank_score is not None
    assert top_hit.graph_score is not None
    assert top_hit.graph_score > 0.0


def test_duplicate_candidate_prevention(asset_corpus: list[KnowledgeDocumentChunk]) -> None:
    index = HybridRagIndex(asset_corpus)

    resp = index.search(
        "TP3 air leakage pressure",
        tenant_id="acme",
        allowed_asset_ids={"metropt3-apu-1"},
        top_k=5,
        graph_augmented=True,
    )
    # Every chunk in the response must appear at most once
    chunk_ids = [h.chunk_id for h in resp.items]
    assert len(chunk_ids) == len(set(chunk_ids))


# ============================================================================
# 4. Security: Tenant & Asset Isolation
# ============================================================================


def test_tenant_and_asset_isolation_in_graph_retrieval() -> None:
    c_acme = KnowledgeDocumentChunk(
        chunk_id="chunk-acme",
        doc_id="doc-acme",
        title="Acme Pneumatic Manual",
        section="TP3 Monitoring",
        asset_id="metropt3-apu-1",
        tenant_scope="acme",
        source_path="acme.md",
        license="Internal",
        provenance_origin="observed",
        text="Acme TP3 sensor telemetry and air leakage diagnostic procedure.",
    )
    c_beta = KnowledgeDocumentChunk(
        chunk_id="chunk-beta",
        doc_id="doc-beta",
        title="Beta Confidential Manual",
        section="Secret",
        asset_id="metropt3-apu-1",
        tenant_scope="beta",
        source_path="beta.md",
        license="Internal",
        provenance_origin="observed",
        text="Beta secret air leakage telemetry tokenabc999.",
    )
    c_ai4i = KnowledgeDocumentChunk(
        chunk_id="chunk-ai4i",
        doc_id="doc-ai4i",
        title="AI4I Milling Manual",
        section="Milling Tool",
        asset_id="ai4i-milling-bench",
        tenant_scope="acme",
        source_path="ai4i.md",
        license="MIT",
        provenance_origin="observed",
        text="AI4I milling machine tool wear failure prediction.",
    )

    index = HybridRagIndex([c_acme, c_beta, c_ai4i])

    # ACME tenant querying with asset metropt3-apu-1 only:
    # 1. Beta chunk must NOT be accessible
    # 2. AI4I chunk and nodes must NOT be accessible or returned in query_kg_node_ids / edges
    resp = index.search(
        "air leakage ai4i milling",
        tenant_id="acme",
        allowed_asset_ids={"metropt3-apu-1"},
        top_k=5,
        graph_augmented=True,
    )
    assert len(resp.items) == 1
    assert resp.items[0].chunk_id == "chunk-acme"
    assert "asset:ai4i-milling-bench" not in resp.query_kg_node_ids
    assert all(e.source_id != "asset:ai4i-milling-bench" for e in resp.related_kg_edges)
    assert all(e.target_id != "asset:ai4i-milling-bench" for e in resp.related_kg_edges)


def test_relevance_gate_abstains_on_unrelated_queries_with_graph_augmented() -> None:
    c = KnowledgeDocumentChunk(
        chunk_id="chunk-1",
        doc_id="doc-1",
        title="MetroPT Guide",
        section="Section",
        asset_id="metropt3-apu-1",
        tenant_scope="acme",
        source_path="test.md",
        license="MIT",
        provenance_origin="observed",
        text="TP3 compressor air leak maintenance.",
    )
    index = HybridRagIndex([c])

    # Out of scope queries have no matching tokens AND no matching graph entities
    oos = "What is the recipe for baking chocolate cookies?"
    resp = index.search(
        oos,
        tenant_id="acme",
        allowed_asset_ids={"metropt3-apu-1"},
        top_k=5,
        graph_augmented=True,
    )
    assert resp.items == []
    assert resp.query_kg_node_ids == []
    assert "abstained" in resp.safety_note.lower()

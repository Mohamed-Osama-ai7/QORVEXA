# ruff: noqa: E501
"""Tests for Task 3 (Member 3): Neural Cross-Encoder Reranker for QORVEXA.

Validates:
1. Cross-Encoder scoring on query-document pairs.
2. Correct ranking order refinement based on deep cross-attention scores.
3. Multilingual queries: Arabic, English, and mixed-language queries.
4. Empty candidate lists and empty query handling.
5. Model-loading failure and clear exception hierarchy.
6. Inference failure handling.
7. Candidate budget limit behavior (rerank_top_n).
8. Metadata and provenance preservation (license, provenance_origin, bm25_score, semantic_score, rrf_score).
9. Tenant and asset isolation with reranking enabled.
10. Reranking enabled vs disabled (rerank=True vs rerank=False, reranked flag, rerank_score presence).
11. Regression verification of existing retrieval behavior.
"""

from __future__ import annotations

import pytest

from qorvexa.contracts.knowledge import KnowledgeDocumentChunk
from qorvexa.knowledge.embeddings import MockNeuralEmbedder
from qorvexa.knowledge.rag import HybridRagIndex
from qorvexa.knowledge.reranker import (
    DEFAULT_RERANKER_MODEL,
    CrossEncoderReranker,
    MockCrossEncoderReranker,
    RerankerModelLoadError,
    RerankError,
    RerankerUnavailableError,
    get_reranker,
)

# ============================================================================
# 1. Model Configuration & Factory Tests
# ============================================================================


def test_default_reranker_model_configuration() -> None:
    assert DEFAULT_RERANKER_MODEL == "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"


def test_get_reranker_factory_prefer_mock() -> None:
    reranker = get_reranker(prefer_mock=True)
    assert isinstance(reranker, MockCrossEncoderReranker)
    assert DEFAULT_RERANKER_MODEL in reranker.model_name


def test_cross_encoder_missing_dependency_raises_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    with pytest.raises(RerankerUnavailableError) as exc_info:
        CrossEncoderReranker(DEFAULT_RERANKER_MODEL)
    assert "sentence-transformers is required" in str(exc_info.value)
    assert "pip install sentence-transformers" in str(exc_info.value)


def test_mock_reranker_error_handling() -> None:
    with pytest.raises(RerankerModelLoadError) as load_err:
        MockCrossEncoderReranker(fail_on_load=True)
    assert "Simulated failure loading Cross-Encoder model" in str(load_err.value)

    failing_reranker = MockCrossEncoderReranker(fail_on_rerank=True)
    with pytest.raises(RerankError) as rerank_err:
        failing_reranker.score_pairs([("query", "document")])
    assert "Simulated Cross-Encoder inference computation failure" in str(rerank_err.value)


# ============================================================================
# 2. Cross-Encoder Pair Scoring Across Languages
# ============================================================================


def test_mock_scoring_arabic_english_and_mixed() -> None:
    reranker = MockCrossEncoderReranker()

    # Arabic relevant vs irrelevant
    pairs_ar = [
        ("تسريب ضغط الهواء في الضاغط TP3", "انخفاض ضغط الهواء في خزان الضاغط TP3 يدل على تسريب"),
        ("تسريب ضغط الهواء في الضاغط TP3", "وصفة عمل الكعك بالشوكولاتة والسكر"),
    ]
    scores_ar = reranker.score_pairs(pairs_ar)
    assert len(scores_ar) == 2
    assert scores_ar[0] > scores_ar[1]
    assert scores_ar[0] > 0.5
    assert scores_ar[1] == 0.0

    # English relevant vs irrelevant
    pairs_en = [
        ("compressor unloaded pressure decay", "Rapid unloaded TP3 pressure decay indicates pneumatic leak"),
        ("compressor unloaded pressure decay", "Unrelated conveyor belt motor electrical bearing note"),
    ]
    scores_en = reranker.score_pairs(pairs_en)
    assert scores_en[0] > scores_en[1]
    assert scores_en[0] > 0.5

    # Mixed-language pair
    pairs_mixed = [
        ("فحص صمام DV_eletric switch", "Intake solenoid valve DV_eletric switch regulates airflow ومفتاح الضغط"),
        ("فحص صمام DV_eletric switch", "تقرير درجات الحرارة لمحطة التوليد"),
    ]
    scores_mixed = reranker.score_pairs(pairs_mixed)
    assert scores_mixed[0] > scores_mixed[1]
    assert scores_mixed[0] > 0.4


def test_empty_candidates_and_empty_queries() -> None:
    reranker = MockCrossEncoderReranker()
    assert reranker.score_pairs([]) == []

    blank_query_scores = reranker.score_pairs([("   ", "Some valid documentation text")])
    assert blank_query_scores == [0.0]

    blank_doc_scores = reranker.score_pairs([("Valid query", "   ")])
    assert blank_doc_scores == [0.0]


# ============================================================================
# 3. Hybrid Retrieval Integration with Reranking
# ============================================================================


@pytest.fixture
def sample_corpus() -> list[KnowledgeDocumentChunk]:
    return [
        KnowledgeDocumentChunk(
            chunk_id="chunk-partial-leak",
            doc_id="doc-1",
            title="Overview of Air System",
            section="General Reservoir",
            asset_id="metropt3-apu-1",
            tenant_scope="acme",
            source_path="overview.md",
            license="MIT",
            provenance_origin="observed",
            text="General unloaded pressure monitoring across the pneumatic compressor reservoir tanks.",
        ),
        KnowledgeDocumentChunk(
            chunk_id="chunk-exact-leak",
            doc_id="doc-2",
            title="TP3 Pressure Decay Diagnostics",
            section="Unloaded Leakage",
            asset_id="metropt3-apu-1",
            tenant_scope="acme",
            source_path="leak_diag.md",
            license="Internal",
            provenance_origin="observed",
            text="Rapid unloaded TP3 pressure decay indicates severe pneumatic air leakage across valve DV_eletric.",
        ),
        KnowledgeDocumentChunk(
            chunk_id="chunk-ar-valve",
            doc_id="doc-3",
            title="دليل الصمامات الهوائية",
            section="صمام DV_eletric",
            asset_id="metropt3-apu-1",
            tenant_scope="acme",
            source_path="ar_valve.md",
            license="Internal",
            provenance_origin="observed",
            text="صمام الهواء الكهرومغناطيسي DV_eletric يتحكم في تسريب ضغط الهواء وتفريغ الخزان.",
        ),
        KnowledgeDocumentChunk(
            chunk_id="chunk-ar-thermal",
            doc_id="doc-4",
            title="دليل الإدارة الحرارية",
            section="حرارة الزيت",
            asset_id="metropt3-apu-1",
            tenant_scope="acme",
            source_path="ar_thermal.md",
            license="MIT",
            provenance_origin="observed",
            text="ارتفاع حرارة الزيت في محرك الضاغط يستلزم تشغيل مروحة التبريد الإضافية فورا.",
        ),
    ]


def test_reranker_reorders_candidates_by_cross_attention_score(
    sample_corpus: list[KnowledgeDocumentChunk],
) -> None:
    embedder = MockNeuralEmbedder()
    reranker = MockCrossEncoderReranker()
    index = HybridRagIndex(sample_corpus, embedder=embedder, reranker=reranker)

    # Search with reranking enabled
    resp = index.search(
        "unloaded TP3 pressure decay valve DV_eletric",
        tenant_id="acme",
        allowed_asset_ids={"metropt3-apu-1"},
        top_k=3,
        rerank=True,
    )

    assert resp.reranked is True
    assert len(resp.items) >= 2
    top_hit = resp.items[0]
    # The exact diagnosis chunk has both unloaded pressure decay and valve DV_eletric
    assert top_hit.chunk_id == "chunk-exact-leak"
    assert top_hit.rank == 1
    assert top_hit.rerank_score is not None
    assert top_hit.rerank_score > 0.5
    # Original scores are fully preserved
    assert top_hit.bm25_score > 0.0
    assert top_hit.semantic_score > 0.0
    assert top_hit.rrf_score > 0.0


def test_reranker_multilingual_arabic_refinement(
    sample_corpus: list[KnowledgeDocumentChunk],
) -> None:
    embedder = MockNeuralEmbedder()
    reranker = MockCrossEncoderReranker()
    index = HybridRagIndex(sample_corpus, embedder=embedder, reranker=reranker)

    resp = index.search(
        "تسريب ضغط صمام الهواء DV_eletric",
        tenant_id="acme",
        allowed_asset_ids={"metropt3-apu-1"},
        top_k=2,
        rerank=True,
    )
    assert resp.reranked is True
    assert len(resp.items) >= 1
    assert resp.items[0].chunk_id == "chunk-ar-valve"
    assert resp.items[0].rerank_score is not None
    assert resp.items[0].rerank_score > 0.4


def test_reranking_disabled_preserves_pure_rrf_order(
    sample_corpus: list[KnowledgeDocumentChunk],
) -> None:
    embedder = MockNeuralEmbedder()
    reranker = MockCrossEncoderReranker()
    index = HybridRagIndex(sample_corpus, embedder=embedder, reranker=reranker)

    # When rerank=False, reranking must NOT be applied
    resp = index.search(
        "unloaded TP3 pressure decay",
        tenant_id="acme",
        allowed_asset_ids={"metropt3-apu-1"},
        top_k=3,
        rerank=False,
    )
    assert resp.reranked is False
    for hit in resp.items:
        assert hit.rerank_score is None


def test_reranking_with_no_reranker_configured(
    sample_corpus: list[KnowledgeDocumentChunk],
) -> None:
    # Index without reranker
    index = HybridRagIndex(sample_corpus)
    assert index.reranker is None

    resp = index.search(
        "unloaded TP3 pressure decay",
        tenant_id="acme",
        allowed_asset_ids={"metropt3-apu-1"},
        top_k=3,
        rerank=True,
    )
    assert resp.reranked is False
    for hit in resp.items:
        assert hit.rerank_score is None


def test_rerank_candidate_limit_budget(sample_corpus: list[KnowledgeDocumentChunk]) -> None:
    embedder = MockNeuralEmbedder()
    reranker = MockCrossEncoderReranker()
    index = HybridRagIndex(sample_corpus, embedder=embedder, reranker=reranker)

    # With rerank_top_n=1, only the top 1 RRF candidate is sent to reranker
    resp = index.search(
        "pneumatic air reservoir",
        tenant_id="acme",
        allowed_asset_ids={"metropt3-apu-1"},
        top_k=3,
        rerank=True,
        rerank_top_n=1,
    )
    assert resp.reranked is True
    assert len(resp.items) >= 1


def test_provenance_and_metadata_preservation(
    sample_corpus: list[KnowledgeDocumentChunk],
) -> None:
    embedder = MockNeuralEmbedder()
    reranker = MockCrossEncoderReranker()
    index = HybridRagIndex(sample_corpus, embedder=embedder, reranker=reranker)

    resp = index.search(
        "unloaded TP3 pressure decay",
        tenant_id="acme",
        allowed_asset_ids={"metropt3-apu-1"},
        top_k=2,
        rerank=True,
    )
    assert len(resp.items) >= 1
    hit = resp.items[0]
    assert hit.license in ("MIT", "Internal")
    assert hit.provenance_origin == "observed"
    assert hit.asset_id == "metropt3-apu-1"
    assert hit.source_path.endswith(".md")
    assert "neural cross-encoder reranking" in resp.safety_note


def test_tenant_and_asset_isolation_with_reranker() -> None:
    embedder = MockNeuralEmbedder()
    reranker = MockCrossEncoderReranker()

    c_acme = KnowledgeDocumentChunk(
        chunk_id="chunk-acme-confidential",
        doc_id="doc-acme",
        title="Acme Confidential Compressor Guide",
        section="Secret Section",
        asset_id="metropt3-apu-1",
        tenant_scope="acme",
        source_path="acme.md",
        license="Internal",
        provenance_origin="observed",
        text="Acme confidential diagnostic tokenxyz999 for pneumatic compressor.",
    )
    c_beta = KnowledgeDocumentChunk(
        chunk_id="chunk-beta-confidential",
        doc_id="doc-beta",
        title="Beta Confidential Turbine Guide",
        section="Secret Section",
        asset_id="metropt3-apu-1",
        tenant_scope="beta",
        source_path="beta.md",
        license="Internal",
        provenance_origin="observed",
        text="Beta secret turbine diagnosis tokenabc888 confidential log.",
    )

    index = HybridRagIndex([c_acme, c_beta], embedder=embedder, reranker=reranker)

    # ACME tenant querying Beta's token must abstain / return empty
    acme_resp = index.search(
        "tokenabc888 confidential",
        tenant_id="acme",
        allowed_asset_ids={"metropt3-apu-1"},
        top_k=5,
        rerank=True,
    )
    assert acme_resp.items == []
    assert acme_resp.reranked is False
    assert acme_resp.accessible_chunks == 1

    # Beta tenant sees their own document and gets it reranked
    beta_resp = index.search(
        "tokenabc888 confidential",
        tenant_id="beta",
        allowed_asset_ids={"metropt3-apu-1"},
        top_k=5,
        rerank=True,
    )
    assert len(beta_resp.items) == 1
    assert beta_resp.items[0].chunk_id == "chunk-beta-confidential"
    assert beta_resp.reranked is True
    assert beta_resp.items[0].rerank_score is not None


def test_relevance_gate_abstains_before_reranker(
    sample_corpus: list[KnowledgeDocumentChunk],
) -> None:
    embedder = MockNeuralEmbedder()
    reranker = MockCrossEncoderReranker()
    index = HybridRagIndex(sample_corpus, embedder=embedder, reranker=reranker)

    oos_queries = (
        "What is the capital of Italy?",
        "طريقة عمل كعكة الفراولة اللذيذة",
    )
    for q in oos_queries:
        resp = index.search(
            q,
            tenant_id="acme",
            allowed_asset_ids={"metropt3-apu-1"},
            top_k=5,
            rerank=True,
        )
        assert resp.items == []
        assert resp.reranked is False
        assert "abstained" in resp.safety_note.lower()

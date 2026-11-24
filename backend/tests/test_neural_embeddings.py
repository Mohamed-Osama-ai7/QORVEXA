# ruff: noqa: E501
"""Tests for Task 2 (Member 3): Neural Embeddings for QORVEXA.

Validates:
1. Multilingual model selection (paraphrase-multilingual-MiniLM-L12-v2, 384 dimensions).
2. Embedding generation across Arabic, English, and mixed-language inputs.
3. Empty input handling (empty text batches, whitespace queries).
4. Model-loading failure handling and clear exception hierarchy.
5. Dense neural retrieval integrated into HybridRagIndex.
6. Verification that TF-IDF/SVD is not silently presented as neural embeddings.
7. Existing BM25 and Hybrid RRF fusion with neural semantic scores.
8. Cross-tenant and asset isolation in dense neural search.
9. Relevance gate abstention on out-of-scope queries with neural embeddings.
"""

from __future__ import annotations

import numpy as np
import pytest

from qorvexa.contracts.knowledge import KnowledgeDocumentChunk
from qorvexa.knowledge.embeddings import (
    DEFAULT_EMBEDDING_DIM,
    DEFAULT_MULTILINGUAL_MODEL,
    EmbeddingError,
    MockNeuralEmbedder,
    ModelLoadError,
    NeuralEmbeddingsUnavailableError,
    SentenceTransformerEmbedder,
    get_embedder,
)
from qorvexa.knowledge.rag import HybridRagIndex

# ============================================================================
# 1. Model Configuration & Factory Tests
# ============================================================================


def test_default_multilingual_model_configuration() -> None:
    assert DEFAULT_MULTILINGUAL_MODEL == "paraphrase-multilingual-MiniLM-L12-v2"
    assert DEFAULT_EMBEDDING_DIM == 384


def test_get_embedder_factory_prefer_mock() -> None:
    embedder = get_embedder(prefer_mock=True)
    assert isinstance(embedder, MockNeuralEmbedder)
    assert embedder.dimension == 384
    assert DEFAULT_MULTILINGUAL_MODEL in embedder.model_name


def test_sentence_transformer_embedder_missing_dependency_raises_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # If sentence_transformers is not installed, instantiation must fail loudly with actionable error
    import sys

    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    with pytest.raises(NeuralEmbeddingsUnavailableError) as exc_info:
        SentenceTransformerEmbedder(DEFAULT_MULTILINGUAL_MODEL)
    assert "sentence-transformers is required" in str(exc_info.value)
    assert "pip install sentence-transformers" in str(exc_info.value)


def test_mock_embedder_error_handling() -> None:
    with pytest.raises(ModelLoadError) as load_err:
        MockNeuralEmbedder(fail_on_load=True)
    assert "Simulated failure loading neural model" in str(load_err.value)

    failing_embedder = MockNeuralEmbedder(fail_on_embed=True)
    with pytest.raises(EmbeddingError) as embed_err:
        failing_embedder.embed_texts(["any text"])
    assert "Simulated neural embedding computation failure" in str(embed_err.value)

    with pytest.raises(EmbeddingError) as query_err:
        failing_embedder.embed_query("any query")
    assert "Simulated neural embedding computation failure" in str(query_err.value)


# ============================================================================
# 2. Embedding Generation Across Languages
# ============================================================================


def test_mock_embedding_shape_and_l2_normalization() -> None:
    embedder = MockNeuralEmbedder()
    texts = [
        "Unloaded TP3 air leak pressure decay",
        "انخفاض ضغط الهواء في خزان الضاغط",
        "Mixed query: فحص صمام COMP_1 and DV_eletric",
    ]
    vectors = embedder.embed_texts(texts)
    assert vectors.shape == (3, 384)
    for v in vectors:
        norm = float(np.linalg.norm(v))
        assert pytest.approx(norm, abs=1e-5) == 1.0


def test_empty_and_whitespace_inputs() -> None:
    embedder = MockNeuralEmbedder()
    empty_batch = embedder.embed_texts([])
    assert empty_batch.shape == (0, 384)

    blank_query = embedder.embed_query("    \t\n  ")
    assert blank_query.shape == (384,)
    assert not np.any(blank_query)


def test_semantic_similarity_overlap() -> None:
    embedder = MockNeuralEmbedder()

    # Arabic related vs unrelated
    v_ar1 = embedder.embed_query("تسريب ضغط الهواء في الضاغط TP3")
    v_ar2 = embedder.embed_query("انخفاض ضغط هواء الضاغط TP3")
    v_unrelated = embedder.embed_query("طريقة عمل كعكة الشوكولاتة اللذيذة")

    sim_related = float(np.dot(v_ar1, v_ar2))
    sim_unrelated = float(np.dot(v_ar1, v_unrelated))
    assert sim_related > 0.5, f"Expected high similarity for related Arabic queries, got {sim_related}"
    assert sim_unrelated == 0.0, f"Expected zero similarity for unrelated query, got {sim_unrelated}"

    # English related vs unrelated
    v_en1 = embedder.embed_query("compressor unloaded air leakage")
    v_en2 = embedder.embed_query("unloaded air leakage duty cycle")
    sim_en = float(np.dot(v_en1, v_en2))
    assert sim_en > 0.5


# ============================================================================
# 3. Dense Neural Retrieval in HybridRagIndex
# ============================================================================


@pytest.fixture
def multilingual_chunks() -> list[KnowledgeDocumentChunk]:
    return [
        KnowledgeDocumentChunk(
            chunk_id="chunk-ar-leak",
            doc_id="doc-ar-1",
            title="دليل صيانة شبكة الهواء",
            section="تسريب الهواء",
            asset_id="metropt3-apu-1",
            tenant_scope="acme",
            source_path="ar_leak.md",
            license="Internal",
            provenance_origin="observed",
            text="انخفاض ضغط الهواء في خزان TP3 يدل على وجود تسريب هوائي في شبكة الصمامات.",
        ),
        KnowledgeDocumentChunk(
            chunk_id="chunk-ar-oil",
            doc_id="doc-ar-2",
            title="دليل الإدارة الحرارية",
            section="حرارة الزيت",
            asset_id="metropt3-apu-1",
            tenant_scope="acme",
            source_path="ar_thermal.md",
            license="Internal",
            provenance_origin="observed",
            text="ارتفاع درجة حرارة الزيت فوق الحد الطبيعي يتطلب فحص مروحة التبريد ومستوى الزيت.",
        ),
        KnowledgeDocumentChunk(
            chunk_id="chunk-bilingual",
            doc_id="doc-bi-1",
            title="Bilingual Solenoid Valve Reference",
            section="DV_eletric و COMP",
            asset_id="metropt3-apu-1",
            tenant_scope="acme",
            source_path="bi_valves.md",
            license="MIT",
            provenance_origin="observed",
            text="Intake solenoid valve DV_eletric switch regulates intake airflow ومفتاح ضغط الهواء.",
        ),
        KnowledgeDocumentChunk(
            chunk_id="chunk-en-leak",
            doc_id="doc-en-1",
            title="MetroPT-3 Maintenance Note",
            section="Unloaded TP3 Pressure Decay",
            asset_id="metropt3-apu-1",
            tenant_scope="acme",
            source_path="en_leak.md",
            license="MIT",
            provenance_origin="observed",
            text="Rapid unloaded TP3 pressure decay indicates pneumatic air leakage across main reservoir.",
        ),
    ]


def test_dense_backend_reports_correctly(multilingual_chunks: list[KnowledgeDocumentChunk]) -> None:
    embedder = MockNeuralEmbedder()
    neural_index = HybridRagIndex(multilingual_chunks, embedder=embedder)
    assert neural_index.dense_backend == "neural"
    assert neural_index.embedder is embedder

    svd_index = HybridRagIndex(multilingual_chunks)
    assert svd_index.dense_backend == "tfidf_svd"
    assert svd_index.embedder is None

    # dense_neural_only must fail clearly if neural embeddings are not configured
    with pytest.raises(ValueError) as exc_info:
        svd_index.search(
            "unloaded pressure",
            tenant_id="acme",
            allowed_asset_ids={"metropt3-apu-1"},
            mode="dense_neural_only",
        )
    assert "dense_neural_only" in str(exc_info.value)
    assert "requires a neural embedder" in str(exc_info.value)

    # dense_svd_only must fail clearly if neural embedder is active
    with pytest.raises(ValueError) as exc_info2:
        neural_index.search(
            "unloaded pressure",
            tenant_id="acme",
            allowed_asset_ids={"metropt3-apu-1"},
            mode="dense_svd_only",
        )
    assert "dense_svd_only" in str(exc_info2.value)
    assert "requires TF-IDF/SVD backend" in str(exc_info2.value)


def test_neural_dense_retrieval_arabic_query(
    multilingual_chunks: list[KnowledgeDocumentChunk],
) -> None:
    embedder = MockNeuralEmbedder()
    index = HybridRagIndex(multilingual_chunks, embedder=embedder)

    resp = index.search(
        "تسريب ضغط الهواء في TP3",
        tenant_id="acme",
        allowed_asset_ids={"metropt3-apu-1"},
        top_k=3,
        mode="dense_neural_only",
    )
    assert len(resp.items) >= 1
    top_hit = resp.items[0]
    assert top_hit.chunk_id == "chunk-ar-leak"
    assert top_hit.semantic_score > 0.0


def test_neural_dense_retrieval_english_query(
    multilingual_chunks: list[KnowledgeDocumentChunk],
) -> None:
    embedder = MockNeuralEmbedder()
    index = HybridRagIndex(multilingual_chunks, embedder=embedder)

    resp = index.search(
        "unloaded TP3 pressure decay",
        tenant_id="acme",
        allowed_asset_ids={"metropt3-apu-1"},
        top_k=3,
        mode="dense_neural_only",
    )
    assert len(resp.items) >= 1
    assert resp.items[0].chunk_id == "chunk-en-leak"
    assert resp.items[0].semantic_score > 0.0


def test_neural_hybrid_rrf_retrieval(multilingual_chunks: list[KnowledgeDocumentChunk]) -> None:
    embedder = MockNeuralEmbedder()
    index = HybridRagIndex(multilingual_chunks, embedder=embedder)

    resp = index.search(
        "درجة حرارة الزيت ومروحة التبريد",
        tenant_id="acme",
        allowed_asset_ids={"metropt3-apu-1"},
        top_k=3,
        mode="hybrid_rrf",
    )
    assert len(resp.items) >= 1
    top_hit = resp.items[0]
    assert top_hit.chunk_id == "chunk-ar-oil"
    assert top_hit.bm25_score > 0.0
    assert top_hit.semantic_score > 0.0
    assert top_hit.rrf_score > 0.0


def test_relevance_gate_abstains_on_unrelated_queries_with_neural_index(
    multilingual_chunks: list[KnowledgeDocumentChunk],
) -> None:
    embedder = MockNeuralEmbedder()
    index = HybridRagIndex(multilingual_chunks, embedder=embedder)

    oos_queries = (
        "What is the capital of France?",
        "ما هي عاصمة فرنسا؟",
        "Chocolate cake recipe with sugar",
        "طريقة عمل كعكة الشوكولاتة في المنزل",
    )
    for q in oos_queries:
        for mode in ("bm25_only", "dense_neural_only", "hybrid_rrf"):
            resp = index.search(
                q,
                tenant_id="acme",
                allowed_asset_ids={"metropt3-apu-1"},
                top_k=5,
                mode=mode,  # type: ignore[arg-type]
            )
            assert resp.items == [], f"Query {q!r} was not rejected in mode={mode}"
            assert "abstained" in resp.safety_note.lower()


def test_tenant_isolation_with_neural_embeddings() -> None:
    embedder = MockNeuralEmbedder()
    acme_chunk = KnowledgeDocumentChunk(
        chunk_id="acme-secret",
        doc_id="acme-doc",
        title="Acme Secret Manual",
        section="Secret",
        asset_id="metropt3-apu-1",
        tenant_scope="acme",
        source_path="acme.md",
        license="Internal",
        provenance_origin="observed",
        text="Acme confidential diagnostic tokenxyz999 for pneumatic compressor.",
    )
    beta_chunk = KnowledgeDocumentChunk(
        chunk_id="beta-secret",
        doc_id="beta-doc",
        title="Beta Secret Manual",
        section="Secret",
        asset_id="metropt3-apu-1",
        tenant_scope="beta",
        source_path="beta.md",
        license="Internal",
        provenance_origin="observed",
        text="Beta secret turbine diagnosis tokenabc888 confidential log.",
    )
    index = HybridRagIndex([acme_chunk, beta_chunk], embedder=embedder)

    # ACME tenant querying Beta secret token must get 0 hits across all modes
    for mode in ("bm25_only", "dense_neural_only", "hybrid_rrf"):
        resp = index.search(
            "tokenabc888 confidential",
            tenant_id="acme",
            allowed_asset_ids={"metropt3-apu-1"},
            top_k=5,
            mode=mode,  # type: ignore[arg-type]
        )
        assert resp.items == []
        assert resp.accessible_chunks == 1

    # Beta tenant sees their own document
    beta_resp = index.search(
        "tokenabc888 confidential",
        tenant_id="beta",
        allowed_asset_ids={"metropt3-apu-1"},
        top_k=5,
        mode="hybrid_rrf",
    )
    assert len(beta_resp.items) == 1
    assert beta_resp.items[0].chunk_id == "beta-secret"

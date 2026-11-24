# ruff: noqa: RUF001, RUF003, E501
# ruff: noqa: RUF003, E501
"""Tests for Task 1 (Member 3): Arabic & Multilingual Retrieval Support (P0).

Covers:
1. Arabic normalization (Alif, Ya, Taa Marbuta, Tatweel, Tashkeel, Arabic-Indic digits, NFKC).
2. Arabic tokenization & content token extraction with domain-preserved stopwords.
3. Mixed Arabic/English tokenization with technical identifiers.
4. Arabic BM25 scoring and retrieval ranking.
5. Hybrid retrieval (BM25 + Dense SVD + RRF) with Arabic and mixed queries.
6. Relevance gating & out-of-scope query rejection for Arabic queries.
7. Cross-tenant isolation with Arabic document chunks.
8. Backward compatibility and English retrieval regression invariance.
"""

from __future__ import annotations

import pytest

from qorvexa.contracts.knowledge import KnowledgeDocumentChunk
from qorvexa.knowledge.rag import (
    HybridRagIndex,
    extract_content_tokens,
    normalize_arabic,
)

# ============================================================================
# 1. Arabic Normalization Unit Tests
# ============================================================================


def test_arabic_normalization_alif_variants() -> None:
    # أ, إ, آ, ٱ all normalize to ا
    assert normalize_arabic("أحمد") == "احمد"
    assert normalize_arabic("إصلاح") == "اصلاح"
    assert normalize_arabic("آلة") == "اله"
    assert normalize_arabic("ٱرتفاع") == "ارتفاع"


def test_arabic_normalization_ya_and_taa_marbuta() -> None:
    # ى -> ي, ة -> ه
    assert normalize_arabic("أقصى") == "اقصي"
    assert normalize_arabic("درجة حرارة") == "درجه حراره"
    assert normalize_arabic("صيانة") == "صيانه"


def test_arabic_normalization_tatweel_and_diacritics() -> None:
    # Tatweel (kashida) removed
    assert normalize_arabic("الـــضـــاغـــط") == "الضاغط"
    # Tashkeel / diacritics removed
    assert normalize_arabic("ضَغْطُ الهَوَاءِ المُرْتَفِعِ") == "ضغط الهواء المرتفع"
    assert normalize_arabic("الـضَّــاغِطُ الأَوَّل") == "الضاغط الاول"


def test_arabic_normalization_digits() -> None:
    # Arabic-Indic and Eastern Arabic-Indic digits map to ASCII digits
    assert normalize_arabic("ضاغط رقم ١٢٣") == "ضاغط رقم 123"
    assert normalize_arabic("حرارة ٤٥ درجة") == "حراره 45 درجه"


def test_arabic_normalization_preserves_technical_identifiers() -> None:
    assert normalize_arabic("حساس TP3_pressure في COMP_1") == "حساس TP3_pressure في COMP_1"
    assert normalize_arabic("نموذج pca_spe للضاغط") == "نموذج pca_spe للضاغط"


# ============================================================================
# 2. Tokenization & Content Token Extraction
# ============================================================================


def test_arabic_pure_query_tokenization() -> None:
    tokens = extract_content_tokens("ما هي درجة حرارة الزيت في الضاغط؟")
    # "ما", "هي", "في" are stopwords; "درجة" -> "درجه", "حرارة" -> "حراره"
    assert tokens == ["درجه", "حراره", "الزيت", "الضاغط"]


def test_arabic_mixed_query_tokenization() -> None:
    tokens = extract_content_tokens("فحص air leak في صمام COMP_1 و TP3")
    # "في", "و" are stopwords
    assert tokens == ["فحص", "air", "leak", "صمام", "comp_1", "tp3"]


def test_english_query_tokenization_regression() -> None:
    tokens = extract_content_tokens("What is the maximum oil temperature for compressor TP3?")
    assert tokens == ["maximum", "oil", "temperature", "compressor", "tp3"]


def test_arabic_domain_polarity_tokens_preserved() -> None:
    # Polarity tokens like "لا" (not) and "غير" (un-) must NOT be stripped as stopwords
    tokens = extract_content_tokens("حالة غير طبيعية لا تعمل")
    assert "غير" in tokens
    assert "لا" in tokens


# ============================================================================
# 3. Arabic BM25 & Hybrid Retrieval Tests (Synthetic Chunks)
# ============================================================================


@pytest.fixture
def synthetic_multilingual_corpus() -> list[KnowledgeDocumentChunk]:
    return [
        KnowledgeDocumentChunk(
            chunk_id="ar-doc-1",
            doc_id="maintenance-manual-ar",
            title="دليل صيانة ضواغط الهواء",
            section="تسريب الهواء المضغوط",
            asset_id="metropt3-apu-1",
            tenant_scope="acme",
            source_path="ar_manual.md",
            license="Internal",
            provenance_origin="observed",
            text="انخفاض ضغط الهواء في خزان TP3 يدل على وجود تسريب هوائي في شبكة الصمامات الهوائية.",
        ),
        KnowledgeDocumentChunk(
            chunk_id="ar-doc-2",
            doc_id="thermal-manual-ar",
            title="دليل الإدارة الحرارية",
            section="حرارة الزيت والمحرك",
            asset_id="metropt3-apu-1",
            tenant_scope="acme",
            source_path="ar_thermal.md",
            license="Internal",
            provenance_origin="observed",
            text="ارتفاع درجة حرارة الزيت فوق الحد الطبيعي يتطلب فحص مراوح التبريد وتدفق السائل.",
        ),
        KnowledgeDocumentChunk(
            chunk_id="mixed-doc-3",
            doc_id="telemetry-manual-bilingual",
            title="Bilingual Telemetry Guide",
            section="صمام DV_eletric و COMP",
            asset_id="metropt3-apu-1",
            tenant_scope="acme",
            source_path="bilingual.md",
            license="Internal",
            provenance_origin="observed",
            text="Electrical solenoid valve DV_eletric controls intake pressure switch ومفتاح ضغط الهواء.",
        ),
        KnowledgeDocumentChunk(
            chunk_id="en-doc-4",
            doc_id="metro-en-note",
            title="MetroPT-3 English Reference Note",
            section="Unloaded TP3 Pressure Decay",
            asset_id="metropt3-apu-1",
            tenant_scope="acme",
            source_path="en_note.md",
            license="MIT",
            provenance_origin="observed",
            text="Rapid unloaded TP3 pressure decay indicates pneumatic air leakage across main reservoir.",
        ),
    ]


def test_arabic_bm25_retrieval(synthetic_multilingual_corpus: list[KnowledgeDocumentChunk]) -> None:
    index = HybridRagIndex(synthetic_multilingual_corpus)
    resp = index.search(
        "تسريب ضغط الهواء في TP3",
        tenant_id="acme",
        allowed_asset_ids={"metropt3-apu-1"},
        top_k=3,
        mode="bm25_only",
    )
    assert len(resp.items) >= 1
    top_hit = resp.items[0]
    assert top_hit.chunk_id == "ar-doc-1"
    assert top_hit.bm25_score > 0.0
    assert "تسريب" in top_hit.snippet or "ضغط" in top_hit.snippet


def test_arabic_thermal_query_retrieval(
    synthetic_multilingual_corpus: list[KnowledgeDocumentChunk],
) -> None:
    index = HybridRagIndex(synthetic_multilingual_corpus)
    resp = index.search(
        "درجة حرارة الزيت ومراوح التبريد",
        tenant_id="acme",
        allowed_asset_ids={"metropt3-apu-1"},
        top_k=3,
        mode="hybrid_rrf",
    )
    assert len(resp.items) >= 1
    assert resp.items[0].chunk_id == "ar-doc-2"
    assert resp.items[0].rrf_score > 0.0


def test_mixed_arabic_english_query_retrieval(
    synthetic_multilingual_corpus: list[KnowledgeDocumentChunk],
) -> None:
    index = HybridRagIndex(synthetic_multilingual_corpus)
    resp = index.search(
        "solenoid valve DV_eletric مفتاح ضغط",
        tenant_id="acme",
        allowed_asset_ids={"metropt3-apu-1"},
        top_k=3,
        mode="hybrid_rrf",
    )
    assert len(resp.items) >= 1
    assert resp.items[0].chunk_id == "mixed-doc-3"


def test_all_search_modes_support_arabic(
    synthetic_multilingual_corpus: list[KnowledgeDocumentChunk],
) -> None:
    index = HybridRagIndex(synthetic_multilingual_corpus)
    query = "تسريب الهواء المضغوط"
    for mode in ("bm25_only", "dense_svd_only", "hybrid_rrf"):
        resp = index.search(
            query,
            tenant_id="acme",
            allowed_asset_ids={"metropt3-apu-1"},
            top_k=3,
            mode=mode,  # type: ignore[arg-type]
        )
        assert len(resp.items) >= 1, f"Failed for mode={mode}"
        assert resp.items[0].chunk_id == "ar-doc-1"


# ============================================================================
# 4. Out-of-Scope Query Rejection (Relevance Gating)
# ============================================================================


def test_arabic_out_of_scope_query_rejection(
    synthetic_multilingual_corpus: list[KnowledgeDocumentChunk],
) -> None:
    index = HybridRagIndex(synthetic_multilingual_corpus)
    oos_queries = (
        "ما هي عاصمة فرنسا؟",
        "طريقة عمل كعكة الشوكولاتة في المنزل",
        "من فاز بكأس العالم لكرة القدم عام 2022؟",
        "كم سعر سهم شركة أبل اليوم؟",
        "خوارزمية البحث الثنائي بلغة بايثون",
    )
    for q in oos_queries:
        for mode in ("bm25_only", "dense_svd_only", "hybrid_rrf"):
            resp = index.search(
                q,
                tenant_id="acme",
                allowed_asset_ids={"metropt3-apu-1"},
                top_k=5,
                mode=mode,  # type: ignore[arg-type]
            )
            assert resp.items == [], f"OOS query {q!r} was not rejected in mode={mode}"
            assert "abstained" in resp.safety_note.lower()


# ============================================================================
# 5. Tenant & Asset Isolation with Arabic Content
# ============================================================================


def test_arabic_cross_tenant_isolation() -> None:
    acme_chunk = KnowledgeDocumentChunk(
        chunk_id="acme-ar-1",
        doc_id="acme-doc",
        title="تقرير صيانة أكمي",
        section="تسريب",
        asset_id="metropt3-apu-1",
        tenant_scope="acme",
        source_path="acme.md",
        license="Internal",
        provenance_origin="observed",
        text="تسريب هواء أكمي السري رمز_خاص_٩٩٩",
    )
    beta_chunk = KnowledgeDocumentChunk(
        chunk_id="beta-ar-1",
        doc_id="beta-doc",
        title="تقرير صيانة بيتا",
        section="سري",
        asset_id="metropt3-apu-1",
        tenant_scope="beta",
        source_path="beta.md",
        license="Internal",
        provenance_origin="observed",
        text="بيانات بيتا السرية جدا رمز_سري_٨٨٨ توربينات",
    )
    index = HybridRagIndex([acme_chunk, beta_chunk])

    # ACME tenant must not see Beta chunk even when querying Beta-specific Arabic tokens
    resp_acme = index.search(
        "رمز_سري_888 توربينات بيتا",
        tenant_id="acme",
        allowed_asset_ids={"metropt3-apu-1"},
        top_k=5,
        mode="hybrid_rrf",
    )
    assert resp_acme.items == []
    assert resp_acme.accessible_chunks == 1

    # Beta tenant sees their chunk
    resp_beta = index.search(
        "رمز_سري_888 توربينات بيتا",
        tenant_id="beta",
        allowed_asset_ids={"metropt3-apu-1"},
        top_k=5,
        mode="hybrid_rrf",
    )
    assert len(resp_beta.items) == 1
    assert resp_beta.items[0].chunk_id == "beta-ar-1"

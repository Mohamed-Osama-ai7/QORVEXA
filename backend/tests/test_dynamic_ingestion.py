"""Comprehensive test suite for Task 5: Dynamic Ingestion & Incremental Indexing."""

from __future__ import annotations

import concurrent.futures

import pytest

from qorvexa.contracts.knowledge import KnowledgeDocumentChunk
from qorvexa.knowledge.corpus import chunk_text_document
from qorvexa.knowledge.embeddings import MockNeuralEmbedder
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
        source_path="test/dynamic.txt",
        license="Proprietary (Test)",
        provenance_origin="observed",
        text=text,
    )


def test_chunk_text_document_english_and_markdown() -> None:
    raw = (
        "## Section One\n"
        "Air compressor temperature is within the nominal envelope and operating safely.\n\n"
        "## Section Two\n"
        "Pressure sensor TP3 indicates potential valve leakage downstream during loaded state."
    )
    chunks = chunk_text_document(
        raw,
        doc_id="report-01",
        title="Diagnostic Report",
        asset_id="metropt3-apu-1",
        tenant_scope="tenant-alpha",
    )
    assert len(chunks) == 2
    assert chunks[0].chunk_id == "report-01:sec-0"
    assert chunks[0].section == "Section One"
    assert "nominal envelope" in chunks[0].text
    assert chunks[0].tenant_scope == "tenant-alpha"

    assert chunks[1].chunk_id == "report-01:sec-1"
    assert chunks[1].section == "Section Two"
    assert "TP3" in chunks[1].text


def test_chunk_text_document_arabic() -> None:
    raw = (
        "## تقرير فحص الضاغط\n"
        "انخفاض ضغط لوحة المفاتيح وفحص صمام تصريف المياه وضغط الهواء المضغوط.\n\n"
        "## اجراءات الصيانة\n"
        "استبدال مانع التسرب واعادة ضبط مستشعر الضغط والحرارة للمحرك."
    )
    chunks = chunk_text_document(
        raw,
        doc_id="doc-arabic-01",
        title="تقرير صيانة",
        asset_id="metropt3-apu-1",
        tenant_scope="tenant-alpha",
    )
    assert len(chunks) == 2
    assert chunks[0].chunk_id == "doc-arabic-01:sec-0"
    assert chunks[0].section == "تقرير فحص الضاغط"
    assert "انخفاض ضغط" in chunks[0].text
    assert chunks[1].section == "اجراءات الصيانة"


def test_add_chunks_and_immediate_bm25_retrieval() -> None:
    base_chunk = _make_chunk("base-1", "Hydraulic fluid specifications and operational envelope.")
    index = HybridRagIndex([base_chunk])

    # Initial search
    res1 = index.search(
        "fluid specifications",
        tenant_id="tenant-alpha",
        allowed_asset_ids={"metropt3-apu-1"},
    )
    assert len(res1.items) == 1
    assert res1.items[0].chunk_id == "base-1"

    # Add new chunk dynamically
    new_chunk = _make_chunk("dyn-1", "Vibration sensor diagnostic and bearing fault frequencies.")
    added = index.add_chunks([new_chunk])
    assert added == 1

    # Search for newly added content
    res2 = index.search(
        "bearing fault frequencies",
        tenant_id="tenant-alpha",
        allowed_asset_ids={"metropt3-apu-1"},
    )
    assert len(res2.items) == 1
    assert res2.items[0].chunk_id == "dyn-1"
    assert "bearing" in res2.items[0].snippet


def test_add_chunks_duplicate_rejection() -> None:
    c1 = _make_chunk("c-1", "First chunk text.")
    index = HybridRagIndex([c1])

    # 1. Attempt adding existing chunk_id -> Must fail with ValueError
    dup = _make_chunk("c-1", "Duplicate chunk text.")
    with pytest.raises(ValueError, match="already exists"):
        index.add_chunks([dup])

    # 2. Attempt adding batch with internal duplicates -> Must fail
    c2 = _make_chunk("c-2", "Text 2")
    c3 = _make_chunk("c-2", "Text 3 duplicate")
    with pytest.raises(ValueError, match="Duplicate chunk_id 'c-2'"):
        index.add_chunks([c2, c3])

    # Original index should remain pristine
    assert len(index.chunks) == 1


def test_upsert_chunks_deterministic_replacement() -> None:
    c1 = _make_chunk("c-1", "Initial text before update.")
    c2 = _make_chunk("c-2", "Unchanged second chunk.")
    index = HybridRagIndex([c1, c2])

    # Upsert: updates c-1 and inserts c-3
    c1_updated = _make_chunk("c-1", "Updated new diagnostic content for compressor.")
    c3_new = _make_chunk("c-3", "Brand new third chunk.")

    upserted = index.upsert_chunks([c1_updated, c3_new])
    assert upserted == 2
    assert len(index.chunks) == 3

    # Check that c-1 was updated in-place and retrievable
    res = index.search(
        "compressor diagnostic content",
        tenant_id="tenant-alpha",
        allowed_asset_ids={"metropt3-apu-1"},
    )
    assert len(res.items) >= 1
    assert res.items[0].chunk_id == "c-1"
    assert "Updated new diagnostic" in res.items[0].snippet


def test_delete_chunks_and_delete_document_scoped() -> None:
    c1 = _make_chunk("c-1", "Text doc A chunk 1", doc_id="doc-A", tenant_scope="tenant-alpha")
    c2 = _make_chunk("c-2", "Text doc A chunk 2", doc_id="doc-A", tenant_scope="tenant-alpha")
    c3 = _make_chunk("c-3", "Text doc B chunk 1", doc_id="doc-B", tenant_scope="tenant-alpha")
    index = HybridRagIndex([c1, c2, c3])

    # Delete single chunk c-1
    deleted = index.delete_chunks(["c-1"], tenant_id="tenant-alpha")
    assert deleted == 1
    assert len(index.chunks) == 2
    assert {c.chunk_id for c in index.chunks} == {"c-2", "c-3"}

    # Delete entire document doc-B
    deleted_doc = index.delete_document("doc-B", tenant_id="tenant-alpha")
    assert deleted_doc == 1
    assert len(index.chunks) == 1
    assert index.chunks[0].chunk_id == "c-2"


def test_tenant_and_asset_isolation_on_mutations() -> None:
    # Chunk owned by tenant-beta
    c_beta = _make_chunk("c-beta", "Confidential beta data", tenant_scope="tenant-beta")
    c_global = _make_chunk("c-global", "Global reference data", tenant_scope=None)
    index = HybridRagIndex([c_beta, c_global])

    # 1. tenant-alpha attempts to overwrite c-beta -> PermissionError
    c_attack = _make_chunk("c-beta", "Overwritten by alpha", tenant_scope="tenant-alpha")
    with pytest.raises(PermissionError, match="Cannot overwrite chunk 'c-beta'"):
        index.upsert_chunks([c_attack])

    # 2. tenant-alpha attempts to delete c-beta -> PermissionError
    with pytest.raises(PermissionError, match="cannot delete chunk 'c-beta'"):
        index.delete_chunks(["c-beta"], tenant_id="tenant-alpha")

    # 3. tenant-alpha attempts to delete global chunk -> PermissionError
    with pytest.raises(PermissionError, match="cannot delete global chunk"):
        index.delete_chunks(["c-global"], tenant_id="tenant-alpha")

    # 4. Empty tenant_id on delete -> ValueError
    with pytest.raises(ValueError, match="tenant_id must be provided"):
        index.delete_chunks(["c-beta"], tenant_id="")


def test_partition_cache_invalidation_and_preservation() -> None:
    c_alpha_1 = _make_chunk(
        "c-a1", "Alpha asset 1 data", asset_id="asset-1", tenant_scope="tenant-alpha"
    )
    c_beta_2 = _make_chunk(
        "c-b2", "Beta asset 2 data", asset_id="asset-2", tenant_scope="tenant-beta"
    )
    index = HybridRagIndex([c_alpha_1, c_beta_2])

    # Populate cache for both partitions
    index.search("Alpha asset", tenant_id="tenant-alpha", allowed_asset_ids={"asset-1"})
    index.search("Beta asset", tenant_id="tenant-beta", allowed_asset_ids={"asset-2"})

    alpha_key = ("tenant-alpha", frozenset(["asset-1"]))
    beta_key = ("tenant-beta", frozenset(["asset-2"]))

    assert alpha_key in index._partitions
    assert beta_key in index._partitions
    cached_beta_partition = index._partitions[beta_key]

    # Mutate only asset-1 in tenant-alpha
    c_alpha_1_new = _make_chunk(
        "c-a1-new", "Alpha asset 1 new", asset_id="asset-1", tenant_scope="tenant-alpha"
    )
    index.add_chunks([c_alpha_1_new])

    # alpha_key must be invalidated; beta_key must be preserved untouched
    assert alpha_key not in index._partitions
    assert beta_key in index._partitions
    assert index._partitions[beta_key] is cached_beta_partition


def test_dynamic_ingestion_with_neural_embeddings_and_reranking() -> None:
    embedder = MockNeuralEmbedder()
    reranker = MockCrossEncoderReranker()

    c1 = _make_chunk("c-1", "Compressor motor current diagnostic and envelope.")
    index = HybridRagIndex([c1], embedder=embedder, reranker=reranker)

    # Initial search with neural + reranker
    res1 = index.search(
        "motor current",
        tenant_id="tenant-alpha",
        allowed_asset_ids={"metropt3-apu-1"},
        mode="hybrid_rrf",
        rerank=True,
    )
    assert len(res1.items) == 1
    assert res1.items[0].rerank_score is not None

    # Ingest new chunk dynamically
    c2 = _make_chunk("c-2", "Radiator fan thermal overheating inspection.")
    index.add_chunks([c2])

    # Query targeting new chunk
    res2 = index.search(
        "fan thermal overheating",
        tenant_id="tenant-alpha",
        allowed_asset_ids={"metropt3-apu-1"},
        mode="hybrid_rrf",
        rerank=True,
    )
    assert len(res2.items) >= 1
    assert res2.items[0].chunk_id == "c-2"
    assert res2.items[0].rerank_score is not None
    assert res2.items[0].semantic_score > 0.0


def test_dynamic_ingestion_with_graph_augmented_retrieval() -> None:
    kg = IndustrialKnowledgeGraph()
    c1 = _make_chunk(
        "c-sensor",
        "Sensor TP3 pneumatic pressure transmitter readings.",
        asset_id="metropt3-apu-1",
    )
    index = HybridRagIndex([c1], kg=kg)

    # Search with graph_augmented=True
    res = index.search(
        "TP3 pneumatic",
        tenant_id="tenant-alpha",
        allowed_asset_ids={"metropt3-apu-1"},
        graph_augmented=True,
    )
    assert len(res.items) == 1
    assert res.items[0].chunk_id == "c-sensor"
    assert "sensor:TP3" in res.items[0].linked_node_ids
    assert res.items[0].graph_score is not None

    # Dynamically add failure mode chunk linking to KG
    c2 = _make_chunk(
        "c-failure",
        "Air leakage in pneumatic line causing oil leak and pressure drop.",
        asset_id="metropt3-apu-1",
    )
    index.add_chunks([c2])

    res2 = index.search(
        "Air leakage",
        tenant_id="tenant-alpha",
        allowed_asset_ids={"metropt3-apu-1"},
        graph_augmented=True,
    )
    assert len(res2.items) >= 1
    assert any("mode:air_leakage" in hit.linked_node_ids for hit in res2.items)


def test_atomic_rollback_on_invalid_chunk() -> None:
    c1 = _make_chunk("c-1", "Valid initial chunk.")
    index = HybridRagIndex([c1])

    # Pass list with invalid object
    with pytest.raises(TypeError, match="Expected KnowledgeDocumentChunk"):
        index.add_chunks([_make_chunk("c-2", "Valid"), "invalid string object"])  # type: ignore[list-item]

    # Index must remain pristine (c-2 was not added)
    assert len(index.chunks) == 1
    assert index.chunks[0].chunk_id == "c-1"


def test_concurrent_mutations_and_search_thread_safety() -> None:
    base_chunks = [_make_chunk(f"base-{i}", f"Initial chunk text number {i}") for i in range(10)]
    index = HybridRagIndex(base_chunks)

    def reader(worker_id: int) -> int:
        hits_count = 0
        for _ in range(20):
            res = index.search(
                "chunk text",
                tenant_id="tenant-alpha",
                allowed_asset_ids={"metropt3-apu-1"},
            )
            hits_count += len(res.items)
        return hits_count

    def writer(worker_id: int) -> int:
        for i in range(10):
            new_c = _make_chunk(
                f"w{worker_id}-{i}",
                f"Dynamically added concurrent text from worker {worker_id} iteration {i}",
            )
            index.add_chunks([new_c])
        return 10

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        read_futures = [executor.submit(reader, i) for i in range(3)]
        write_futures = [executor.submit(writer, i) for i in range(3)]

        for f in concurrent.futures.as_completed(read_futures + write_futures):
            f.result()

    # Verify that all 30 dynamically added chunks are present and unique
    assert len(index.chunks) == 10 + 30
    assert len({c.chunk_id for c in index.chunks}) == 40

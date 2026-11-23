"""Task 03B: retrieval v1 runner: explicit chunk ids, stale labels, tokenizer gate, corpus hash."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from qorvexa.contracts.knowledge import KnowledgeDocumentChunk
from qorvexa.evaluation.__main__ import main
from qorvexa.evaluation.jsonl import load_cases
from qorvexa.evaluation.retrieval import (
    RetrievalSystem,
    corpus_fingerprint,
    diagnose_arabic_tokenization,
    run_retrieval_case,
)
from qorvexa.evaluation.schemas import RetrievalCase
from qorvexa.knowledge.rag import HybridRagIndex, extract_content_tokens
from tests.test_evaluation_harness import BENCH
from tests.test_evaluation_splits import ret_v1

CHUNKS = {"doc-a#1": "air leak", "doc-a#2": "oil temp", "doc-a#3": "duty rule", "doc-b#1": "other"}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def system(ranked: list[object], *, tokenize: bool = True) -> RetrievalSystem:
    return RetrievalSystem(
        name="fake",
        search=lambda q, a, k: ranked,
        corpus=lambda a: set(CHUNKS),
        tokenize=(lambda q: q.split()) if tokenize else (lambda q: []),
        chunk_sha256=lambda a, cid: sha(CHUNKS[cid]) if cid in CHUNKS else None,
    )


def case(**kw: Any) -> RetrievalCase:
    return RetrievalCase.model_validate(ret_v1(**kw))


def test_explicit_source_chunk_ids_define_relevance() -> None:
    c = case(
        relevant_chunk_prefixes=[],
        source_chunk_ids=["doc-a#1", "doc-a#2"],
        source_chunk_sha256={"doc-a#1": sha("air leak")},
    )
    res = run_retrieval_case(c, system(["doc-a#2", "doc-b#1"]))
    assert res.status == "scored" and res.details["relevant_in_corpus"] == 2
    assert res.metrics["hit_rate_at_5"].value == 1.0
    assert res.metrics["recall_at_5"].value == 0.5
    assert res.metrics["reciprocal_rank_at_10"].value == 1.0
    assert res.split == "development" and res.details["language"] == "en"


def test_explicit_ids_union_with_prefixes() -> None:
    c = case(relevant_chunk_prefixes=["doc-b"], source_chunk_ids=["doc-a#1"])
    res = run_retrieval_case(c, system(["doc-b#1"]))
    assert res.details["relevant_in_corpus"] == 2 and res.metrics["recall_at_5"].value == 0.5


def test_missing_source_chunk_is_a_stale_label_not_a_zero() -> None:
    c = case(relevant_chunk_prefixes=[], source_chunk_ids=["doc-a#1", "doc-z#9"])
    res = run_retrieval_case(c, system(["doc-a#1"]))
    assert res.status == "stale_label" and "doc-z#9: not in corpus" in res.errors[0]
    assert {m.state for m in res.metrics.values()} == {"unavailable"}


def test_changed_chunk_text_is_a_stale_label() -> None:
    c = case(
        relevant_chunk_prefixes=[],
        source_chunk_ids=["doc-a#1"],
        source_chunk_sha256={"doc-a#1": sha("the text the label was written against")},
    )
    res = run_retrieval_case(c, system(["doc-a#1"]))
    assert res.status == "stale_label" and "chunk text changed" in res.errors[0]
    assert all(m.value is None for m in res.metrics.values())


def test_unrepresentable_query_is_unsupported_not_zero_recall() -> None:
    arabic = case(language="ar", query="ضغط الهواء في الضاغط")
    res = run_retrieval_case(arabic, system([], tokenize=False))
    assert res.status == "unsupported_language" and res.details["language"] == "ar"
    assert {m.state for m in res.metrics.values()} == {"unavailable"}
    assert res.metrics["recall_at_5"].value is None


def test_unrepresentable_out_of_scope_probe_is_not_a_false_abstention_success() -> None:
    probe = case(
        provenance="out_of_scope_probe",
        relevant_chunk_prefixes=[],
        language="ar",
        query="ما هي عاصمة فرنسا",
    )
    res = run_retrieval_case(probe, system([], tokenize=False))
    assert res.status == "unsupported_language"
    assert res.metrics["out_of_scope_abstention"].state == "unavailable"
    english = case(
        provenance="out_of_scope_probe", relevant_chunk_prefixes=[], query="capital of france"
    )
    ok = run_retrieval_case(english, system([]))
    assert ok.metrics["out_of_scope_abstention"].value == 1.0


def test_tokenizer_diagnostic_arabic_is_currently_unsupported() -> None:
    """Documents a limitation: the retrieval tokenizer drops Arabic characters entirely. If this
    starts failing the tokenizer changed: re-audit the gate and the Arabic claim before relying
    on Arabic scores."""
    assert extract_content_tokens("ضغط الهواء في الضاغط") == []
    assert extract_content_tokens("air pressure ضغط") == ["air", "pressure"]


def _index(texts: dict[str, str]) -> HybridRagIndex:
    return HybridRagIndex(
        [
            KnowledgeDocumentChunk(
                chunk_id=cid,
                doc_id=cid.split("#")[0],
                title="t",
                section="s",
                asset_id="a",
                source_path="p",
                license="internal",
                text=text,
            )
            for cid, text in texts.items()
        ]
    )


def test_corpus_fingerprint_is_deterministic_and_sensitive() -> None:
    base = _index(CHUNKS)
    assert corpus_fingerprint(base) == corpus_fingerprint(_index(dict(reversed(CHUNKS.items()))))
    changed = _index({**CHUNKS, "doc-a#1": "air leak!"})
    removed = _index({k: v for k, v in CHUNKS.items() if k != "doc-b#1"})
    assert len({corpus_fingerprint(i) for i in (base, changed, removed)}) == 3


def test_retrieval_cli_on_v1_starter_gates_arabic_and_writes_split_summary(tmp_path: Path) -> None:
    out = tmp_path / "ret"
    cases = BENCH / "retrieval_cases.v1.jsonl"
    argv = ["retrieval", "--cases", str(cases), "--out", str(out), "--raw-dir", str(tmp_path / "x")]
    assert main(argv) == 0
    rows = [json.loads(x) for x in (out / "results.jsonl").read_text("utf-8").splitlines()]
    arabic = [r for r in rows if r["details"].get("language") == "ar"]
    assert all(r["status"] == "unsupported_language" for r in arabic)
    summary = json.loads((out / "summary.json").read_text("utf-8"))
    assert summary["summary_schema"] == "summary/v1" and "development" in summary["by_split"]
    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    assert len(manifest["corpus"]["fingerprint_sha256"]) == 64
    declared = {c.case_id: c for c in load_cases(cases, RetrievalCase)}
    assert {r["case_id"] for r in rows} == set(declared)


def test_retrieval_final_mode_is_refused_without_test_cases(tmp_path: Path) -> None:
    argv = [
        "retrieval",
        "--cases",
        str(BENCH / "retrieval_cases.v1.jsonl"),
        "--out",
        str(tmp_path / "o"),
        "--final",
    ]
    assert main(argv) == 4
    assert not (tmp_path / "o").exists()


def test_diagnose_arabic_tokenization_detects_orthography_and_tokenizer_limits() -> None:
    # 1. Pure English text: supported, no Arabic
    en_diag = diagnose_arabic_tokenization("air pressure compressor unloaded decay")
    assert en_diag.has_arabic is False
    assert en_diag.is_supported is True
    assert len(en_diag.issues) == 0

    # 2. Pure Arabic text with default tokenizer: drops Arabic
    ar_diag = diagnose_arabic_tokenization("ضغط هواء الضاغط")
    assert ar_diag.has_arabic is True
    assert ar_diag.arabic_char_count > 0
    assert ar_diag.tokens == []
    assert ar_diag.is_supported is False
    assert any("ASCII" in issue for issue in ar_diag.issues)

    # 3. Arabic with tashkeel, tatweel, and hamza variants
    complex_ar = "الـضَّــاغِطُ الأَوَّل"
    c_diag = diagnose_arabic_tokenization(complex_ar)
    assert c_diag.has_diacritics is True
    assert c_diag.has_tatweel is True
    assert c_diag.has_alif_variants is True
    assert any("tashkeel" in issue for issue in c_diag.issues)
    assert any("tatweel" in issue for issue in c_diag.issues)
    assert any("Alif" in issue for issue in c_diag.issues)

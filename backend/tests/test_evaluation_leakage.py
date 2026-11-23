"""Unit tests for evaluation leakage heuristics in leakage.py."""

from __future__ import annotations

from qorvexa.evaluation.leakage import (
    DEFAULT_TRAINING_PERIODS,
    audit_author_overlap,
    audit_event_window_overlap,
    audit_leakage,
    audit_query_document_copying,
    audit_query_duplicates,
    audit_training_window_overlap,
)
from qorvexa.evaluation.schemas import Origin, RcaCase, RetrievalCase


def _ret_case(case_id: str, query: str, split: str = "development", **kw) -> RetrievalCase:
    origin_data = kw.pop("origin", {"kind": "authored_team"})
    return RetrievalCase(
        schema_version="retrieval_case/v1",
        case_id=case_id,
        task="retrieval",
        provenance="project_document_retrieval",
        asset_id="metropt3-apu-1",
        query=query,
        language="en",
        split=split,  # type: ignore[arg-type]
        origin=Origin.model_validate(origin_data),
        cluster_id=kw.pop("cluster_id", None),
        content_origin=kw.pop("content_origin", "native"),
        relevance_source=kw.pop("relevance_source", "inherited_prefix_v0"),
        relevant_chunk_prefixes=kw.pop("relevant_chunk_prefixes", ["passport:mode-air_leakage"]),
        **kw,
    )


def _rca_case(
    case_id: str,
    start: str,
    end: str,
    split: str = "development",
    author_id: str | None = None,
) -> RcaCase:
    origin = (
        {"kind": "authored_independent", "author_id": author_id}
        if author_id
        else {"kind": "authored_team"}
    )
    return RcaCase(
        schema_version="rca_case/v1",
        case_id=case_id,
        task="rca",
        description="test event window case",
        provenance="metropt3_observed" if split == "development" else "synthetic_scenario",
        ground_truth_level="operator_report_coarse",
        ground_truth_status="partial",
        event_window={"start": start, "end": end},
        split=split,  # type: ignore[arg-type]
        origin=Origin.model_validate(origin),
        cluster_id="cluster-1",
    )


def test_audit_query_duplicates_detects_high_similarity() -> None:
    case_a = _ret_case("q1", "unloaded tp3 pressure decay slope air leakage", split="development")
    case_b = _ret_case(
        "q2",
        "unloaded tp3 pressure decay slope air leak",
        split="test",
        origin={"kind": "authored_independent", "author_id": "eng-2"},
        relevance_source="annotator_labelled",
        annotator_ids=["ann-1"],
    )
    case_c = _ret_case("q3", "completely unrelated thermal bearing failure query")

    findings = audit_query_duplicates([case_a, case_b, case_c], threshold=0.60)
    assert len(findings) == 1
    f = findings[0]
    assert f.category == "near_duplicate_query"
    assert f.severity == "critical"  # cross-split (test vs dev)
    assert f.case_id == "q1" and f.related_id == "q2"


def test_audit_query_document_copying_verbatim_and_containment() -> None:
    corpus = {
        "chunk-1": (
            "The air production unit operates with cyclical loaded and unloaded regimes. "
            "Unloaded TP3 pressure decay slope air leakage indicates downstream seal degradation."
        ),
        "chunk-2": "Routine lubrication of motor bearings is performed every six months.",
    }
    copied = _ret_case(
        "q-copy",
        "Unloaded TP3 pressure decay slope air leakage indicates downstream seal degradation.",
    )
    clean = _ret_case("q-clean", "compressor duty limit calculation")

    findings = audit_query_document_copying([copied, clean], corpus)
    assert len(findings) >= 1
    copy_finding = next(f for f in findings if f.case_id == "q-copy")
    assert copy_finding.category == "query_document_copy"
    assert copy_finding.related_id == "chunk-1"


def test_audit_author_overlap_flags_test_dev_overlap() -> None:
    dev = _ret_case(
        "dev-1",
        "dev query",
        split="development",
        origin={"kind": "authored_independent", "author_id": "author-alpha"},
    )
    test_overlap = _ret_case(
        "test-1",
        "test query",
        split="test",
        origin={"kind": "authored_independent", "author_id": "author-alpha"},
        relevance_source="annotator_labelled",
        annotator_ids=["ann-1"],
    )
    test_clean = _ret_case(
        "test-2",
        "clean test query",
        split="test",
        origin={"kind": "authored_independent", "author_id": "author-beta"},
        relevance_source="annotator_labelled",
        annotator_ids=["ann-1"],
    )

    findings = audit_author_overlap([dev, test_overlap, test_clean])
    assert len(findings) == 1
    assert findings[0].case_id == "test-1"
    assert findings[0].severity == "critical"


def test_audit_event_window_overlap() -> None:
    case_a = _rca_case("rca-1", "2020-05-01T00:00:00", "2020-05-01T12:00:00", split="development")
    case_b = _rca_case("rca-2", "2020-05-01T06:00:00", "2020-05-01T18:00:00", split="development")
    case_c = _rca_case("rca-3", "2020-05-02T00:00:00", "2020-05-02T12:00:00", split="development")

    findings = audit_event_window_overlap([case_a, case_b, case_c])
    assert len(findings) == 1
    assert findings[0].case_id == "rca-1" and findings[0].related_id == "rca-2"


def test_audit_training_window_overlap() -> None:
    # Overlaps with MetroPT-3 training window (Feb 2020)
    in_training = _rca_case(
        "rca-train", "2020-02-15T00:00:00", "2020-02-15T12:00:00", split="development"
    )
    outside_training = _rca_case(
        "rca-eval", "2020-05-15T00:00:00", "2020-05-15T12:00:00", split="development"
    )

    findings = audit_training_window_overlap(
        [in_training, outside_training], periods=DEFAULT_TRAINING_PERIODS
    )
    assert len(findings) == 1
    assert findings[0].case_id == "rca-train"
    assert findings[0].severity == "warning"


def test_audit_leakage_full_report() -> None:
    q = _ret_case("q1", "unique query")
    report = audit_leakage(retrieval_cases=[q])
    assert report.total_findings == 0
    assert report.has_critical is False
    assert "heuristic" in report.heuristics_disclaimer.lower()

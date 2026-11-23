"""Evaluation leakage audit and contamination heuristics.

Audits benchmark cases and corpus documents for potential leakage and contamination vectors:
* Exact and near-duplicate queries (Jaccard similarity >= 0.80).
* Query-to-document copying (verbatim substring or excessive token containment).
* Author overlap across evaluation splits (test vs development authors).
* RCA event-window overlap (temporal intersection between cases).
* Detector training-window overlap (event window overlapping model training periods).

Findings are heuristic risk indicators and do not constitute definitive proof of contamination.
Conversely, zero findings do not guarantee total statistical or epistemic independence.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from qorvexa.evaluation.schemas import RcaCase, RetrievalCase

LeakageCategory = Literal[
    "near_duplicate_query",
    "query_document_copy",
    "author_overlap",
    "event_window_overlap",
    "training_window_overlap",
]
Severity = Literal["critical", "warning", "info"]

_TOKEN_RE = re.compile(r"\b[a-zA-Z0-9_]+\b")

# Default known baseline detector training/calibration periods from model cards
DEFAULT_TRAINING_PERIODS: tuple[dict[str, str], ...] = (
    {
        "name": "metropt3_detector_training",
        "start": "2020-02-01T00:00:00",
        "end": "2020-03-17T23:59:59",
        "description": "MetroPT-3 Isolation Forest / PCA / rule detector fitting window",
    },
    {
        "name": "metropt3_detector_calibration",
        "start": "2020-03-18T00:00:00",
        "end": "2020-04-01T23:59:59",
        "description": "MetroPT-3 detector score calibration and threshold verification",
    },
)


class LeakageFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: LeakageCategory
    severity: Severity
    case_id: str
    related_id: str | None = None
    description: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class LeakageReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["leakage_report/v1"] = "leakage_report/v1"
    total_findings: int
    findings_by_category: dict[str, int]
    findings_by_severity: dict[str, int]
    has_critical: bool
    findings: list[LeakageFinding]
    heuristics_disclaimer: str = (
        "Findings are heuristic indicators of potential evaluation leakage and risk. "
        "They do not constitute definitive proof of contamination, and zero findings "
        "do not guarantee total independence."
    )


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _jaccard(tokens_a: set[str], tokens_b: set[str]) -> float:
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def audit_query_duplicates(
    cases: Sequence[RetrievalCase], *, threshold: float = 0.80
) -> list[LeakageFinding]:
    """Find exact or near-duplicate queries across retrieval benchmark cases."""
    findings: list[LeakageFinding] = []
    token_map = {c.case_id: _tokenize(c.query) for c in cases}

    for i, case_a in enumerate(cases):
        toks_a = token_map[case_a.case_id]
        split_a = case_a.effective_split
        for case_b in cases[i + 1 :]:
            toks_b = token_map[case_b.case_id]
            split_b = case_b.effective_split
            sim = _jaccard(toks_a, toks_b)
            if sim >= threshold:
                is_cross_split = split_a != split_b and {split_a, split_b} & {"test", "validation"}
                severity: Severity = "critical" if is_cross_split else "warning"
                findings.append(
                    LeakageFinding(
                        category="near_duplicate_query",
                        severity=severity,
                        case_id=case_a.case_id,
                        related_id=case_b.case_id,
                        description=(
                            f"Queries {case_a.case_id!r} ({split_a}) and {case_b.case_id!r} "
                            f"({split_b}) have high Jaccard similarity "
                            f"({sim:.2f} >= {threshold:.2f})"
                        ),
                        evidence={
                            "jaccard_similarity": round(sim, 4),
                            "split_a": split_a,
                            "split_b": split_b,
                            "query_a": case_a.query,
                            "query_b": case_b.query,
                        },
                    )
                )
    return findings


def audit_query_document_copying(
    cases: Sequence[RetrievalCase],
    corpus_chunks: Mapping[str, str],
    *,
    containment_threshold: float = 0.70,
    min_length: int = 30,
) -> list[LeakageFinding]:
    """Flag queries that copy verbatim spans or contain large token fractions of corpus text."""
    findings: list[LeakageFinding] = []
    for case in cases:
        query_norm = case.query.lower().strip()
        query_toks = _tokenize(case.query)
        if not query_toks:
            continue
        split = case.effective_split

        for chunk_id, chunk_text in sorted(corpus_chunks.items()):
            text_norm = chunk_text.lower()
            chunk_toks = _tokenize(chunk_text)
            if not chunk_toks:
                continue

            verbatim = len(query_norm) >= min_length and query_norm in text_norm
            tok_overlap = len(query_toks & chunk_toks) / len(query_toks)

            if verbatim or tok_overlap >= containment_threshold:
                severity: Severity = "critical" if split == "test" else "warning"
                reason = "verbatim substring" if verbatim else "high token containment"
                findings.append(
                    LeakageFinding(
                        category="query_document_copy",
                        severity=severity,
                        case_id=case.case_id,
                        related_id=chunk_id,
                        description=(
                            f"Query {case.case_id!r} ({split}) has {reason} in chunk {chunk_id!r}"
                        ),
                        evidence={
                            "split": split,
                            "verbatim": verbatim,
                            "token_containment": round(tok_overlap, 4),
                            "chunk_id": chunk_id,
                        },
                    )
                )
    return findings


def audit_author_overlap(
    cases: Sequence[RcaCase | RetrievalCase],
    *,
    dev_author_ids: Iterable[str] = (),
) -> list[LeakageFinding]:
    """Check if held-out / test cases share authors with development cases or development team."""
    findings: list[LeakageFinding] = []
    known_dev = set(dev_author_ids)

    # Collect authors declared in development split cases
    for case in cases:
        if case.effective_split == "development" and case.origin and case.origin.author_id:
            known_dev.add(case.origin.author_id)

    for case in cases:
        split = case.effective_split
        if split in ("test", "validation") and case.origin and case.origin.author_id:
            author = case.origin.author_id
            if author in known_dev:
                findings.append(
                    LeakageFinding(
                        category="author_overlap",
                        severity="critical" if split == "test" else "warning",
                        case_id=case.case_id,
                        related_id=author,
                        description=(
                            f"Case {case.case_id!r} in split {split!r} was authored by {author!r}, "
                            f"who also authored development cases or code"
                        ),
                        evidence={"author_id": author, "split": split},
                    )
                )
    return findings


def _parse_iso(ts: str | datetime) -> datetime:
    if isinstance(ts, datetime):
        return ts
    clean = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
    return datetime.fromisoformat(clean)


def audit_event_window_overlap(cases: Sequence[RcaCase]) -> list[LeakageFinding]:
    """Audit RCA cases for intersecting event windows."""
    findings: list[LeakageFinding] = []
    cases_with_window = [c for c in cases if c.event_window is not None]

    for i, case_a in enumerate(cases_with_window):
        win_a = case_a.event_window
        if win_a is None:
            continue
        start_a = _parse_iso(win_a.start)
        end_a = _parse_iso(win_a.end)
        split_a = case_a.effective_split

        for case_b in cases_with_window[i + 1 :]:
            win_b = case_b.event_window
            if win_b is None:
                continue
            start_b = _parse_iso(win_b.start)
            end_b = _parse_iso(win_b.end)
            split_b = case_b.effective_split

            # Check overlap: start_a < end_b and start_b < end_a
            if start_a < end_b and start_b < end_a:
                is_cross_split = split_a != split_b and {split_a, split_b} & {"test", "validation"}
                findings.append(
                    LeakageFinding(
                        category="event_window_overlap",
                        severity="critical" if is_cross_split else "info",
                        case_id=case_a.case_id,
                        related_id=case_b.case_id,
                        description=(
                            f"RCA case {case_a.case_id!r} ({split_a}) and {case_b.case_id!r} "
                            f"({split_b}) have overlapping event windows"
                        ),
                        evidence={
                            "window_a": [str(win_a.start), str(win_a.end)],
                            "window_b": [str(win_b.start), str(win_b.end)],
                            "split_a": split_a,
                            "split_b": split_b,
                        },
                    )
                )
    return findings


def audit_training_window_overlap(
    cases: Sequence[RcaCase],
    periods: Sequence[Mapping[str, str]] = DEFAULT_TRAINING_PERIODS,
) -> list[LeakageFinding]:
    """Audit RCA cases whose event window overlaps with detector training periods."""
    findings: list[LeakageFinding] = []
    for case in cases:
        if case.event_window is None:
            continue
        case_start = _parse_iso(case.event_window.start)
        case_end = _parse_iso(case.event_window.end)
        split = case.effective_split

        for period in periods:
            p_start = _parse_iso(period["start"])
            p_end = _parse_iso(period["end"])

            if case_start < p_end and p_start < case_end:
                p_name = period.get("name", "training_period")
                findings.append(
                    LeakageFinding(
                        category="training_window_overlap",
                        severity="critical" if split == "test" else "warning",
                        case_id=case.case_id,
                        related_id=p_name,
                        description=(
                            f"RCA case {case.case_id!r} ({split}) overlaps with detector "
                            f"training window {p_name!r}"
                        ),
                        evidence={
                            "period": dict(period),
                            "case_window": [
                                str(case.event_window.start),
                                str(case.event_window.end),
                            ],
                            "split": split,
                        },
                    )
                )
    return findings


def audit_leakage(
    *,
    rca_cases: Sequence[RcaCase] = (),
    retrieval_cases: Sequence[RetrievalCase] = (),
    corpus_chunks: Mapping[str, str] | None = None,
    training_periods: Sequence[Mapping[str, str]] = DEFAULT_TRAINING_PERIODS,
    dev_author_ids: Iterable[str] = (),
) -> LeakageReport:
    """Run all leakage audit heuristics across RCA and retrieval benchmarks."""
    findings: list[LeakageFinding] = []

    if retrieval_cases:
        findings.extend(audit_query_duplicates(retrieval_cases))
        if corpus_chunks:
            findings.extend(audit_query_document_copying(retrieval_cases, corpus_chunks))

    all_cases: list[RcaCase | RetrievalCase] = list(rca_cases) + list(retrieval_cases)
    if all_cases:
        findings.extend(audit_author_overlap(all_cases, dev_author_ids=dev_author_ids))

    if rca_cases:
        findings.extend(audit_event_window_overlap(rca_cases))
        findings.extend(audit_training_window_overlap(rca_cases, periods=training_periods))

    by_cat = Counter(f.category for f in findings)
    by_sev = Counter(f.severity for f in findings)

    return LeakageReport(
        total_findings=len(findings),
        findings_by_category=dict(sorted(by_cat.items())),
        findings_by_severity=dict(sorted(by_sev.items())),
        has_critical=by_sev.get("critical", 0) > 0,
        findings=findings,
    )

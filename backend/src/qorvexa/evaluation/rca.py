"""Score RCA predictions against RCA benchmark cases using the Task 02A metric functions.

Nothing here decides D-10 or D-11. Recommendation accuracy and HITL compliance are reported as
``unavailable`` because RCA output carries no recommendation class or approval flags, and the
recommendation-safety (R) and verification-action (V) labels are passed as unknown, so
contradiction handling is ``undecided`` unless the output abstains.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from qorvexa.anomaly.rca_eval import (
    critical_contradiction_handled,
    critical_contradictions_disclosed,
    evidence_precision,
    evidence_recall,
    safe_diagnostic_disposition,
    sanitize_surfaced_keys,
)
from qorvexa.contracts.events import EventEvidencePackage
from qorvexa.contracts.rca import RcaAssessment
from qorvexa.evaluation.jsonl import PredictionLoad
from qorvexa.evaluation.schemas import (
    CaseResult,
    MetricOutcome,
    MetricState,
    RcaCase,
    RcaPrediction,
)

RCA_METRICS: tuple[str, ...] = (
    "category_match",
    "unacceptable_hypothesis",
    "abstention_correct",
    "supporting_evidence_recall",
    "supporting_evidence_precision",
    "missing_check_recall",
    "missing_check_precision",
    "contradicting_evidence_disclosure",
    "critical_contradiction_disclosure",
    "safe_diagnostic_disposition",
    "critical_contradiction_handling",
    "recommendation_accuracy",
    "hitl_compliance",
)

_PENDING_RV = "R/V labels not provided by RCA output (pending D-10, D-11)"
_UNAVAILABLE = {
    "recommendation_accuracy": "RCA output has no recommendation class (pending D-10)",
    "hitl_compliance": "RCA output has no approval/auto-execution flags (pending D-10)",
}


def _scored(
    value: float | bool | None, *, none_state: MetricState, reason: str | None = None
) -> MetricOutcome:
    if value is None:
        return MetricOutcome(value=None, state=none_state, reason=reason)
    return MetricOutcome(value=round(float(value), 6), state="scored")


def _unlabelled() -> MetricOutcome:
    return MetricOutcome(value=None, state="unlabelled", reason="ground truth not labelled")


def _evidence_pair(
    expected: list[str] | None, surfaced: Iterable[object]
) -> tuple[MetricOutcome, MetricOutcome]:
    if expected is None:
        return _unlabelled(), _unlabelled()
    got = list(surfaced)
    return (
        _scored(evidence_recall(expected, got), none_state="not_applicable"),
        _scored(evidence_precision(expected, got), none_state="not_applicable"),
    )


def _count_discarded(*lists: Sequence[object] | None) -> int:
    return sum(
        1 for items in lists if items is not None for i in items if not sanitize_surfaced_keys([i])
    )


def score_rca_case(
    case: RcaCase, prediction: RcaPrediction | None, errors: list[str] | None = None
) -> CaseResult:
    """Score one case. A missing or invalid prediction scores nothing (``no_prediction``).
    The result carries the case's effective split and cluster for the summary."""
    result = _score_rca_case(case, prediction, errors)
    return result.model_copy(update={"split": case.effective_split, "cluster_id": case.cluster_id})


def _score_rca_case(
    case: RcaCase, prediction: RcaPrediction | None, errors: list[str] | None = None
) -> CaseResult:
    if prediction is None:
        status = "invalid_prediction" if errors else "missing_prediction"
        return CaseResult(
            case_id=case.case_id,
            task="rca",
            provenance=case.provenance,
            status=status,
            errors=list(errors or []),
            metrics={
                m: MetricOutcome(value=None, state="no_prediction", reason=status)
                for m in RCA_METRICS
            },
        )

    p = prediction
    metrics: dict[str, MetricOutcome] = {}

    if case.expected_categories is None:
        metrics["category_match"] = _unlabelled()
    else:
        metrics["category_match"] = _scored(
            p.primary_category in case.expected_categories, none_state="unlabelled"
        )
    if not case.unacceptable_categories:
        metrics["unacceptable_hypothesis"] = MetricOutcome(
            value=None, state="not_applicable", reason="no unacceptable categories listed"
        )
    else:
        metrics["unacceptable_hypothesis"] = _scored(
            (not p.abstained) and p.primary_category in case.unacceptable_categories,
            none_state="not_applicable",
        )
    if case.expected_abstain is None:
        metrics["abstention_correct"] = _unlabelled()
    else:
        metrics["abstention_correct"] = _scored(
            p.abstained == case.expected_abstain, none_state="unlabelled"
        )

    sup_r, sup_p = _evidence_pair(case.expected_supporting_evidence, p.supporting_evidence)
    miss_r, miss_p = _evidence_pair(case.expected_missing_checks, p.missing_evidence)
    con_r, _ = _evidence_pair(case.expected_contradicting_evidence, p.contradicting_evidence)
    metrics |= {
        "supporting_evidence_recall": sup_r,
        "supporting_evidence_precision": sup_p,
        "missing_check_recall": miss_r,
        "missing_check_precision": miss_p,
        "contradicting_evidence_disclosure": con_r,
    }

    critical: list[str] | None
    source: str
    if case.critical_contradictions is not None:
        critical, source = case.critical_contradictions, "case"
    elif p.package_critical_contradictions is not None:
        critical = sorted(sanitize_surfaced_keys(p.package_critical_contradictions))
        source = "evidence_package"
    else:
        critical, source = None, "unknown"

    cited = [
        *p.cited_evidence,
        *p.supporting_evidence,
        *p.contradicting_evidence,
        *p.missing_evidence,
    ]
    if critical is None:
        for m in (
            "critical_contradiction_disclosure",
            "safe_diagnostic_disposition",
            "critical_contradiction_handling",
        ):
            metrics[m] = MetricOutcome(
                value=None, state="unlabelled", reason="critical contradictions unknown"
            )
    else:
        metrics["critical_contradiction_disclosure"] = _scored(
            critical_contradictions_disclosed(critical, cited), none_state="not_applicable"
        )
        if not critical:
            disposition: bool | None = None
            dispo_state: MetricState = "not_applicable"
            dispo_reason: str | None = None
        else:
            disposition = safe_diagnostic_disposition(
                abstained=p.abstained,
                primary_category=p.primary_category,
                contradiction_verification_action=None,
                recommendation_safe=None,
            )
            dispo_state, dispo_reason = "undecided", _PENDING_RV
        metrics["safe_diagnostic_disposition"] = _scored(
            disposition, none_state=dispo_state, reason=dispo_reason
        )
        handled = critical_contradiction_handled(
            critical,
            cited,
            abstained=p.abstained,
            primary_category=p.primary_category,
            contradiction_verification_action=None,
            recommendation_safe=None,
        )
        metrics["critical_contradiction_handling"] = _scored(
            handled,
            none_state="not_applicable" if not critical else "undecided",
            reason=None if not critical else _PENDING_RV,
        )

    for name, reason in _UNAVAILABLE.items():
        metrics[name] = MetricOutcome(value=None, state="unavailable", reason=reason)

    return CaseResult(
        case_id=case.case_id,
        task="rca",
        provenance=case.provenance,
        status="scored",
        errors=list(errors or []),
        discarded_keys=_count_discarded(
            p.supporting_evidence,
            p.contradicting_evidence,
            p.missing_evidence,
            p.cited_evidence,
            p.package_critical_contradictions,
        ),
        details={
            "producer": p.producer,
            "evaluation_role": case.evaluation_role,
            "primary_category": p.primary_category,
            "abstained": p.abstained,
            "confidence_band": p.confidence_band,
            "critical_contradictions": critical,
            "critical_contradictions_source": source,
        },
        metrics={m: metrics[m] for m in RCA_METRICS},
    )


def score_rca_cases(cases: Sequence[RcaCase], load: PredictionLoad) -> list[CaseResult]:
    """Score every case in file order; one bad prediction never affects another case."""
    known = {c.case_id for c in cases}
    results = [
        score_rca_case(c, load.predictions.get(c.case_id), load.case_errors.get(c.case_id))
        for c in cases
    ]
    unknown = sorted((set(load.predictions) | set(load.case_errors)) - known)
    load.orphan_errors.extend(f"prediction for unknown case_id {cid!r}" for cid in unknown)
    return results


def prediction_from_assessment(
    case_id: str,
    rca: RcaAssessment,
    *,
    package: EventEvidencePackage | None = None,
    producer: str = "qorvexa.anomaly.rca",
) -> RcaPrediction:
    """Convert real RCA engine output into a prediction record without adding any label.

    Evidence lists come from the rank-1 hypothesis (§B.3 matching rule); ``cited_evidence`` is
    every key cited in any hypothesis; critical contradiction kinds come from the package."""
    top = rca.ranked_hypotheses[0]
    cited = sorted(
        {
            ref.signal_or_feature
            for h in rca.ranked_hypotheses
            for refs in (h.supporting_evidence, h.contradicting_evidence, h.missing_evidence)
            for ref in refs
        }
    )
    critical = (
        sorted({c.kind for c in package.quality.contradictions if c.severity == "critical"})
        if package is not None
        else None
    )
    return RcaPrediction(
        schema_version="rca_prediction/v0",
        case_id=case_id,
        producer=producer,
        abstained=rca.abstained,
        primary_category=rca.primary_category,
        confidence_band=top.confidence_band,
        supporting_evidence=[r.signal_or_feature for r in top.supporting_evidence],
        contradicting_evidence=[r.signal_or_feature for r in top.contradicting_evidence],
        missing_evidence=[r.signal_or_feature for r in top.missing_evidence],
        cited_evidence=list(cited),
        package_critical_contradictions=critical,
    )


@dataclass
class EngineRun:
    """Outcome of running the existing RCA engine over cases: one prediction or one error per
    case with an ``event_window``, plus the evidence provenance of every package built."""

    predictions: list[RcaPrediction] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)


def run_engine(service: Any, cases: Sequence[RcaCase], *, asset_id: str) -> EngineRun:
    """Run package builder + RCA engine for each case that has an ``event_window``.

    ``service`` is an ``AnomalyService`` (needs MetroPT-3 data and a model bundle). A failure
    in one case is recorded in ``errors`` and never stops the batch. Cases without a window
    are skipped (not an error: nothing can be run for them)."""
    from qorvexa.anomaly.evidence_package import build_event_evidence_package
    from qorvexa.anomaly.rca import analyze_event_evidence

    run = EngineRun()
    for case in cases:
        if case.event_window is None:
            continue
        start = pd.Timestamp(case.event_window.start)
        try:
            pkg = build_event_evidence_package(
                service,
                asset_id=asset_id,
                event_start=start,
                event_end=pd.Timestamp(case.event_window.end),
                raised_at=start,
            )
            rca = analyze_event_evidence(pkg)
        except Exception as exc:  # per-case isolation at the evaluation boundary
            run.errors[case.case_id] = f"{type(exc).__name__}: {exc}"
            continue
        run.predictions.append(prediction_from_assessment(case.case_id, rca, package=pkg))
        run.evidence.append(evidence_record(case.case_id, pkg, rca))
    return run


def evidence_record(case_id: str, pkg: EventEvidencePackage, rca: RcaAssessment) -> dict[str, Any]:
    """Compact, deterministic provenance for one evidence package (not the package itself)."""
    contradictions: dict[str, int] = {}
    for c in pkg.quality.contradictions:
        key = f"{c.kind}:{c.severity}"
        contradictions[key] = contradictions.get(key, 0) + 1
    return {
        "case_id": case_id,
        "package_id": pkg.package_id,
        "package_sha256": hashlib.sha256(pkg.model_dump_json().encode("utf-8")).hexdigest(),
        "rca_id": rca.rca_id,
        "provenance": pkg.provenance.model_dump(mode="json"),
        "quality": {
            "overall_status": pkg.quality.overall_status,
            "completeness_score": pkg.quality.completeness_score,
            "coverage_ratio": pkg.quality.coverage_ratio,
            "stale_windows_count": pkg.quality.stale_windows_count,
            "contradictions": dict(sorted(contradictions.items())),
        },
    }


def predictions_from_service(
    service: Any, cases: Sequence[RcaCase], *, asset_id: str
) -> list[RcaPrediction]:
    """Predictions only; raises ``RuntimeError`` if any case failed (use ``run_engine`` to
    keep per-case errors)."""
    run = run_engine(service, cases, asset_id=asset_id)
    if run.errors:
        raise RuntimeError(f"RCA engine failed for {sorted(run.errors)}: {run.errors}")
    return run.predictions

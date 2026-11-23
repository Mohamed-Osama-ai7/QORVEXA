"""Reproducible WP-B RCA Casebook evaluation harness.

Separates REAL MetroPT-3 labeled events and warning cases cleanly from SYNTHETIC
stress cases so synthetic integrity separation never masquerades as real failure validation.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from qorvexa.anomaly.evidence_package import build_event_evidence_package
from qorvexa.anomaly.rca import analyze_event_evidence
from qorvexa.anomaly.service import AnomalyService
from qorvexa.contracts.events import EventEvidencePackage
from qorvexa.contracts.rca import EvidenceReference, RcaAssessment, RcaHypothesisCategory

_VALID_DERIVED_REFS = frozenset(
    {
        "load_duty",
        "off_fraction",
        "load_starts",
        "inrush_samples",
        "tp3_reservoir_gap",
        "loaded_tp3_rise",
        "unloaded_tp3_slope",
        "unloaded_fraction",
        "stale_telemetry",
        "valve_contradiction",
        "motor_valve_mismatch",
        "coverage_ratio",
        "air_demand_flowmeter",
        "pre_event_baseline",
        "high_rate_electrical_current_gt_1hz",
        "recovery_or_persistence",
    }
)


@dataclass(frozen=True)
class RcaEvalCaseResult:
    case_id: str
    kind: str  # real_labeled | real_unlabeled_warning | synthetic_stress
    expected_categories: list[RcaHypothesisCategory] | None
    expect_abstain: bool | None
    primary_category: RcaHypothesisCategory
    abstained: bool
    top_support_score: float
    confidence_band: str
    plausible_alternatives: list[RcaHypothesisCategory]
    citations_grounded: bool
    correlation_disclosed: bool
    matches_expectation: bool | None
    verification_steps_count: int


def _valid_signals_in_pkg(pkg: EventEvidencePackage) -> set[str]:
    names: set[str] = set()
    for p in pkg.phases:
        names.update(s.signal for s in p.observed_signals)
        names.update(d.name for d in p.derived_features)
    names.update(c.kind for c in pkg.quality.contradictions)
    for c in pkg.quality.contradictions:
        names.update(c.signals)
    names.update(o.category for o in pkg.human_observations)
    names.update(e.feature for e in pkg.evidence_items)
    names.add(pkg.provenance.primary_detector)
    if pkg.provenance.warning_detector:
        names.add(pkg.provenance.warning_detector)
    names.update(_VALID_DERIVED_REFS)
    return names


def citations_grounded(pkg: EventEvidencePackage, rca: RcaAssessment) -> bool:
    """Verify every EvidenceReference cites an existing package symbol & matches phase bounds."""
    known = _valid_signals_in_pkg(pkg)
    phase_names = {p.phase for p in pkg.phases}
    obs_by_cat = {o.category for o in pkg.human_observations}
    for hyp in rca.ranked_hypotheses:
        for ref_list in (hyp.supporting_evidence, hyp.contradicting_evidence, hyp.missing_evidence):
            ref: EvidenceReference
            for ref in ref_list:
                if ref.phase is not None and ref.phase not in phase_names:
                    return False
                if ref.origin == "human_observation" and ref.signal_or_feature not in obs_by_cat:
                    return False
                if ref.signal_or_feature not in known:
                    return False
                if not ref.statement.strip():
                    return False
    return True


def correlation_disclosed(rca: RcaAssessment) -> bool:
    return (
        len(rca.correlation_vs_causation_boundary) > 0
        and all(bool(h.correlation_vs_causation_note.strip()) for h in rca.ranked_hypotheses)
        and "not a calibrated" in rca.score_meaning_note.lower()
    )


# Evidence keys under which the RCA engine discloses each contradiction kind. ``sensor_freeze``
# is cited as ``stale_telemetry`` (anomaly/rca.py); other kinds are cited under their own name.
CONTRADICTION_DISCLOSURE_KEYS: dict[str, frozenset[str]] = {
    "sensor_freeze": frozenset({"sensor_freeze", "stale_telemetry"}),
}


# A canonical evidence/check key after trimming and case-folding: a lowercase identifier such as
# ``load_duty`` or ``high_rate_electrical_current_gt_1hz`` (``.``, ``:`` and ``-`` also allowed).
_CANONICAL_KEY = re.compile(r"[a-z0-9_][a-z0-9_.:\-]*")


def _normalize_key(key: object) -> str | None:
    if not isinstance(key, str):
        return None
    norm = key.strip().casefold()
    return norm if _CANONICAL_KEY.fullmatch(norm) else None


def _expected_keys(keys: Iterable[str]) -> set[str]:
    """Benchmark ground-truth keys: strict. Any blank, non-string or malformed key raises
    ``ValueError`` because it is a defect in the case file. Keys are matched exactly after
    trimming and case-folding; free-text or semantic matching is not used."""
    out: set[str] = set()
    for key in keys:
        norm = _normalize_key(key)
        if norm is None:
            raise ValueError(f"evidence keys must be non-empty canonical identifiers: {key!r}")
        out.add(norm)
    return out


def _surfaced_keys(keys: Iterable[object]) -> set[str]:
    """Keys produced by the system under evaluation: lenient. Valid keys are trimmed and
    case-folded; blank, non-string or malformed keys are discarded (they can never match a
    ground-truth key), so one malformed output cannot crash an evaluation batch."""
    return {norm for key in keys if (norm := _normalize_key(key)) is not None}


def validate_expected_keys(keys: Iterable[str]) -> set[str]:
    """Public strict normaliser for benchmark ground-truth keys (raises ``ValueError``)."""
    return _expected_keys(keys)


def sanitize_surfaced_keys(keys: Iterable[object]) -> set[str]:
    """Public lenient normaliser for system-produced keys (drops malformed ones)."""
    return _surfaced_keys(keys)


def evidence_recall(expected: Iterable[str], surfaced: Iterable[object]) -> float | None:
    """|expected ∩ surfaced| / |expected| for one case.

    Used for supporting-evidence recall, missing-check disclosure and contradicting-evidence
    disclosure. Returns ``None`` (not applicable, excluded from averages) when nothing is
    expected."""
    exp = _expected_keys(expected)
    got = _surfaced_keys(surfaced)
    if not exp:
        return None
    return len(exp & got) / len(exp)


def evidence_precision(expected: Iterable[str], surfaced: Iterable[object]) -> float | None:
    """|expected ∩ surfaced| / |surfaced| for one case.

    Returns ``None`` when nothing valid is surfaced; the omission is already counted by recall.
    Discarded malformed surfaced keys do not count in the denominator."""
    exp = _expected_keys(expected)
    got = _surfaced_keys(surfaced)
    if not got:
        return None
    return len(exp & got) / len(got)


def recommendation_accurate(expected_class: str, predicted_class: str) -> bool:
    """Recommendation accuracy for one case: exact class match after trimming/case-folding.

    Every case has an expected class, so this metric is never undefined. HITL compliance is a
    separate check (``hitl_compliant``) and is not folded into accuracy."""
    expected = expected_class.strip().casefold()
    if not expected:
        raise ValueError("expected_class must be non-empty")
    return predicted_class.strip().casefold() == expected


def hitl_compliant(
    *, human_approval_required: bool | None, auto_execution_path: bool | None
) -> bool | None:
    """HITL compliance for one output: approval is *required* and no automated execution exists.

    This checks that approval is required, not that it was obtained. Returns ``None`` when either
    input is unknown; the current RCA data model carries neither flag, so callers must supply
    them from an agreed source. Evaluated over all outputs (target 100 %), independent of
    accuracy and of contradiction handling."""
    if human_approval_required is None or auto_execution_path is None:
        return None
    return human_approval_required and not auto_execution_path


# Primary categories that attribute the event to the data/instrumentation rather than to the
# machine. Under a critical contradiction they are a *basis* for a safe disposition but not proof
# of one: RCA output has no structured operational recommendation, so an unsafe recommendation
# could coexist with them. ``insufficient_evidence`` declines to diagnose. All other categories
# (air_leakage, abnormal_high_demand, concurrent_conditions) are positive mechanical diagnoses.
INSTRUMENTATION_CATEGORIES: frozenset[str] = frozenset(
    {"telemetry_inconsistency", "sensor_malfunction"}
)
NO_DIAGNOSIS_CATEGORY = "insufficient_evidence"


def critical_contradictions_disclosed(
    critical_kinds: Iterable[str], disclosed: Iterable[object]
) -> bool | None:
    """Contradiction disclosure: every critical contradiction kind is cited.

    ``critical_kinds`` are the ``ContradictionRecord.kind`` values with ``severity="critical"``
    in the evidence package; ``disclosed`` are the ``signal_or_feature`` keys cited anywhere in
    the RCA assessment. Partial disclosure fails. Returns ``None`` when there is no critical
    contradiction."""
    critical = _expected_keys(critical_kinds)
    if not critical:
        return None
    cited = _surfaced_keys(disclosed)
    return all(
        cited & CONTRADICTION_DISCLOSURE_KEYS.get(kind, frozenset({kind})) for kind in critical
    )


def safe_diagnostic_disposition(
    *,
    abstained: bool | None,
    primary_category: str | None,
    contradiction_verification_action: bool | None,
    recommendation_safe: bool | None = None,
) -> bool | None:
    """Safe disposition of an output that faces a critical contradiction.

    * Safe: the output abstains, or its primary category is ``insufficient_evidence``.
    * Otherwise safe only if ``recommendation_safe`` is ``True`` **and** there is a basis: the
      primary category is ``telemetry_inconsistency`` / ``sensor_malfunction``, or a documented,
      contradiction-directed verification action is present.
    * Unsafe: ``recommendation_safe`` is ``False``, or a mechanical diagnosis with
      ``contradiction_verification_action=False``. The confidence band is not an input.
    * ``None`` (undecided) whenever the structured inputs cannot establish either outcome; a
      category alone never establishes safety.

    ``recommendation_safe`` and ``contradiction_verification_action`` are structured labels that
    the RCA output does not carry today (no recommendation field; ``VerificationStep`` has only
    free text and no link to a contradiction). They must come from an agreed mapping
    (D-10, D-11); nothing is inferred from text."""
    if abstained is True:
        return True
    category = primary_category.strip().casefold() if primary_category is not None else None
    if category == NO_DIAGNOSIS_CATEGORY:
        return True
    if abstained is None:
        return None  # an abstention would make it safe, so it cannot be ruled out
    if recommendation_safe is False:
        return False
    mechanical = category is not None and category not in INSTRUMENTATION_CATEGORIES
    if mechanical and contradiction_verification_action is False:
        return False
    if category is None:
        return None
    has_basis = category in INSTRUMENTATION_CATEGORIES or contradiction_verification_action is True
    if not has_basis:
        return None
    return True if recommendation_safe is True else None


def critical_contradiction_handled(
    critical_kinds: Iterable[str],
    disclosed: Iterable[str],
    *,
    abstained: bool | None,
    primary_category: str | None,
    contradiction_verification_action: bool | None,
    recommendation_safe: bool | None = None,
) -> bool | None:
    """Contradiction handling: full disclosure **and** a safe diagnostic disposition.

    Disclosure alone never passes. Returns ``None`` when there is no critical contradiction, or
    when everything is disclosed but the disposition cannot be decided from the inputs. A hidden
    or partially disclosed contradiction fails regardless of the disposition."""
    disclosed_all = critical_contradictions_disclosed(critical_kinds, disclosed)
    if disclosed_all is None:
        return None
    if not disclosed_all:
        return False
    return safe_diagnostic_disposition(
        abstained=abstained,
        primary_category=primary_category,
        contradiction_verification_action=contradiction_verification_action,
        recommendation_safe=recommendation_safe,
    )


def evaluate_rca_case(
    case_id: str,
    pkg: EventEvidencePackage,
    *,
    kind: str,
    expected_categories: Sequence[RcaHypothesisCategory] | None = None,
    expect_abstain: bool | None = None,
) -> tuple[RcaEvalCaseResult, RcaAssessment]:
    rca = analyze_event_evidence(pkg)
    grounded = citations_grounded(pkg, rca)
    disclosed = correlation_disclosed(rca)
    alts = [
        h.category
        for h in rca.ranked_hypotheses[1:]
        if h.relative_support == "plausible_alternative"
    ]
    top = rca.ranked_hypotheses[0]

    matches: bool | None = None
    if expected_categories is not None or expect_abstain is not None:
        cat_ok = (
            True if expected_categories is None else rca.primary_category in expected_categories
        )
        abs_ok = True if expect_abstain is None else rca.abstained == expect_abstain
        matches = cat_ok and abs_ok

    return (
        RcaEvalCaseResult(
            case_id=case_id,
            kind=kind,
            expected_categories=list(expected_categories) if expected_categories else None,
            expect_abstain=expect_abstain,
            primary_category=rca.primary_category,
            abstained=rca.abstained,
            top_support_score=top.support_score,
            confidence_band=top.confidence_band,
            plausible_alternatives=alts,
            citations_grounded=grounded,
            correlation_disclosed=disclosed,
            matches_expectation=matches,
            verification_steps_count=sum(len(h.verification_steps) for h in rca.ranked_hypotheses),
        ),
        rca,
    )


def evaluate_service_report_rca(
    service: AnomalyService,
    *,
    asset_id: str = "metropt3-apu-1",
) -> dict[str, Any]:
    """Run WP-B RCA Casebook evaluation across MetroPT-3 ``F1..F4`` operator reports."""
    rows: list[RcaEvalCaseResult] = []
    for report in service.reports:
        pkg = build_event_evidence_package(
            service,
            asset_id=asset_id,
            event_start=pd.Timestamp(report.start),
            event_end=pd.Timestamp(report.end),
            raised_at=pd.Timestamp(datetime.fromisoformat(str(report.start))),
        )
        # F1 includes pre-onset/early-event stale telemetry -> concurrent or leakage are both valid
        expected: list[RcaHypothesisCategory] = (
            ["air_leakage", "concurrent_conditions"]
            if pkg.quality.stale_windows_count > 0 or pkg.quality.frozen_sensors
            else ["air_leakage"]
        )
        res, _ = evaluate_rca_case(
            report.report_id,
            pkg,
            kind="real_labeled",
            expected_categories=expected,
            expect_abstain=False,
        )
        rows.append(res)

    n = len(rows)
    checked = [r for r in rows if r.matches_expectation is not None]
    return {
        "cases": n,
        "kind": "real_labeled",
        "evidence_citation_grounding_rate": round(sum(r.citations_grounded for r in rows) / n, 4)
        if n
        else None,
        "correlation_disclaimer_rate": round(sum(r.correlation_disclosed for r in rows) / n, 4)
        if n
        else None,
        "expectation_agreement_rate": round(
            sum(bool(r.matches_expectation) for r in checked) / len(checked), 4
        )
        if checked
        else None,
        "results": [asdict(r) for r in rows],
    }


def evaluate_unlabeled_warning_slice(
    service: AnomalyService,
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    asset_id: str = "metropt3-apu-1",
    max_cases: int = 5,
) -> dict[str, Any]:
    """Evaluate real unlabeled warning episodes on non-leak diagnosis & grounding."""
    replay = service.replay_details(start, end)
    rows: list[RcaEvalCaseResult] = []
    for idx, warn in enumerate(replay.warnings[:max_cases], start=1):
        pkg = build_event_evidence_package(
            service,
            asset_id=asset_id,
            event_start=warn.start,
            event_end=warn.end,
            raised_at=warn.raised_at,
        )
        expected: list[RcaHypothesisCategory] = (
            [
                "telemetry_inconsistency",
                "insufficient_evidence",
                "sensor_malfunction",
                "concurrent_conditions",
            ]
            if warn.warning_kind
            in ("stale_telemetry", "signal_inconsistency", "mixed_consistency_warning")
            else ["sensor_malfunction", "concurrent_conditions", "abnormal_high_demand"]
        )
        res, _ = evaluate_rca_case(
            f"W{idx}_{warn.warning_kind}",
            pkg,
            kind="real_unlabeled_warning",
            expected_categories=expected,
            expect_abstain=None,
        )
        rows.append(res)

    n = len(rows)
    return {
        "cases": n,
        "kind": "real_unlabeled_warning",
        "evidence_citation_grounding_rate": round(sum(r.citations_grounded for r in rows) / n, 4)
        if n
        else None,
        "correlation_disclaimer_rate": round(sum(r.correlation_disclosed for r in rows) / n, 4)
        if n
        else None,
        "non_leak_primary_rate": round(
            sum(r.primary_category != "air_leakage" or r.abstained for r in rows) / n, 4
        )
        if n
        else None,
        "results": [asdict(r) for r in rows],
    }

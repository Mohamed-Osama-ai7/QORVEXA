"""Deterministic toy tests for retrieval and diagnosis evaluation metrics (QORVEXA F-01, F-05)."""

from __future__ import annotations

import pytest

from qorvexa.anomaly.rca_eval import (
    critical_contradiction_handled,
    critical_contradictions_disclosed,
    evidence_precision,
    evidence_recall,
    hitl_compliant,
    recommendation_accurate,
    safe_diagnostic_disposition,
)
from qorvexa.knowledge.eval import (
    hit_rate_at_k,
    macro_average,
    macro_hit_rate_at_k,
    macro_recall_at_k,
    recall_at_k,
)

# --- F-01: Recall@K vs Hit Rate@K -------------------------------------------------------------


def test_multiple_relevant_all_retrieved() -> None:
    ranked = ["a", "x", "b", "c"]
    assert recall_at_k(ranked, {"a", "b", "c"}, 4) == 1.0
    assert hit_rate_at_k(ranked, {"a", "b", "c"}, 4) == 1.0


def test_partial_retrieval_separates_recall_from_hit_rate() -> None:
    ranked = ["a", "x", "y", "b", "c"]
    relevant = {"a", "b", "c"}
    assert recall_at_k(ranked, relevant, 3) == pytest.approx(1 / 3)
    assert hit_rate_at_k(ranked, relevant, 3) == 1.0
    assert recall_at_k(ranked, relevant, 1) == pytest.approx(1 / 3)
    assert recall_at_k(ranked, relevant, 5) == 1.0


def test_relevant_only_below_cutoff_scores_zero() -> None:
    ranked = ["x", "y", "a"]
    assert recall_at_k(ranked, {"a"}, 2) == 0.0
    assert hit_rate_at_k(ranked, {"a"}, 2) == 0.0


def test_empty_results_score_zero() -> None:
    assert recall_at_k([], {"a", "b"}, 5) == 0.0
    assert hit_rate_at_k([], {"a", "b"}, 5) == 0.0


def test_zero_relevant_passages_is_undefined_not_zero() -> None:
    assert recall_at_k(["a", "b"], set(), 5) is None
    assert hit_rate_at_k(["a", "b"], set(), 5) is None
    assert recall_at_k([], set(), 5) is None


def test_duplicate_ids_in_ranking_count_once() -> None:
    assert recall_at_k(["a", "a", "a"], {"a", "b"}, 3) == 0.5


def test_top_k_is_first_k_distinct_passages() -> None:
    assert recall_at_k(["a", "a", "b", "c"], {"b"}, 2) == 1.0
    assert recall_at_k(["a", "a", "b", "c"], {"c"}, 2) == 0.0


def test_k_must_be_positive() -> None:
    with pytest.raises(ValueError, match="k must be >= 1"):
        recall_at_k(["a"], {"a"}, 0)
    with pytest.raises(ValueError, match="k must be >= 1"):
        hit_rate_at_k(["a"], set(), 0)


def test_macro_average_excludes_items_without_relevant_passages() -> None:
    items = [
        (["a", "x", "y"], {"a", "b"}),  # recall@3 = 0.5, hit = 1
        (["x", "y", "z"], {"c"}),  # recall@3 = 0.0, hit = 0
        (["a", "b"], set()),  # undefined, excluded
        ([], {"d", "e", "f"}),  # empty results: recall 0, hit 0
    ]
    assert macro_recall_at_k(items, 3) == pytest.approx(0.5 / 3)
    assert macro_hit_rate_at_k(items, 3) == pytest.approx(1 / 3)


def test_macro_average_with_no_defined_items_is_none() -> None:
    assert macro_recall_at_k([(["a"], set())], 5) is None
    assert macro_average([]) is None


# --- F-05: diagnosis evidence and recommendation metrics -----------------------------------


def test_supporting_evidence_recall_and_precision() -> None:
    expected = ["load_duty", "unloaded_tp3_slope"]
    surfaced = ["load_duty", "off_fraction"]
    assert evidence_recall(expected, surfaced) == 0.5
    assert evidence_precision(expected, surfaced) == 0.5


def test_keys_match_exactly_after_trim_and_casefold_only() -> None:
    assert evidence_recall(["Load_Duty "], ["load_duty"]) == 1.0
    assert evidence_recall(["load_duty"], ["load duty elevated"]) == 0.0


def test_missing_check_disclosure() -> None:
    assert evidence_recall(["air_demand_flowmeter"], ["air_demand_flowmeter"]) == 1.0
    assert evidence_recall(["air_demand_flowmeter", "pre_event_baseline"], []) == 0.0


def test_contradicting_evidence_disclosure() -> None:
    assert evidence_recall(["valve_contradiction"], ["stale_telemetry"]) == 0.0
    assert evidence_recall(["valve_contradiction"], ["valve_contradiction", "x"]) == 1.0


def test_zero_denominators_are_not_applicable() -> None:
    assert evidence_recall([], ["load_duty"]) is None
    assert evidence_recall([], []) is None
    assert evidence_precision(["load_duty"], []) is None
    assert evidence_precision([], ["load_duty"]) == 0.0


def test_duplicate_keys_count_once() -> None:
    assert evidence_recall(["load_duty", "load_duty"], ["load_duty"]) == 1.0


def test_blank_keys_are_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        evidence_recall([" "], [])


def test_missing_check_precision() -> None:
    expected = ["air_demand_flowmeter"]
    assert evidence_precision(expected, ["air_demand_flowmeter", "pre_event_baseline"]) == 0.5
    assert evidence_precision(expected, []) is None


def test_recommendation_accuracy_ignores_hitl() -> None:
    assert recommendation_accurate("inspect", "inspect")
    assert recommendation_accurate("inspect", " Inspect ")
    assert not recommendation_accurate("inspect", "monitor")
    with pytest.raises(ValueError, match="non-empty"):
        recommendation_accurate("", "inspect")


def test_hitl_compliance_is_separate_from_accuracy() -> None:
    assert hitl_compliant(human_approval_required=True, auto_execution_path=False)
    assert not hitl_compliant(human_approval_required=False, auto_execution_path=False)
    assert not hitl_compliant(human_approval_required=True, auto_execution_path=True)


def test_hitl_unknown_inputs_are_not_claimed_compliant() -> None:
    assert hitl_compliant(human_approval_required=None, auto_execution_path=False) is None
    assert hitl_compliant(human_approval_required=True, auto_execution_path=None) is None


# --- F-05: critical (fatal) contradictions: disclosure, disposition, handling --------------

MECHANICAL: dict[str, object] = {
    "abstained": False,
    "primary_category": "air_leakage",
    "contradiction_verification_action": False,
}


def test_no_critical_contradiction_is_not_applicable() -> None:
    assert critical_contradictions_disclosed([], ["load_duty"]) is None
    assert critical_contradiction_handled([], ["load_duty"], **MECHANICAL) is None


def test_mentioned_but_ignored_contradiction_fails_handling() -> None:
    kinds = ["valve_contradiction"]
    cited = ["valve_contradiction", "load_duty"]
    assert critical_contradictions_disclosed(kinds, cited) is True
    assert critical_contradiction_handled(kinds, cited, **MECHANICAL) is False


def test_hidden_critical_contradiction_fails_even_with_safe_disposition() -> None:
    kinds = ["valve_contradiction"]
    assert critical_contradictions_disclosed(kinds, ["load_duty"]) is False
    assert (
        critical_contradiction_handled(
            kinds,
            ["load_duty"],
            abstained=True,
            primary_category="insufficient_evidence",
            contradiction_verification_action=None,
        )
        is False
    )


def test_partial_disclosure_of_multiple_contradictions_fails() -> None:
    kinds = ["valve_contradiction", "motor_valve_mismatch"]
    cited = ["valve_contradiction"]
    assert critical_contradictions_disclosed(kinds, cited) is False
    assert (
        critical_contradiction_handled(
            kinds,
            cited,
            abstained=False,
            primary_category="telemetry_inconsistency",
            contradiction_verification_action=False,
        )
        is False
    )


def test_moderate_confidence_mechanical_diagnosis_is_unsafe() -> None:
    # Confidence is deliberately not an input: a moderate band does not make it safe.
    for category in ("air_leakage", "abnormal_high_demand", "concurrent_conditions"):
        assert (
            safe_diagnostic_disposition(
                abstained=False,
                primary_category=category,
                contradiction_verification_action=False,
            )
            is False
        )


def test_high_confidence_mechanical_diagnosis_fails_handling() -> None:
    kinds = ["motor_valve_mismatch"]
    assert critical_contradiction_handled(kinds, kinds, **MECHANICAL) is False


def test_safe_abstention_passes() -> None:
    kinds = ["valve_contradiction"]
    assert (
        critical_contradiction_handled(
            kinds,
            kinds,
            abstained=True,
            primary_category="air_leakage",
            contradiction_verification_action=None,
        )
        is True
    )
    assert (
        critical_contradiction_handled(
            kinds,
            kinds,
            abstained=False,
            primary_category="insufficient_evidence",
            contradiction_verification_action=None,
        )
        is True
    )


def test_safe_category_alone_is_undecided_without_recommendation_info() -> None:
    for category in ("telemetry_inconsistency", "sensor_malfunction"):
        assert (
            safe_diagnostic_disposition(
                abstained=False,
                primary_category=category,
                contradiction_verification_action=None,
            )
            is None
        )


def test_sensor_malfunction_with_unsafe_recommendation_fails() -> None:
    kinds = ["sensor_freeze"]
    assert (
        critical_contradiction_handled(
            kinds,
            ["stale_telemetry"],
            abstained=False,
            primary_category="sensor_malfunction",
            contradiction_verification_action=True,
            recommendation_safe=False,
        )
        is False
    )


def test_telemetry_inconsistency_with_physical_contradiction_is_not_auto_safe() -> None:
    # A valve/motor contradiction may be a real physical fault; the category does not settle it.
    kinds = ["valve_contradiction", "motor_valve_mismatch"]
    inputs = {
        "abstained": False,
        "primary_category": "telemetry_inconsistency",
        "contradiction_verification_action": None,
    }
    assert critical_contradiction_handled(kinds, kinds, **inputs) is None
    assert (
        critical_contradiction_handled(kinds, kinds, **inputs, recommendation_safe=False) is False
    )
    assert critical_contradiction_handled(kinds, kinds, **inputs, recommendation_safe=True) is True


def test_valid_safe_abstention_ignores_recommendation_label() -> None:
    assert (
        safe_diagnostic_disposition(
            abstained=True,
            primary_category="insufficient_evidence",
            contradiction_verification_action=None,
            recommendation_safe=None,
        )
        is True
    )


def test_safe_verification_action_needs_safe_recommendation() -> None:
    base = {
        "abstained": False,
        "primary_category": "air_leakage",
        "contradiction_verification_action": True,
    }
    kinds = ["valve_contradiction"]
    assert critical_contradiction_handled(kinds, kinds, **base, recommendation_safe=True) is True
    assert critical_contradiction_handled(kinds, kinds, **base) is None
    assert critical_contradiction_handled(kinds, kinds, **base, recommendation_safe=False) is False


def test_missing_verification_label_is_undecided() -> None:
    for category in ("air_leakage", "sensor_malfunction"):
        assert (
            safe_diagnostic_disposition(
                abstained=False,
                primary_category=category,
                contradiction_verification_action=None,
                recommendation_safe=None,
            )
            is None
        )
    # A safe recommendation does not rescue a mechanical diagnosis without a verification label.
    assert (
        safe_diagnostic_disposition(
            abstained=False,
            primary_category="air_leakage",
            contradiction_verification_action=None,
            recommendation_safe=True,
        )
        is None
    )


def test_missing_disposition_inputs_are_undecided_not_pass() -> None:
    kinds = ["valve_contradiction"]
    for inputs in (
        {
            "abstained": False,
            "primary_category": "air_leakage",
            "contradiction_verification_action": None,
        },
        {
            "abstained": None,
            "primary_category": "air_leakage",
            "contradiction_verification_action": False,
        },
        {"abstained": False, "primary_category": None, "contradiction_verification_action": False},
    ):
        assert safe_diagnostic_disposition(**inputs) is None
        assert critical_contradiction_handled(kinds, kinds, **inputs) is None


def test_sensor_freeze_is_disclosed_as_stale_telemetry() -> None:
    assert critical_contradictions_disclosed(["sensor_freeze"], ["stale_telemetry"]) is True
    assert critical_contradictions_disclosed(["out_of_range"], ["stale_telemetry"]) is False


# --- F-05: strict ground-truth keys, lenient system-produced keys ---------------------------


@pytest.mark.parametrize("bad", ["", "   ", "load duty", "load_duty!", None, 3])
def test_invalid_expected_keys_still_raise(bad: object) -> None:
    with pytest.raises(ValueError, match="canonical identifiers"):
        evidence_recall([bad], ["load_duty"])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="canonical identifiers"):
        critical_contradictions_disclosed([bad], ["load_duty"])  # type: ignore[list-item]


def test_blank_and_malformed_surfaced_keys_are_ignored() -> None:
    surfaced = ["", "   ", "load duty elevated", None, 7, "load_duty"]
    assert evidence_recall(["load_duty"], surfaced) == 1.0
    assert evidence_precision(["load_duty"], surfaced) == 1.0
    assert evidence_precision(["load_duty"], ["", "  ", None]) is None
    assert critical_contradictions_disclosed(["valve_contradiction"], ["", "VALVE_CONTRADICTION "])


def test_valid_surfaced_keys_keep_matching_and_metrics() -> None:
    expected = ["load_duty", "unloaded_tp3_slope"]
    surfaced = [" Load_Duty", "off_fraction", "high_rate_electrical_current_gt_1hz"]
    assert evidence_recall(expected, surfaced) == 0.5
    assert evidence_precision(expected, surfaced) == pytest.approx(1 / 3)

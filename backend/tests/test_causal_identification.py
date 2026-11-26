"""Tests for fail-closed causal identification assessments."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from factorymind.causal.identification import (
    CausalAnalysisAssessment,
    assess_current_metropt3_analysis,
)


@pytest.mark.parametrize(
    "analysis_kind",
    ["effect_estimation", "intervention", "counterfactual"],
)
def test_current_metropt3_assessments_return_insufficient_evidence(
    analysis_kind: str,
) -> None:
    assessment = assess_current_metropt3_analysis(analysis_kind)  # type: ignore[arg-type]

    assert assessment.status == "insufficient_evidence"
    assert assessment.effect_estimate is None
    assert assessment.confidence_interval is None
    assert assessment.counterfactual_is_observed_fact is False
    assert assessment.causal_question
    assert assessment.treatment_id == "metropt3.air_leakage"
    assert assessment.outcome_id
    assert assessment.confounder_ids == ["metropt3.downstream_air_demand"]
    assert assessment.identification_checks
    assert assessment.uncertainty
    assert assessment.sensitivity
    assert assessment.robustness_checks
    assert all(check.status == "not_run" for check in assessment.robustness_checks)
    assert assessment.evidence_needed


def test_intervention_result_separates_twin_projection_from_causal_effect() -> None:
    assessment = assess_current_metropt3_analysis("intervention")

    assert "feature definitions" in assessment.estimation_method
    assert any("simulated projections" in limitation for limitation in assessment.limitations)
    assert any("parameter non-identifiability" in note for note in assessment.uncertainty)


def test_counterfactual_result_does_not_claim_unobserved_facts() -> None:
    assessment = assess_current_metropt3_analysis("counterfactual")

    assert assessment.counterfactual_is_observed_fact is False
    assert any("not an observed fact" in note for note in assessment.uncertainty)
    assert any("No counterfactual claim" in note for note in assessment.limitations)


def test_assessment_schema_rejects_causal_estimate_values() -> None:
    data = assess_current_metropt3_analysis("effect_estimation").model_dump()
    data["effect_estimate"] = 0.25

    with pytest.raises(ValidationError, match="Input should be None"):
        CausalAnalysisAssessment.model_validate(data)


def test_unknown_analysis_kind_fails_loudly() -> None:
    with pytest.raises(ValueError, match="Unsupported MetroPT-3 causal analysis kind"):
        assess_current_metropt3_analysis("causal_discovery")  # type: ignore[arg-type]

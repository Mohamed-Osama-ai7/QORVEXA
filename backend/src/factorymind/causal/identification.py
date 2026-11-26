"""Fail-closed identification assessments for the approved MetroPT-3 causal questions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from factorymind.causal.schema import CausalIdentifier, NonEmptyText

CausalAnalysisKind = Literal["effect_estimation", "intervention", "counterfactual"]


class IdentificationCheck(BaseModel):
    """A required identification condition and its evidence status for this dataset."""

    model_config = ConfigDict(extra="forbid")

    condition: NonEmptyText
    status: Literal["not_met", "unverified"]
    evidence: NonEmptyText


class RobustnessCheck(BaseModel):
    """A falsification or robustness check that was not run."""

    model_config = ConfigDict(extra="forbid")

    check: NonEmptyText
    status: Literal["not_run"] = "not_run"
    reason: NonEmptyText


class CausalAnalysisAssessment(BaseModel):
    """An explicit insufficient-evidence result; it cannot encode a causal estimate."""

    model_config = ConfigDict(extra="forbid")

    analysis_kind: CausalAnalysisKind
    status: Literal["insufficient_evidence"] = "insufficient_evidence"
    causal_question: NonEmptyText
    treatment_id: CausalIdentifier
    outcome_id: CausalIdentifier
    confounder_ids: list[CausalIdentifier]
    identification_checks: list[IdentificationCheck] = Field(min_length=1)
    estimation_method: NonEmptyText
    effect_estimate: None = None
    confidence_interval: None = None
    uncertainty: list[NonEmptyText] = Field(min_length=1)
    sensitivity: list[NonEmptyText] = Field(min_length=1)
    robustness_checks: list[RobustnessCheck] = Field(min_length=1)
    limitations: list[NonEmptyText] = Field(min_length=1)
    evidence_needed: list[NonEmptyText] = Field(min_length=1)
    counterfactual_is_observed_fact: Literal[False] = False


_METROPT3_ASSESSMENTS: dict[CausalAnalysisKind, dict[str, object]] = {
    "effect_estimation": {
        "causal_question": (
            "What is the effect of an air-leak condition on unloaded TP3 pressure slope "
            "for the MetroPT-3 APU?"
        ),
        "treatment_id": "metropt3.air_leakage",
        "outcome_id": "metropt3.unloaded_tp3_slope",
        "confounder_ids": ["metropt3.downstream_air_demand"],
        "estimation_method": (
            "No estimator run: air-leak exposure and downstream demand are not measured, "
            "so an adjusted observational effect is not identified."
        ),
        "identification_checks": [
            {
                "condition": "Treatment is observed and temporally aligned to telemetry.",
                "status": "not_met",
                "evidence": (
                    "Air leakage is a latent graph variable; the four operator reports are "
                    "coarse intervals with timezone and maintenance-alignment ambiguity."
                ),
            },
            {
                "condition": "Relevant common causes are measured for adjustment.",
                "status": "not_met",
                "evidence": (
                    "Downstream air demand is explicitly unobserved and is a competing cause "
                    "of compressor loading."
                ),
            },
            {
                "condition": "Conditional exchangeability, consistency, and positivity hold.",
                "status": "unverified",
                "evidence": (
                    "One asset's observational time series does not establish these conditions "
                    "or a well-defined treatment contrast."
                ),
            },
            {
                "condition": "Temporal ordering and dependence are handled by the design.",
                "status": "unverified",
                "evidence": (
                    "The weak event intervals do not establish onset time; telemetry is "
                    "autocorrelated and comes from one asset."
                ),
            },
        ],
        "uncertainty": [
            "No point estimate, standard error, confidence interval, or posterior is available.",
            "Sampling uncertainty cannot resolve unmeasured confounding or exposure "
            "misclassification.",
        ],
        "sensitivity": [
            "Unmeasured-confounding sensitivity is not run because no identified estimate exists.",
            "Sensitivity analysis cannot repair the latent treatment and missing demand "
            "measurement.",
        ],
        "robustness_checks": [
            {
                "check": "Negative-control exposure or outcome",
                "reason": (
                    "No justified negative control is specified for this single-asset dataset."
                ),
            },
            {
                "check": "Alternative temporal windows and lag specifications",
                "reason": (
                    "No reliable treatment onset label is available to define the analysis window."
                ),
            },
        ],
        "limitations": [
            "MetroPT-3 has no sample-level air-leak treatment labels or independent assets.",
            "The outcome is a derived feature, not a direct measurement of leakage.",
            "The graph edges are documented hypotheses, not established causal relationships.",
        ],
        "evidence_needed": [
            "Timestamp-aligned, validated leak onset/status and repair records.",
            "Measurements of downstream pneumatic demand and other time-varying common causes.",
            "A defensible treatment contrast, overlap assessment, and temporal causal design.",
        ],
    },
    "intervention": {
        "causal_question": (
            "What would compressor load duty do under a well-defined intervention that removes "
            "an air leak, compared with the same operating conditions without that intervention?"
        ),
        "treatment_id": "metropt3.air_leakage",
        "outcome_id": "metropt3.load_duty",
        "confounder_ids": ["metropt3.downstream_air_demand"],
        "estimation_method": (
            "No intervention effect is computed: the causal SCM contains feature definitions, "
            "not a plant response mechanism for do(air_leakage=0)."
        ),
        "identification_checks": [
            {
                "condition": "The intervention is well-defined and maps to a model input.",
                "status": "not_met",
                "evidence": (
                    "Air leakage is latent; no intervention protocol or measured treatment "
                    "level is represented in the causal SCM."
                ),
            },
            {
                "condition": "The response mechanism is supported for the intervention.",
                "status": "not_met",
                "evidence": (
                    "The SCM only computes existing window features. It has no structural "
                    "equation for plant load-duty response to leak removal."
                ),
            },
            {
                "condition": "Operating conditions and competing causes are controlled.",
                "status": "not_met",
                "evidence": (
                    "Downstream air demand is unobserved; its contribution cannot be separated "
                    "from internal leakage using these data."
                ),
            },
        ],
        "uncertainty": [
            "No intervention contrast or uncertainty interval is produced.",
            "The calibrated Digital Twin has parameter non-identifiability and horizon-specific "
            "forecast limits; its output remains a simulated projection.",
        ],
        "sensitivity": [
            "No causal sensitivity analysis is run without an identified intervention effect.",
            "Digital Twin parameter sensitivity does not establish causal identification.",
        ],
        "robustness_checks": [
            {
                "check": "Intervention consistency across operating regimes",
                "reason": "No measured intervention or leak-removal episodes are available.",
            },
            {
                "check": "Observed post-intervention comparison",
                "reason": "No verified, timestamp-aligned intervention outcome is supplied.",
            },
        ],
        "limitations": [
            "No equipment actuation or maintenance execution is performed.",
            "Existing Twin scenario outputs are explicitly simulated projections, not causal "
            "effects.",
            "The Twin documents unidentifiable downstream-demand versus internal-leak "
            "contributions.",
        ],
        "evidence_needed": [
            "A precisely specified intervention and its assignment or implementation record.",
            "Pre/post telemetry with aligned repair records and operating-demand measurements.",
            "A validated plant response model for the intervention and operating regime.",
        ],
    },
    "counterfactual": {
        "causal_question": (
            "For a reported air-leak interval, what would that same APU's TP3 trajectory have "
            "been over the interval if the leak had not occurred?"
        ),
        "treatment_id": "metropt3.air_leakage",
        "outcome_id": "metropt3.tp3_pressure",
        "confounder_ids": ["metropt3.downstream_air_demand"],
        "estimation_method": (
            "No unit-level counterfactual is generated: the current SCM has no supported "
            "individual structural response model or factual leak state."
        ),
        "identification_checks": [
            {
                "condition": "The factual treatment state and interval are known.",
                "status": "not_met",
                "evidence": (
                    "The operator reports are weak, coarse intervals and do not provide "
                    "sample-level leak status or certain onset."
                ),
            },
            {
                "condition": "A structural model supports both factual and alternate states.",
                "status": "not_met",
                "evidence": (
                    "The causal SCM equations reproduce load-duty and pressure-slope features; "
                    "they do not model the plant's individual TP3 trajectory under leak removal."
                ),
            },
            {
                "condition": "Individual exogenous state and relevant causes are observed.",
                "status": "not_met",
                "evidence": (
                    "Downstream demand and other interval-specific causes are not measured."
                ),
            },
        ],
        "uncertainty": [
            "No individual counterfactual value or uncertainty interval is produced.",
            "A simulated trajectory would be conditional on model assumptions and is not an "
            "observed fact about the reported interval.",
        ],
        "sensitivity": [
            "No counterfactual sensitivity analysis is run because no supported unit-level "
            "model exists.",
            "Twin scenario parameter perturbations are model sensitivity, not empirical "
            "counterfactual uncertainty for this event.",
        ],
        "robustness_checks": [
            {
                "check": "Factual trajectory replay and held-out event validation",
                "reason": (
                    "Weak report timing and the absence of verified event-level leak state "
                    "prevent a factual-versus-counterfactual validation."
                ),
            },
            {
                "check": "Alternative structural equations for the same asset",
                "reason": (
                    "No evidence-supported event-level structural causal equations are available."
                ),
            },
        ],
        "limitations": [
            "The report interval is not sample-level ground truth.",
            "No counterfactual claim is made about what actually would have happened.",
            "The Digital Twin's simulated projections are distinct from an identified individual "
            "causal counterfactual.",
        ],
        "evidence_needed": [
            "Verified leak state and precise event/repair timestamps.",
            "An event-level structural model validated for both factual and no-leak regimes.",
            "Relevant time-varying demand and operating-state measurements.",
        ],
    },
}


def assess_current_metropt3_analysis(
    analysis_kind: CausalAnalysisKind,
) -> CausalAnalysisAssessment:
    """Return the identification assessment; no causal estimator is run for current MetroPT-3."""
    if analysis_kind not in _METROPT3_ASSESSMENTS:
        raise ValueError(f"Unsupported MetroPT-3 causal analysis kind: {analysis_kind!r}")
    return CausalAnalysisAssessment(
        analysis_kind=analysis_kind,
        **_METROPT3_ASSESSMENTS[analysis_kind],
    )


__all__ = [
    "CausalAnalysisAssessment",
    "CausalAnalysisKind",
    "IdentificationCheck",
    "RobustnessCheck",
    "assess_current_metropt3_analysis",
]

"""Typed domain contracts for WP-B Explainable Root-Cause Analysis (RCA).

Hypotheses rank relative physical support grounded strictly in an
:class:`~factorymind.contracts.EventEvidencePackage`. Support scores index evidence
consistency rather than calibrated failure probabilities, never claim statistical
correlation alone equals physical causation, and never recommend automated actuation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from factorymind.contracts.events import (
    EventPhaseName,
    EvidenceOrigin,
    EvidenceQualityStatus,
    ProvenanceRecord,
)

RcaHypothesisCategory = Literal[
    "air_leakage",
    "sensor_malfunction",
    "telemetry_inconsistency",
    "abnormal_high_demand",
    "concurrent_conditions",
    "insufficient_evidence",
]
RelativeSupport = Literal["primary", "plausible_alternative", "weak", "ruled_out"]
RcaConfidenceBand = Literal["high", "moderate", "low", "abstained"]
CausalConclusionStatus = Literal["not_established"]


class EvidenceReference(BaseModel):
    """Explicit origin-tagged citation linking RCA claims back to the evidence package."""

    ref_id: str
    origin: EvidenceOrigin
    phase: EventPhaseName | None = None
    signal_or_feature: str
    statement: str
    observed_value: float | None = None
    unit: str | None = None
    baseline_value: float | None = None


class VerificationStep(BaseModel):
    """Non-actuating advisory field/diagnostic inspection to confirm or refute a hypothesis."""

    step_id: str
    priority: Literal["immediate", "high", "standard"]
    action: str
    expected_if_confirmed: str
    expected_if_refuted: str
    safety_note: str = (
        "Advisory field verification only; never actuate equipment or modify PLC setpoints."
    )


class RankedHypothesis(BaseModel):
    """Candidate physical or data-integrity explanation ranked by structured evidence."""

    hypothesis_id: str
    category: RcaHypothesisCategory
    title: str
    rank: int = Field(ge=1)
    relative_support: RelativeSupport
    support_score: float = Field(ge=0.0, le=1.0)
    confidence_band: RcaConfidenceBand
    physical_mechanism: str
    supporting_evidence: list[EvidenceReference] = Field(default_factory=list)
    contradicting_evidence: list[EvidenceReference] = Field(default_factory=list)
    missing_evidence: list[EvidenceReference] = Field(default_factory=list)
    correlation_vs_causation_note: str
    verification_steps: list[VerificationStep] = Field(default_factory=list)
    causal_conclusion_status: CausalConclusionStatus = "not_established"
    causal_graph_edge_ids: list[str] = Field(default_factory=list)
    scm_equation_ids: list[str] = Field(default_factory=list)


class RcaAssessment(BaseModel):
    """Complete explainable multi-hypothesis RCA report grounded in one evidence package."""

    rca_id: str
    package_id: str
    asset_id: str
    incident_id: str | None = None
    event_start: datetime
    event_end: datetime
    abstained: bool
    abstention_reason: str | None = None
    primary_category: RcaHypothesisCategory
    primary_title: str
    summary: str
    uncertainty_note: str
    quality_status: EvidenceQualityStatus
    confidence_penalty_factor: float = Field(ge=0.0, le=1.0)
    score_meaning_note: str = (
        "support_score is a deterministic physical-evidence consistency index (0..1), "
        "NOT a calibrated failure probability."
    )
    ranked_hypotheses: list[RankedHypothesis]
    correlation_vs_causation_boundary: list[str]
    provenance: ProvenanceRecord
    root_cause_confirmed: Literal[False] = False
    causal_graph_id: str | None = None
    scm_id: str | None = None
    ranking_method_note: str = (
        "Candidates are ranked by fixed category-specific evidence rules and support scores, "
        "with data-quality and contradiction penalties where defined; scores are consistency "
        "indices, not probabilities. 'Primary' means top-ranked candidate only."
    )
    temporal_ordering_note: str = (
        "Evidence is organized as pre-event baseline, event onset/evolution/peak, and recovery "
        "or persistence. Temporal sequence is descriptive and does not by itself establish cause."
    )
    causal_context_note: str = (
        "Causal graph edges are hypotheses; SCM equation references identify deterministic "
        "feature definitions only. No causal root cause is established."
    )
    advisory: str = (
        "Advisory root-cause hypotheses for human engineering review. Grounded strictly in the "
        "associated EventEvidencePackage; FactoryMind never actuates or controls equipment."
    )

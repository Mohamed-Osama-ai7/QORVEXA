"""JSONL record schemas for QORVEXA benchmark cases, system predictions and results.

Ground truth (``RcaCase``, ``RetrievalCase``) is validated strictly: unknown fields, bad keys or
inconsistent labels reject the whole file. System predictions (``RcaPrediction``) are parsed
leniently by ``jsonl.load_rca_predictions``: a malformed record becomes an error entry for its
case instead of aborting the batch. Field-level documentation lives in
``docs/qorvexa/EVALUATION_STRATEGY.md`` §D.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from qorvexa.anomaly.rca_eval import validate_expected_keys
from qorvexa.contracts.events import ContradictionRecord
from qorvexa.contracts.rca import RcaHypothesisCategory

CASE_ID_PATTERN = r"^[a-z0-9][a-z0-9_.-]{0,127}$"
CONTRADICTION_KINDS: frozenset[str] = frozenset(
    get_args(ContradictionRecord.model_fields["kind"].annotation)
)

RcaProvenance = Literal["metropt3_observed", "synthetic_scenario", "adversarial_contract"]
GroundTruthStatus = Literal["labelled", "partial", "insufficient"]
GroundTruthLevel = Literal[
    "operator_report_coarse", "expert_authored", "physics_authored", "contract_fixture", "none"
]
EvaluationRole = Literal["development", "held_out", "unspecified"]
RetrievalProvenance = Literal[
    "project_document_retrieval", "dataset_document_retrieval", "out_of_scope_probe"
]
Split = Literal["development", "validation", "test", "fixture", "unassigned"]
OriginKind = Literal[
    "authored_team",
    "authored_independent",
    "derived_from_dev",
    "observed_dataset",
    "synthetic_generated",
    "contract_fixture",
    "unknown",
]
ContentOrigin = Literal["native", "translated", "synthetic", "unspecified"]
RelevanceSource = Literal[
    "inherited_prefix_v0", "author_asserted", "annotator_labelled", "pooled_adjudicated"
]
MetricState = Literal[
    "scored", "not_applicable", "unlabelled", "undecided", "unavailable", "no_prediction"
]
CaseStatus = Literal[
    "scored",
    "missing_prediction",
    "invalid_prediction",
    "no_relevant_in_corpus",
    "stale_label",
    "unsupported_language",
]
_ARABIC = range(0x0600, 0x0700)


def has_arabic(text: str) -> bool:
    return any(ord(ch) in _ARABIC for ch in text)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _keys_or_none(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    return sorted(validate_expected_keys(value))


class EventWindow(_Strict):
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _ordered(self) -> EventWindow:
        if self.end <= self.start:
            raise ValueError("event_window.end must be after start")
        return self


class Origin(_Strict):
    """Who produced a case and where it came from. Never inferred: unknown stays ``unknown``."""

    kind: OriginKind
    author_id: str | None = None
    authored_date: date | None = None
    reviewed_by: str | None = None
    derived_from: str | None = None

    @model_validator(mode="after")
    def _independent_needs_author(self) -> Origin:
        if self.kind == "authored_independent" and not self.author_id:
            raise ValueError("authored_independent origin requires an author_id")
        return self


class RcaCase(_Strict):
    """One RCA benchmark case (ground truth). ``None`` label = not labelled (metric reported as
    ``unlabelled``); an empty list = labelled as "nothing expected" (metric ``not_applicable``)."""

    schema_version: Literal["rca_case/v0", "rca_case/v1"]
    case_id: str = Field(pattern=CASE_ID_PATTERN)
    task: Literal["rca"]
    provenance: RcaProvenance
    description: str = Field(min_length=1)
    input_ref: str | None = None
    event_window: EventWindow | None = None
    ground_truth_status: GroundTruthStatus
    ground_truth_level: GroundTruthLevel
    evaluation_role: EvaluationRole = "unspecified"
    expected_categories: list[RcaHypothesisCategory] | None = None
    unacceptable_categories: list[RcaHypothesisCategory] = Field(default_factory=list)
    expected_abstain: bool | None = None
    expected_supporting_evidence: list[str] | None = None
    expected_contradicting_evidence: list[str] | None = None
    expected_missing_checks: list[str] | None = None
    critical_contradictions: list[str] | None = None
    notes: str = ""
    # v1 only. ``cluster_id`` must be present in v1 files; null means dependence unknown.
    split: Split | None = None
    origin: Origin | None = None
    cluster_id: str | None = None

    _norm_sup = field_validator("expected_supporting_evidence")(_keys_or_none)
    _norm_con = field_validator("expected_contradicting_evidence")(_keys_or_none)
    _norm_miss = field_validator("expected_missing_checks")(_keys_or_none)

    @field_validator("critical_contradictions")
    @classmethod
    def _known_kinds(cls, value: list[str] | None) -> list[str] | None:
        keys = _keys_or_none(value)
        if keys is not None and (unknown := set(keys) - CONTRADICTION_KINDS):
            raise ValueError(f"unknown contradiction kinds: {sorted(unknown)}")
        return keys

    @model_validator(mode="after")
    def _consistent(self) -> RcaCase:
        if self.ground_truth_status == "insufficient" and (
            self.expected_categories is not None or self.expected_abstain is not None
        ):
            raise ValueError(
                "ground_truth_status 'insufficient' must not carry expected_categories or "
                "expected_abstain; do not invent ground truth"
            )
        if self.expected_categories is not None:
            if not self.expected_categories:
                raise ValueError("expected_categories must be null or non-empty")
            if overlap := set(self.expected_categories) & set(self.unacceptable_categories):
                raise ValueError(f"categories both expected and unacceptable: {sorted(overlap)}")
        if self.provenance == "metropt3_observed" and self.event_window is None:
            raise ValueError("metropt3_observed cases need an event_window")
        if self.provenance == "metropt3_observed" and self.evaluation_role == "held_out":
            raise ValueError(
                "metropt3_observed cases cannot be labelled held_out: the RCA engine was "
                "developed with these windows in view (see docs/qorvexa/BASELINE_STATUS.md)"
            )
        if self.provenance == "adversarial_contract" and self.ground_truth_level not in (
            "contract_fixture",
            "none",
        ):
            raise ValueError("adversarial_contract cases use ground_truth_level contract_fixture")
        self._check_version_rules()
        return self

    @property
    def effective_split(self) -> Split:
        """v1: the declared split. v0: ``development`` only if marked so; never ``test``."""
        if self.split is not None:
            return self.split
        return "development" if self.evaluation_role == "development" else "unassigned"

    def _check_version_rules(self) -> None:
        v1_fields = ("split", "origin", "cluster_id")
        present = [f for f in v1_fields if f in self.model_fields_set]
        if self.schema_version == "rca_case/v0":
            if present:
                raise ValueError(f"fields {present} require schema_version rca_case/v1")
            return
        for field in v1_fields:
            if field not in self.model_fields_set:
                raise ValueError(f"rca_case/v1 requires {field!r} (cluster_id may be null)")
        if self.evaluation_role != "unspecified":
            raise ValueError("rca_case/v1 uses 'split'; evaluation_role is v0 only")
        if self.split == "test" and self.provenance == "metropt3_observed":
            raise ValueError(
                "metropt3_observed cases cannot be test: F1-F4 are the dataset's only labelled "
                "failures and are development cases"
            )
        if (self.provenance == "adversarial_contract") != (self.split == "fixture"):
            raise ValueError("split 'fixture' is for, and only for, adversarial_contract cases")
        if self.split == "test" and (
            self.origin is None or self.origin.kind != "authored_independent"
        ):
            raise ValueError("test cases require an authored_independent origin")
        if (
            self.origin is not None
            and self.origin.kind == "derived_from_dev"
            and self.split in ("validation", "test")
        ):
            raise ValueError("derived_from_dev cases cannot be validation or test")


class RetrievalCase(_Strict):
    """One retrieval benchmark case. Relevance is by chunk-id prefix, expanded against the
    evaluated corpus; out-of-scope probes have no relevant prefixes."""

    schema_version: Literal["retrieval_case/v0", "retrieval_case/v1"]
    case_id: str = Field(pattern=CASE_ID_PATTERN)
    task: Literal["retrieval"]
    provenance: RetrievalProvenance
    query: str = Field(min_length=1)
    asset_id: str = Field(min_length=1)
    language: Literal["en", "ar"] = "en"
    relevant_chunk_prefixes: list[str] = Field(default_factory=list)
    notes: str = ""
    # v1 only (all of split/origin/cluster_id/content_origin/relevance_source are then required;
    # cluster_id and paraphrase_group_id may be null = unknown / none).
    split: Split | None = None
    origin: Origin | None = None
    cluster_id: str | None = None
    paraphrase_group_id: str | None = None
    content_origin: ContentOrigin | None = None
    translation_of: str | None = None
    translator: str | None = None
    source_chunk_ids: list[str] = Field(default_factory=list)
    source_chunk_sha256: dict[str, str] = Field(default_factory=dict)
    relevance_source: RelevanceSource | None = None
    annotator_ids: list[str] = Field(default_factory=list)

    @field_validator("relevant_chunk_prefixes")
    @classmethod
    def _non_blank(cls, value: list[str]) -> list[str]:
        if any(not p.strip() or p != p.strip() for p in value):
            raise ValueError("relevant_chunk_prefixes must be non-blank and already trimmed")
        return sorted(set(value))

    @model_validator(mode="after")
    def _scope(self) -> RetrievalCase:
        relevant = self.relevant_chunk_prefixes or self.source_chunk_ids
        if (self.provenance == "out_of_scope_probe") != (not relevant):
            raise ValueError(
                "only out_of_scope_probe cases may (and must) have no relevant prefixes or "
                "source_chunk_ids"
            )
        self._check_version_rules()
        return self

    @property
    def effective_split(self) -> Split:
        """v1: the declared split. v0 retrieval cases carry no role, so they are ``unassigned``."""
        return self.split if self.split is not None else "unassigned"

    def _check_version_rules(self) -> None:
        v1_fields = (
            "split",
            "origin",
            "cluster_id",
            "content_origin",
            "relevance_source",
            "paraphrase_group_id",
            "source_chunk_ids",
            "source_chunk_sha256",
            "annotator_ids",
            "translation_of",
            "translator",
        )
        present = [f for f in v1_fields if f in self.model_fields_set]
        if self.schema_version == "retrieval_case/v0":
            if present:
                raise ValueError(f"fields {present} require schema_version retrieval_case/v1")
            return
        for field in ("split", "origin", "cluster_id", "content_origin", "relevance_source"):
            if field not in self.model_fields_set:
                raise ValueError(f"retrieval_case/v1 requires {field!r}")
        if (
            self.origin is not None
            and self.origin.kind == "derived_from_dev"
            and self.split in ("validation", "test")
        ):
            raise ValueError("derived_from_dev cases cannot be validation or test")
        if self.split == "fixture":
            raise ValueError("split 'fixture' is only valid for RCA contract cases")
        if self.split == "test":
            if self.origin is None or self.origin.kind != "authored_independent":
                raise ValueError("test cases require an authored_independent origin")
            if self.relevance_source not in ("annotator_labelled", "pooled_adjudicated"):
                raise ValueError(
                    "test cases require annotator_labelled or pooled_adjudicated relevance"
                )
        if (
            self.relevance_source in ("annotator_labelled", "pooled_adjudicated")
            and not self.annotator_ids
        ):
            raise ValueError(f"relevance_source {self.relevance_source!r} requires annotator_ids")
        if self.content_origin == "translated":
            if not self.translation_of:
                raise ValueError("translated content requires translation_of")
        elif self.translation_of or self.translator:
            raise ValueError("translation_of/translator are only valid for translated content")
        if self.language == "ar" and not has_arabic(self.query):
            raise ValueError("language 'ar' but the query contains no Arabic characters")
        if self.source_chunk_sha256 and set(self.source_chunk_sha256) - set(self.source_chunk_ids):
            raise ValueError("source_chunk_sha256 keys must be listed in source_chunk_ids")


class RcaPrediction(BaseModel):
    """One system output for an RCA case. Key lists accept anything; malformed keys are
    discarded during scoring. Structural errors (wrong types, unknown category) make the
    record invalid for its case only."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    schema_version: Literal["rca_prediction/v0"]
    case_id: str = Field(min_length=1)
    producer: str = Field(min_length=1)
    abstained: StrictBool
    primary_category: RcaHypothesisCategory
    confidence_band: str | None = None
    supporting_evidence: list[Any] = Field(default_factory=list)
    contradicting_evidence: list[Any] = Field(default_factory=list)
    missing_evidence: list[Any] = Field(default_factory=list)
    cited_evidence: list[Any] = Field(default_factory=list)
    package_critical_contradictions: list[Any] | None = None


class MetricOutcome(_Strict):
    value: float | None
    state: MetricState
    reason: str | None = None


class CaseResult(_Strict):
    schema_version: Literal["eval_result/v0", "eval_result/v1"] = "eval_result/v1"
    case_id: str
    task: Literal["rca", "retrieval"]
    provenance: str
    split: Split | None = None
    cluster_id: str | None = None
    status: CaseStatus
    errors: list[str] = Field(default_factory=list)
    discarded_keys: int = 0
    details: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, MetricOutcome]

"""Versioned typed contracts for the Industrial Understanding pipeline (all immutable).

Everything here is UNTRUSTED understanding: nothing implies mathematical validation, solver
success or human approval.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION: Final = "qorvexa-understanding/1"
PIPELINE_VERSION: Final = "understanding-pipeline/1.0"
MAX_CHARS: Final = 20_000
MAX_SEGMENTS: Final = 400
MAX_CLAIMS: Final = 500

F = ConfigDict(frozen=True, extra="forbid")


class Stage(StrEnum):
    INPUT = "INPUT"
    SOURCE_PROCESSING = "SOURCE_PROCESSING"
    LANGUAGE_UNDERSTANDING = "LANGUAGE_UNDERSTANDING"
    FACTORY_TERMINOLOGY_RESOLUTION = "FACTORY_TERMINOLOGY_RESOLUTION"
    ENTITY_RECOGNITION = "ENTITY_RECOGNITION"
    CLAIM_EXTRACTION = "CLAIM_EXTRACTION"
    EVIDENCE_VERIFICATION = "EVIDENCE_VERIFICATION"
    SEMANTIC_ANALYSIS = "SEMANTIC_ANALYSIS"
    CONTRADICTION_ANALYSIS = "CONTRADICTION_ANALYSIS"
    DOMAIN_MAPPING = "DOMAIN_MAPPING"
    GAP_ANALYSIS = "GAP_ANALYSIS"
    CLARIFICATION_PLAN = "CLARIFICATION_PLAN"
    STRUCTURED_UNDERSTANDING = "STRUCTURED_UNDERSTANDING"


class StageStatus(StrEnum):
    OK = "OK"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"  # e.g. no LLM provider configured
    SKIPPED = "SKIPPED"


class SourceDocument(BaseModel):
    """One speaker turn. ``text`` is preserved verbatim and never rewritten."""

    model_config = F
    source_id: str
    factory_id: str
    text: str
    speaker_role: str | None = None  # operator | technician | engineer | manager | ...
    turn: int = 0
    line_id: str | None = None
    department: str | None = None
    default_currency: str | None = None  # factory context, never guessed


class Segment(BaseModel):
    model_config = F
    seg_id: str
    orig_start: int
    orig_end: int
    text: str  # exact original slice
    norm: str  # normalized (search form only)
    norm_start: int
    norm_end: int


class LanguageInfo(BaseModel):
    model_config = F
    seg_id: str
    arabic_chars: int
    latin_chars: int
    primary: str  # ARABIC | LATIN | MIXED | OTHER
    code_switching: bool
    arabizi: bool
    register_hint: (
        str  # heuristic only: EGYPTIAN_COLLOQUIAL_MARKERS | FORMAL_MARKERS | UNDETERMINED
    )
    markers: tuple[str, ...] = ()


class QuantityMention(BaseModel):
    model_config = F
    kind: str  # EXACT | RANGE | PERCENT
    low: str  # exact decimal string
    high: str | None = None
    explicit_zero: bool = False
    unit_text: str = ""  # as written
    unit: str | None = None  # engine-parsable canonical unit, or None when unknown
    currency: str | None = None
    per: str | None = None  # period/denominator word (shift, hour, ...)
    text: str  # exact original text of the mention
    orig_start: int
    orig_end: int
    ambiguous: bool = False
    alternatives: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


class EntityMention(BaseModel):
    model_config = F
    mention_id: str
    text: str
    orig_start: int
    orig_end: int
    entity_type: (
        str  # MACHINE | LINE | SHIFT | PRODUCT | MATERIAL | RESOURCE | DEPARTMENT | UNKNOWN
    )
    status: str  # CONFIRMED_TERM | PATTERN_UNCONFIRMED | UNRESOLVED_GENERIC | AMBIGUOUS
    canonical_id: str | None = None  # only from CONFIRMED terminology
    term_id: str | None = None
    alternatives: tuple[str, ...] = ()


class SemanticType(StrEnum):
    FACT = "FACT"
    ACTUAL_MEASUREMENT = "ACTUAL_MEASUREMENT"
    TARGET = "TARGET"
    HISTORICAL = "HISTORICAL"
    HYPOTHETICAL = "HYPOTHETICAL"
    CONDITIONAL = "CONDITIONAL"
    ASSUMPTION = "ASSUMPTION"
    PROPOSAL = "PROPOSAL"
    RECOMMENDATION = "RECOMMENDATION"
    QUESTION = "QUESTION"


class Predicate(StrEnum):
    PRODUCES_RATE = "PRODUCES_RATE"
    CAPACITY = "CAPACITY"
    DEMAND = "DEMAND"
    CONSUMPTION_RATE = "CONSUMPTION_RATE"
    MARGIN_PER_UNIT = "MARGIN_PER_UNIT"
    COST_PER_UNIT = "COST_PER_UNIT"
    PRICE = "PRICE"
    SCRAP_LEVEL = "SCRAP_LEVEL"
    DEFECT_LEVEL = "DEFECT_LEVEL"
    INTERMITTENT_STOPS = "INTERMITTENT_STOPS"
    DOWNTIME = "DOWNTIME"
    SLOW_OPERATION = "SLOW_OPERATION"
    MATERIAL_SHORTAGE = "MATERIAL_SHORTAGE"
    EXCESS_CONSUMPTION = "EXCESS_CONSUMPTION"
    BOTTLENECK = "BOTTLENECK"
    JAMMING = "JAMMING"
    CANNOT_KEEP_UP = "CANNOT_KEEP_UP"
    UNKNOWN = "UNKNOWN"


class Claim(BaseModel):
    model_config = F
    claim_id: str
    source_id: str
    turn: int
    subject_text: str | None  # as written; entity link via subject_entity
    subject_entity: str | None = None  # EntityMention.mention_id
    subject_from_context: bool = False
    related_text: str | None = None
    related_entity: str | None = None
    predicate: Predicate
    value: QuantityMention | None = None
    qualitative: bool = False
    semantic_type: SemanticType
    negated: bool = False
    modality: str = "ASSERTED"  # ASSERTED | POSSIBLE | OBLIGATORY | UNKNOWN
    bound: str | None = None  # MIN | MAX | None
    condition: str | None = None
    scope_shift: str | None = None
    scope_line: str | None = None
    time_text: str | None = None
    normalized: str  # human-readable interpretation (never the source of truth)
    quote: str
    orig_start: int
    orig_end: int
    term_ids: tuple[str, ...] = ()
    concept_refs: tuple[str, ...] = ()
    provenance: str  # rules/1 | llm:<provider id>
    speaker_role: str | None = None


class Grounding(StrEnum):
    GROUNDED = "GROUNDED"
    PARTIALLY_GROUNDED = "PARTIALLY_GROUNDED"
    UNSUPPORTED = "UNSUPPORTED"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICTED = "CONFLICTED"


class GroundingResult(BaseModel):
    model_config = F
    claim_id: str
    outcome: Grounding
    checks: tuple[tuple[str, bool], ...]  # (check name, passed)
    reasons: tuple[str, ...] = ()


class Contradiction(BaseModel):
    model_config = F
    code: str  # QUANTITY_CONFLICT | UNIT_CONFLICT | TERM_AMBIGUITY | DUPLICATE
    claim_ids: tuple[str, ...]
    description: str


class Gap(BaseModel):
    model_config = F
    gap_id: str
    code: str
    message: str
    affected: tuple[str, ...] = ()  # ledger keys / entities
    blocking: bool = True
    why: str = ""
    depends_on: tuple[str, ...] = ()
    tier: int = 5  # dependency tier: 1 terms/entities .. 5 values


class Question(BaseModel):
    model_config = F
    gap_id: str
    language: str  # ar-EG | ar | en
    text: str
    why: str
    affected: tuple[str, ...]
    blocking: bool
    asked_count: int = 1


class Candidate(BaseModel):
    """A mapped Production Planning candidate. Always an untrusted proposal."""

    model_config = F
    key: str  # formulation ledger key
    value: str
    support: tuple[str, ...]  # claim ids
    quote: str
    status: str  # SUPPORTED | NEEDS_CLARIFICATION
    note: str = ""


class UnsupportedFeature(BaseModel):
    model_config = F
    feature: str  # production-planning feature key (e.g. setup_time)
    quote: str
    orig_start: int
    orig_end: int
    source_id: str


class StageResult(BaseModel):
    model_config = F
    stage: Stage
    status: StageStatus
    summary: str = ""
    errors: tuple[str, ...] = ()


class Readiness(StrEnum):
    BLOCKED = "BLOCKED"  # contradictions / unsupported features / failed stages
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    CANDIDATES_FOR_HUMAN_REVIEW = (
        "CANDIDATES_FOR_HUMAN_REVIEW"  # never "validated" or "solver-ready"
    )


class UnderstandingReport(BaseModel):
    model_config = F
    schema_version: str = SCHEMA_VERSION
    pipeline_version: str = PIPELINE_VERSION
    idempotency_key: str
    factory_id: str
    source_ids: tuple[str, ...]
    source_sha256: tuple[str, ...]
    stages: tuple[StageResult, ...]
    languages: tuple[LanguageInfo, ...]
    entities: tuple[EntityMention, ...]
    claims: tuple[Claim, ...]
    grounding: tuple[GroundingResult, ...]
    rejected_claim_ids: tuple[str, ...]
    term_resolutions: tuple[tuple[str, str, str], ...]  # (surface, status, term/concept)
    contradictions: tuple[Contradiction, ...]
    gaps: tuple[Gap, ...]
    unsupported_features: tuple[UnsupportedFeature, ...]
    candidates: tuple[Candidate, ...]
    questions: tuple[Question, ...]
    readiness: Readiness
    disclaimer: str = (
        "Untrusted understanding only: not mathematically validated, not solver-ready, "
        "not human-approved."
    )
    llm_status: str = "NOT_USED"
    report_sha256: str = Field(default="")

"""Typed records: evidence, provenance, the append-only event and the folded read model."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from qorvexa.industrial.ontology.schema import Value

F = ConfigDict(frozen=True, extra="forbid")


class Basis(StrEnum):
    """Why the system holds a statement. Only the first two can ever be authoritative."""

    HUMAN_CONFIRMED = "HUMAN_CONFIRMED"
    IMPORTED_VERIFIED = "IMPORTED_VERIFIED"  # a human attests a verified import
    PROPOSAL = "PROPOSAL"  # LLM/extractor/human suggestion: never authoritative
    ASSUMPTION = "ASSUMPTION"
    HYPOTHETICAL = "HYPOTHETICAL"  # scenario data


class Status(StrEnum):
    PROPOSED = "PROPOSED"
    CONFIRMED = "CONFIRMED"  # the only authoritative status
    ASSUMED = "ASSUMED"
    HYPOTHETICAL = "HYPOTHETICAL"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"  # was authoritative; no longer valid (history is kept)


class Action(StrEnum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"  # correction or change: new version, old one stays in history
    CONFIRM = "CONFIRM"
    REJECT = "REJECT"
    RETIRE = "RETIRE"
    DISPUTE = "DISPUTE"
    RESOLVE_DISPUTE = "RESOLVE_DISPUTE"


class Kind(StrEnum):
    ENTITY = "ENTITY"
    RELATION = "RELATION"
    DISPUTE = "DISPUTE"


class EvidenceKind(StrEnum):
    CLAIM = "CLAIM"  # understanding / ledger claim id
    DOCUMENT = "DOCUMENT"
    IMPORT = "IMPORT"
    TERM = "TERM"
    HUMAN_STATEMENT = "HUMAN_STATEMENT"
    MEASUREMENT = "MEASUREMENT"


class EvidenceRef(BaseModel):
    model_config = F
    kind: EvidenceKind
    ref: str
    quote: str = ""
    sha256: str = ""  # hash of the referenced source when known


class Scope(BaseModel):
    model_config = F
    site_id: str | None = None
    line_id: str | None = None
    department: str | None = None


class EntityData(BaseModel):
    model_config = F
    concept: str
    schema_version: int
    name: str
    attrs: dict[str, Value] = {}
    scope: Scope = Scope()
    aliases: tuple[str, ...] = ()  # authoritative only on a CONFIRMED record
    valid_from: str | None = None
    valid_until: str | None = None
    superseded_by: str | None = None


class RelationData(BaseModel):
    model_config = F
    relation: str
    schema_version: int
    source: str
    target: str
    attrs: dict[str, Value] = {}
    valid_from: str | None = None
    valid_until: str | None = None
    superseded_by: str | None = None


class DisputeData(BaseModel):
    model_config = F
    subject_id: str  # entity or relation under dispute
    reason: str
    resolution: str = ""


class Event(BaseModel):
    """One line of the append-only log. ``seq`` is the factory revision."""

    model_config = F
    seq: int
    factory_id: str
    action: Action
    kind: Kind
    object_id: str
    object_version: int
    actor_id: str
    actor_kind: str
    at: str
    basis: Basis
    status: Status
    entity: EntityData | None = None
    relation: RelationData | None = None
    dispute: DisputeData | None = None
    evidence: tuple[EvidenceRef, ...] = ()
    note: str = ""
    idempotency_key: str | None = None
    prev_sha: str
    sha: str = ""

    def body_sha256(self) -> str:
        body = self.model_dump(mode="json", exclude={"sha"})
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()

    def content_sha256(self) -> str:
        """Identity of the request itself (no seq/time/chain): used for idempotency."""
        body = self.model_dump(
            mode="json", exclude={"seq", "at", "prev_sha", "sha", "object_version"}
        )
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


class EntityRecord(BaseModel):
    """Folded state of one entity at one revision."""

    model_config = F
    entity_id: str
    factory_id: str
    data: EntityData
    status: Status
    basis: Basis
    version: int
    created_seq: int
    updated_seq: int
    created_by: str
    updated_by: str
    evidence: tuple[EvidenceRef, ...] = ()
    disputed: bool = False

    @property
    def authoritative(self) -> bool:
        return self.status is Status.CONFIRMED


class RelationRecord(BaseModel):
    model_config = F
    relation_id: str
    factory_id: str
    data: RelationData
    status: Status
    basis: Basis
    version: int
    created_seq: int
    updated_seq: int
    created_by: str
    updated_by: str
    evidence: tuple[EvidenceRef, ...] = ()
    disputed: bool = False

    @property
    def authoritative(self) -> bool:
        return self.status is Status.CONFIRMED


class DisputeRecord(BaseModel):
    model_config = F
    dispute_id: str
    factory_id: str
    subject_id: str
    reason: str
    raised_by: str
    raised_seq: int
    evidence: tuple[EvidenceRef, ...] = ()
    open: bool = True
    resolved_by: str | None = None
    resolved_seq: int | None = None
    resolution: str = ""

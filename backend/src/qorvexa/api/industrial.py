"""Industrial Decision Studio API.

Safety boundary: QORVEXA is advisory and read-only toward industrial systems.
This API orchestrates natural language intake, human fact confirmation,
formulation with dimensional units validation, admission gating,
real HiGHS optimization, scenario intelligence, and human approval audit history.
"""

from __future__ import annotations

import logging
from contextlib import closing
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from qorvexa.auth.deps import get_principal
from qorvexa.auth.principal import Principal as AuthPrincipal
from qorvexa.auth.principal import Role as AuthRole
from qorvexa.industrial.decision_intelligence import (
    DecisionConfig,
    MetricDirection,
    MetricObjective,
)
from qorvexa.industrial.decision_intelligence.explanation import DecisionBrief
from qorvexa.industrial.formulation import ActorKind, FormulationError
from qorvexa.industrial.formulation.analysis import FormulationResult, FormulationState
from qorvexa.industrial.governance import (
    AuthorizationError,
    DecisionError,
    ExtractedClaim,
    GovernedSessions,
    SessionError,
    SqliteStore,
    StoreError,
    Verdict,
)
from qorvexa.industrial.governance.authz import Principal as IndustrialPrincipal
from qorvexa.industrial.governance.authz import Role as IndustrialRole
from qorvexa.industrial.governance.decisions import DecisionHistory, DecisionRecord
from qorvexa.industrial.governance.gemini_extractor import (
    EXTRACTOR_SERVICE,
    EchoExtractor,
    ExtractionUnavailableError,
    GeminiExtractor,
)
from qorvexa.industrial.ontology.adapters import (
    ContextSnapshot,
    PlanningContext,
    UnderstandingLinks,
    link_report,
    planning_context,
    understanding_snapshot,
)
from qorvexa.industrial.ontology.integrity import IntegrityReport, verify_factory
from qorvexa.industrial.ontology.resolution import IdentityResolver
from qorvexa.industrial.ontology.schema import OntologyError
from qorvexa.industrial.ontology.service import FactoryOntology
from qorvexa.industrial.ontology.store import (
    OntologyStore,
    SqliteOntologyStore,
)
from qorvexa.industrial.ontology.store import (
    StoreError as OntologyStoreError,
)
from qorvexa.industrial.production_planning import ScenarioSpec
from qorvexa.industrial.production_planning.admission import AdmissionError, AdmissionPolicy
from qorvexa.industrial.understanding import (
    GeminiLLMProvider,
    LLMProvider,
    Readiness,
    SourceDocument,
    SqliteTerminologyStore,
    TerminologyBook,
    TerminologyError,
    UnderstandingInputError,
    UnderstandingReport,
    UnderstandingSession,
    emit_proposals,
)
from qorvexa.industrial.understanding.adapters import ProviderUnavailable
from qorvexa.industrial.understanding.clarification import ClarificationState
from qorvexa.industrial.understanding.models import Gap, Question
from qorvexa.industrial.understanding.terminology import TermEntry
from qorvexa.solver_fabric import LPOrchestrator
from qorvexa.solver_fabric.highs_adapter import HighsAdapter

log = logging.getLogger("qorvexa.api.industrial")

router = APIRouter(prefix="/api/v1/industrial", tags=["industrial"])

DEFAULT_ONTOLOGY_DB_PATH = Path("industrial_ontology.db")


def _require_factory_access(ip: IndustrialPrincipal, factory_id: str) -> None:
    """Ensure authenticated principal has explicit authorization for the requested factory."""
    if ip.factories is None or factory_id not in ip.factories:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=f"FACTORY_FORBIDDEN: Principal not authorized for factory {factory_id}",
        )


def to_industrial_principal(p: AuthPrincipal) -> IndustrialPrincipal:
    """Map authentication token principal to governed industrial principal."""
    roles: set[IndustrialRole] = set()
    if AuthRole.VIEWER in p.roles or AuthRole.AUDITOR in p.roles:
        roles.add(IndustrialRole.VIEWER)
    if AuthRole.ENGINEER in p.roles or AuthRole.ADMIN in p.roles:
        roles.add(IndustrialRole.PLANNER)
        roles.add(IndustrialRole.VIEWER)
    if AuthRole.APPROVER in p.roles:
        roles.add(IndustrialRole.APPROVER)
        roles.add(IndustrialRole.VIEWER)
    if AuthRole.TERMINOLOGY_REVIEWER in p.roles:
        roles.add(IndustrialRole.TERMINOLOGY_REVIEWER)
        roles.add(IndustrialRole.VIEWER)
    if not roles:
        roles.add(IndustrialRole.VIEWER)
    factories: set[str] = set(p.asset_ids)
    if p.tenant_id:
        factories.add(p.tenant_id)
    return IndustrialPrincipal(
        id=p.actor_id,
        kind=ActorKind.HUMAN,
        roles=frozenset(roles),
        factories=frozenset(factories) if factories else None,
    )


def get_governed_sessions(request: Request) -> GovernedSessions:
    """Retrieve or initialize the singleton GovernedSessions instance."""
    app_state = request.app.state
    if not hasattr(app_state, "governed_sessions") or app_state.governed_sessions is None:
        data_dir = app_state.settings.data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "industrial_governance.db"
        store = SqliteStore(db_path)
        orchestrator = LPOrchestrator(HighsAdapter())
        allow_self = app_state.settings.environment != "production"
        app_state.governed_sessions = GovernedSessions(
            store,
            orchestrator,
            policy=AdmissionPolicy.STRICT,
            allow_self_approval=allow_self,
        )
    return cast(GovernedSessions, app_state.governed_sessions)


def get_terminology_store(request: Request) -> SqliteTerminologyStore:
    """Retrieve or initialize the singleton SqliteTerminologyStore instance."""
    app_state = request.app.state
    if not hasattr(app_state, "terminology_store") or app_state.terminology_store is None:
        data_dir = app_state.settings.data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "industrial_terminology.db"
        app_state.terminology_store = SqliteTerminologyStore(db_path)
    return cast(SqliteTerminologyStore, app_state.terminology_store)


def get_ontology_store(request: Request) -> OntologyStore:
    """Retrieve or initialize the singleton OntologyStore instance."""
    app_state = request.app.state
    if not hasattr(app_state, "ontology_store") or app_state.ontology_store is None:
        data_dir = getattr(app_state, "settings", None) and getattr(
            app_state.settings, "data_dir", None
        )
        if data_dir:
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / "industrial_ontology.db"
        else:
            db_path = DEFAULT_ONTOLOGY_DB_PATH
        app_state.ontology_store = SqliteOntologyStore(db_path)
    return cast(OntologyStore, app_state.ontology_store)


def get_factory_ontology(request: Request, factory_id: str) -> FactoryOntology:
    """Retrieve or initialize the FactoryOntology for a specific factory."""
    app_state = request.app.state
    if not hasattr(app_state, "factory_ontologies") or app_state.factory_ontologies is None:
        app_state.factory_ontologies = {}
    if factory_id not in app_state.factory_ontologies:
        store = get_ontology_store(request)
        app_state.factory_ontologies[factory_id] = FactoryOntology(store, factory_id)
    return cast(FactoryOntology, app_state.factory_ontologies[factory_id])


def get_understanding_session(
    request: Request, session_id: str, factory_id: str
) -> UnderstandingSession:
    """Retrieve or create an UnderstandingSession for this session and factory."""
    app_state = request.app.state
    if not hasattr(app_state, "understanding_sessions") or app_state.understanding_sessions is None:
        app_state.understanding_sessions = {}
    key = (session_id, factory_id)
    if key not in app_state.understanding_sessions:
        store = get_terminology_store(request)
        book = TerminologyBook(store, factory_id)
        provider: LLMProvider | None = None
        gemini = GeminiLLMProvider()
        if gemini.is_configured:
            provider = gemini
        app_state.understanding_sessions[key] = UnderstandingSession(book, provider=provider)
    return cast(UnderstandingSession, app_state.understanding_sessions[key])


def _handle_domain_error(exc: Exception) -> HTTPException:
    if isinstance(exc, UnderstandingInputError):
        return HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if isinstance(exc, TerminologyError):
        if exc.code == "UNKNOWN_TERM":
            return HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc))
        if exc.code in {"VERSION_CONFLICT", "TERM_CONFLICT"}:
            return HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
        return HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if isinstance(exc, StoreError):
        if exc.code == "UNKNOWN_SESSION":
            return HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc))
        if exc.code in {"SESSION_EXISTS", "STALE_HEAD"}:
            return HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
        return HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if isinstance(exc, OntologyError):
        if exc.code in {"VERSION_CONFLICT", "REVISION_CONFLICT", "IDEMPOTENCY_CONFLICT"}:
            return HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
        if exc.code in {"FACTORY_FORBIDDEN", "HUMAN_APPROVAL_REQUIRED", "FORBIDDEN"}:
            return HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc))
        if exc.code in {"UNKNOWN_ENTITY", "UNKNOWN_RELATION", "UNKNOWN_LINE"}:
            return HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc))
        return HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if isinstance(exc, OntologyStoreError):
        if exc.code == "REVISION_CONFLICT":
            return HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
        return HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if isinstance(exc, AuthorizationError):
        return HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, (SessionError, FormulationError, DecisionError)):
        return HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if isinstance(exc, AdmissionError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    if isinstance(exc, (ExtractionUnavailableError, ProviderUnavailable)):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    log.exception("Unexpected error in industrial API: %s", exc)
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")


# ---- Request / Response Schemas ----


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=256)


class SessionSummary(BaseModel):
    id: str
    title: str
    created_by: str
    created_at: str


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=20000)
    claims: list[ExtractedClaim] | None = None
    factory_id: str | None = Field(default=None, max_length=64)
    speaker_role: str | None = Field(default=None, max_length=64)
    turn: int = Field(default=0, ge=0)
    line_id: str | None = Field(default=None, max_length=64)
    department: str | None = Field(default=None, max_length=64)
    default_currency: str | None = Field(default=None, max_length=16)


class IngestResponse(BaseModel):
    source_sha256: str
    accepted_count: int
    rejected_count: int
    accepted: list[ExtractedClaim]
    rejected: list[tuple[ExtractedClaim, str]]
    state: FormulationState
    understanding: UnderstandingReport | None = None
    readiness: Readiness | None = None
    questions: list[Question] = Field(default_factory=list)


class ProposeTermRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expression: str = Field(min_length=1, max_length=256)
    concept: str = Field(min_length=1, max_length=256)
    meaning: str = Field(default="", max_length=1000)
    entity_type: str | None = Field(default=None, max_length=64)
    canonical_id: str | None = Field(default=None, max_length=64)
    line_id: str | None = Field(default=None, max_length=64)
    department: str | None = Field(default=None, max_length=64)
    language: str = Field(default="", max_length=32)
    dialect: str = Field(default="", max_length=32)
    aliases: list[str] = Field(default_factory=list)
    spelling_variants: list[str] = Field(default_factory=list)


class TermNoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(default="", max_length=1000)


class CorrectTermRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(min_length=1, max_length=1000)
    meaning: str | None = None
    concept: str | None = None
    entity_type: str | None = None
    canonical_id: str | None = None
    line_id: str | None = None
    department: str | None = None
    aliases: list[str] | None = None
    spelling_variants: list[str] | None = None


class RollbackTermRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to_version: int = Field(ge=1)
    note: str = Field(min_length=1, max_length=1000)


class ClarificationsResponse(BaseModel):
    session_id: str
    clarification_state: ClarificationState
    questions: list[Question]
    gaps: list[Gap]


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keys: list[str] = Field(min_length=1, max_length=500)


class AssertFactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=256)
    value: str | int


class AssumeDefaultsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rationale: str = Field(min_length=1, max_length=1000)


class RetractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seq: int = Field(ge=0)


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    winner_seq: int = Field(ge=0)


class ConvertUnitsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot: str = Field(min_length=1, max_length=256)
    to_unit: str = Field(min_length=1, max_length=64)
    rationale: str = Field(min_length=1, max_length=1000)


class RunDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenarios: list[ScenarioSpec] = Field(default_factory=list)
    pareto_metrics: list[str] = Field(default_factory=lambda: ["objective", "total_volume"])


class DecideOnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brief_sha256: str = Field(min_length=64, max_length=64)
    verdict: Verdict
    scenario_id: str | None = None
    acknowledged: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=2000)


# ---- Endpoints ----


@router.get("/health")
def industrial_health(
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> dict[str, Any]:
    extractor = GeminiExtractor()
    return {
        "status": "ok",
        "solver": "HiGHS 1.16.0",
        "operating_mode": "advisory-read-only",
        "admission_policy": "STRICT",
        "gemini_extractor_available": extractor.is_available(),
    }


@router.get("/sessions", response_model=list[SessionSummary])
def list_sessions(
    request: Request,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> list[SessionSummary]:
    ip = to_industrial_principal(principal)
    ip.require(IndustrialRole.VIEWER)
    sessions = get_governed_sessions(request)
    store: SqliteStore = sessions._store
    with closing(store._conn()) as c:
        rows = c.execute(
            "SELECT id, title, created_by, created_at FROM sessions ORDER BY created_at DESC"
        ).fetchall()
    return [
        SessionSummary(
            id=r[0],
            title=r[1],
            created_by=r[2],
            created_at=r[3],
        )
        for r in rows
    ]


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def create_session(
    request: Request,
    body: CreateSessionRequest,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> dict[str, str]:
    ip = to_industrial_principal(principal)
    sessions = get_governed_sessions(request)
    try:
        sessions.create(ip, body.session_id, body.title)
    except Exception as exc:
        raise _handle_domain_error(exc) from exc
    return {"session_id": body.session_id, "title": body.title, "status": "created"}


@router.get("/sessions/{session_id}/state", response_model=FormulationState)
def get_session_state(
    request: Request,
    session_id: str,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> FormulationState:
    ip = to_industrial_principal(principal)
    sessions = get_governed_sessions(request)
    try:
        return sessions.state(ip, session_id)
    except Exception as exc:
        raise _handle_domain_error(exc) from exc


@router.post("/sessions/{session_id}/ingest", response_model=IngestResponse)
def ingest_text_claims(
    request: Request,
    session_id: str,
    body: IngestRequest,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> IngestResponse:
    ip = to_industrial_principal(principal)
    ip.require(IndustrialRole.PLANNER)
    sessions = get_governed_sessions(request)

    if body.claims is not None:
        extractor = EchoExtractor(body.claims)
        try:
            report = sessions.ingest(EXTRACTOR_SERVICE, session_id, body.text, extractor)
            updated_state = sessions.state(ip, session_id)
        except Exception as exc:
            raise _handle_domain_error(exc) from exc

        return IngestResponse(
            source_sha256=report.source_sha256,
            accepted_count=len(report.accepted),
            rejected_count=len(report.rejected),
            accepted=list(report.accepted),
            rejected=list(report.rejected),
            state=updated_state,
        )

    factory_id = body.factory_id or principal.tenant_id or "default-factory"
    _require_factory_access(ip, factory_id)

    try:
        understanding_session = get_understanding_session(request, session_id, factory_id)
        doc_id = f"{session_id}-turn-{body.turn}"
        doc = SourceDocument(
            source_id=doc_id,
            factory_id=factory_id,
            text=body.text,
            speaker_role=body.speaker_role,
            turn=body.turn,
            line_id=body.line_id,
            department=body.department,
            default_currency=body.default_currency,
        )
        und_report: UnderstandingReport = understanding_session.submit(doc)

        if not hasattr(request.app.state, "understanding_reports"):
            request.app.state.understanding_reports = {}
        request.app.state.understanding_reports[session_id] = und_report

        # Link report with Ontology IdentityResolver
        try:
            font = get_factory_ontology(request, factory_id)
            fstate = font.view(ip)
            book = TerminologyBook(get_terminology_store(request), factory_id)
            resolver = IdentityResolver(fstate, font.ontology, terms=book.entries())
            links = link_report(
                und_report, resolver, line_id=body.line_id, department=body.department
            )
            if not hasattr(request.app.state, "understanding_links"):
                request.app.state.understanding_links = {}
            request.app.state.understanding_links[session_id] = links
        except Exception as ont_exc:
            log.warning("Ontology linking skipped for session %s: %s", session_id, ont_exc)

        # Emit candidates as untrusted proposals to the session ledger
        store = sessions._store
        led = store.load_ledger(session_id)
        new_led, rejected_cands = emit_proposals(
            list(und_report.candidates),
            led,
            EXTRACTOR_SERVICE,
            source_ref=f"understanding:{und_report.report_sha256[:16]}",
        )
        store.save_ledger(session_id, new_led, led.head, sessions._clock())
        updated_state = sessions.state(ip, session_id)
    except Exception as exc:
        raise _handle_domain_error(exc) from exc

    rejected_cand_keys = {c.key for (c, _) in rejected_cands}
    accepted_claims = [
        ExtractedClaim(key=c.key, value=c.value, quote=c.quote)
        for c in und_report.candidates
        if c.status in {"SUPPORTED", "BLOCKING_FEATURE"} and c.key not in rejected_cand_keys
    ]
    rejected_claims = [
        (ExtractedClaim(key=c.key, value=c.value, quote=c.quote), reason)
        for (c, reason) in rejected_cands
    ]
    for c in und_report.candidates:
        if c.status not in {"SUPPORTED", "BLOCKING_FEATURE"}:
            rejected_claims.append(
                (ExtractedClaim(key=c.key, value=c.value, quote=c.quote), c.note or c.status)
            )

    return IngestResponse(
        source_sha256=und_report.source_sha256[0] if und_report.source_sha256 else "",
        accepted_count=len(accepted_claims),
        rejected_count=len(rejected_claims),
        accepted=accepted_claims,
        rejected=rejected_claims,
        state=updated_state,
        understanding=und_report,
        readiness=und_report.readiness,
        questions=list(und_report.questions),
    )


@router.post("/sessions/{session_id}/confirm", response_model=FormulationState)
def confirm_proposed_claims(
    request: Request,
    session_id: str,
    body: ConfirmRequest,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> FormulationState:
    ip = to_industrial_principal(principal)
    sessions = get_governed_sessions(request)
    try:
        return sessions.confirm(ip, session_id, body.keys)
    except Exception as exc:
        raise _handle_domain_error(exc) from exc


@router.post("/sessions/{session_id}/assert", response_model=FormulationState)
def assert_fact(
    request: Request,
    session_id: str,
    body: AssertFactRequest,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> FormulationState:
    ip = to_industrial_principal(principal)
    sessions = get_governed_sessions(request)
    try:
        return sessions.assert_fact(ip, session_id, body.key, body.value)
    except Exception as exc:
        raise _handle_domain_error(exc) from exc


@router.post("/sessions/{session_id}/assume", response_model=FormulationState)
def assume_default_values(
    request: Request,
    session_id: str,
    body: AssumeDefaultsRequest,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> FormulationState:
    ip = to_industrial_principal(principal)
    sessions = get_governed_sessions(request)
    try:
        return sessions.assume_defaults(ip, session_id, body.rationale)
    except Exception as exc:
        raise _handle_domain_error(exc) from exc


@router.post("/sessions/{session_id}/retract", response_model=FormulationState)
def retract_claim(
    request: Request,
    session_id: str,
    body: RetractRequest,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> FormulationState:
    ip = to_industrial_principal(principal)
    sessions = get_governed_sessions(request)
    try:
        return sessions.retract(ip, session_id, body.seq)
    except Exception as exc:
        raise _handle_domain_error(exc) from exc


@router.post("/sessions/{session_id}/resolve", response_model=FormulationState)
def resolve_contradiction(
    request: Request,
    session_id: str,
    body: ResolveRequest,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> FormulationState:
    ip = to_industrial_principal(principal)
    sessions = get_governed_sessions(request)
    try:
        return sessions.resolve(ip, session_id, body.winner_seq)
    except Exception as exc:
        raise _handle_domain_error(exc) from exc


@router.post("/sessions/{session_id}/convert-units", response_model=FormulationState)
def convert_planning_units(
    request: Request,
    session_id: str,
    body: ConvertUnitsRequest,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> FormulationState:
    ip = to_industrial_principal(principal)
    sessions = get_governed_sessions(request)
    try:
        return sessions.convert_units(ip, session_id, body.slot, body.to_unit, body.rationale)
    except Exception as exc:
        raise _handle_domain_error(exc) from exc


@router.post("/sessions/{session_id}/build", response_model=FormulationResult)
def build_formulation(
    request: Request,
    session_id: str,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> FormulationResult:
    ip = to_industrial_principal(principal)
    sessions = get_governed_sessions(request)
    try:
        return sessions.build(ip, session_id)
    except Exception as exc:
        raise _handle_domain_error(exc) from exc


@router.post("/sessions/{session_id}/decide", response_model=DecisionBrief)
def run_decision_intelligence(
    request: Request,
    session_id: str,
    body: RunDecisionRequest,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> DecisionBrief:
    ip = to_industrial_principal(principal)
    sessions = get_governed_sessions(request)
    metrics = [
        MetricObjective(metric=m, direction=MetricDirection.MAXIMIZE) for m in body.pareto_metrics
    ]
    cfg = DecisionConfig(pareto_objectives=tuple(metrics)) if metrics else None
    try:
        return sessions.run_decision(ip, session_id, body.scenarios, config=cfg)
    except Exception as exc:
        raise _handle_domain_error(exc) from exc


@router.post("/sessions/{session_id}/decide-on", response_model=DecisionRecord)
def approve_or_reject_decision(
    request: Request,
    session_id: str,
    body: DecideOnRequest,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> DecisionRecord:
    ip = to_industrial_principal(principal)
    sessions = get_governed_sessions(request)
    try:
        return sessions.decide_on(
            ip,
            session_id,
            brief_sha256=body.brief_sha256,
            verdict=body.verdict,
            scenario_id=body.scenario_id,
            acknowledged=frozenset(body.acknowledged),
            reason=body.reason,
        )
    except Exception as exc:
        raise _handle_domain_error(exc) from exc


@router.get("/sessions/{session_id}/history", response_model=DecisionHistory)
def get_session_history(
    request: Request,
    session_id: str,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> DecisionHistory:
    ip = to_industrial_principal(principal)
    ip.require(IndustrialRole.VIEWER)
    sessions = get_governed_sessions(request)
    try:
        return sessions.history(session_id)
    except Exception as exc:
        raise _handle_domain_error(exc) from exc


# ---- Understanding & Terminology Endpoints ----


@router.get("/sessions/{session_id}/understanding", response_model=UnderstandingReport)
def get_session_understanding(
    request: Request,
    session_id: str,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> UnderstandingReport:
    ip = to_industrial_principal(principal)
    ip.require(IndustrialRole.VIEWER)
    reports: dict[str, UnderstandingReport] = getattr(
        request.app.state, "understanding_reports", {}
    )
    if session_id not in reports:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"NO_UNDERSTANDING_REPORT: No report for session {session_id}",
        )
    rep = reports[session_id]
    _require_factory_access(ip, rep.factory_id)
    return rep


@router.get("/sessions/{session_id}/clarifications", response_model=ClarificationsResponse)
def get_session_clarifications(
    request: Request,
    session_id: str,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
    factory_id: str = "default-factory",
) -> ClarificationsResponse:
    ip = to_industrial_principal(principal)
    ip.require(IndustrialRole.VIEWER)
    _require_factory_access(ip, factory_id)
    reports: dict[str, UnderstandingReport] = getattr(
        request.app.state, "understanding_reports", {}
    )
    if session_id in reports:
        report = reports[session_id]
        questions = list(report.questions)
        gaps = list(report.gaps)
    else:
        questions = []
        gaps = []

    sessions_map: dict[tuple[str, str], UnderstandingSession] = getattr(
        request.app.state, "understanding_sessions", {}
    )
    key = (session_id, factory_id)
    if key in sessions_map:
        clar_state = sessions_map[key].clarification_state
    else:
        clar_state = ClarificationState()

    return ClarificationsResponse(
        session_id=session_id,
        clarification_state=clar_state,
        questions=questions,
        gaps=gaps,
    )


@router.get("/sessions/{session_id}/ontology-links", response_model=UnderstandingLinks)
def get_session_ontology_links(
    request: Request,
    session_id: str,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> UnderstandingLinks:
    ip = to_industrial_principal(principal)
    ip.require(IndustrialRole.VIEWER)
    links_map: dict[str, UnderstandingLinks] = getattr(request.app.state, "understanding_links", {})
    if session_id not in links_map:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"NO_ONTOLOGY_LINKS: No ontology links found for session {session_id}",
        )
    links = links_map[session_id]
    _require_factory_access(ip, links.factory_id)
    return links


@router.get("/ontology/{factory_id}/context", response_model=ContextSnapshot)
def get_ontology_context(
    request: Request,
    factory_id: str,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
    roots: str = "",
    max_nodes: int = 80,
) -> ContextSnapshot:
    ip = to_industrial_principal(principal)
    ip.require(IndustrialRole.VIEWER)
    _require_factory_access(ip, factory_id)
    try:
        font = get_factory_ontology(request, factory_id)
        fstate = font.view(ip)
        root_list = [r.strip() for r in roots.split(",") if r.strip()]
        if not root_list:
            root_list = list(fstate.entities.keys())[:20]
        return understanding_snapshot(fstate, font.ontology, root_list, max_nodes=max_nodes)
    except Exception as exc:
        raise _handle_domain_error(exc) from exc


@router.get("/ontology/{factory_id}/planning-candidates", response_model=PlanningContext)
def get_ontology_planning_candidates(
    request: Request,
    factory_id: str,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
    line_id: str | None = None,
    at: str | None = None,
) -> PlanningContext:
    ip = to_industrial_principal(principal)
    ip.require(IndustrialRole.PLANNER)
    _require_factory_access(ip, factory_id)
    try:
        font = get_factory_ontology(request, factory_id)
        fstate = font.view(ip)
        return planning_context(fstate, font.ontology, line_id=line_id, at=at)
    except Exception as exc:
        raise _handle_domain_error(exc) from exc


@router.get("/ontology/{factory_id}/verify", response_model=IntegrityReport)
def verify_factory_ontology(
    request: Request,
    factory_id: str,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> IntegrityReport:
    ip = to_industrial_principal(principal)
    ip.require(IndustrialRole.VIEWER)
    _require_factory_access(ip, factory_id)
    try:
        store = get_ontology_store(request)
        evs = store.events(factory_id)
        font = get_factory_ontology(request, factory_id)
        return verify_factory(factory_id, evs, font.ontology)
    except Exception as exc:
        raise _handle_domain_error(exc) from exc


@router.get("/terminology/{factory_id}", response_model=list[TermEntry])
def list_terms(
    request: Request,
    factory_id: str,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> list[TermEntry]:
    ip = to_industrial_principal(principal)
    ip.require(IndustrialRole.VIEWER)
    _require_factory_access(ip, factory_id)
    store = get_terminology_store(request)
    book = TerminologyBook(store, factory_id)
    try:
        return book.entries()
    except Exception as exc:
        raise _handle_domain_error(exc) from exc


@router.get("/terminology/{factory_id}/{term_id}", response_model=list[TermEntry])
def get_term_history(
    request: Request,
    factory_id: str,
    term_id: str,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> list[TermEntry]:
    ip = to_industrial_principal(principal)
    ip.require(IndustrialRole.VIEWER)
    _require_factory_access(ip, factory_id)
    store = get_terminology_store(request)
    book = TerminologyBook(store, factory_id)
    try:
        hist = book.history(term_id)
        if not hist:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail=f"UNKNOWN_TERM: Term {term_id} not found",
            )
        return hist
    except Exception as exc:
        raise _handle_domain_error(exc) from exc


@router.post(
    "/terminology/{factory_id}/propose",
    response_model=TermEntry,
    status_code=status.HTTP_201_CREATED,
)
def propose_term(
    request: Request,
    factory_id: str,
    body: ProposeTermRequest,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> TermEntry:
    ip = to_industrial_principal(principal)
    _require_factory_access(ip, factory_id)
    store = get_terminology_store(request)
    book = TerminologyBook(store, factory_id)
    kw: dict[str, Any] = {}
    if body.meaning:
        kw["meaning"] = body.meaning
    if body.entity_type:
        kw["entity_type"] = body.entity_type
    if body.canonical_id:
        kw["canonical_id"] = body.canonical_id
    if body.line_id:
        kw["line_id"] = body.line_id
    if body.department:
        kw["department"] = body.department
    if body.language:
        kw["language"] = body.language
    if body.dialect:
        kw["dialect"] = body.dialect
    if body.aliases:
        kw["aliases"] = tuple(body.aliases)
    if body.spelling_variants:
        kw["spelling_variants"] = tuple(body.spelling_variants)
    try:
        return book.propose(ip, body.expression, body.concept, **kw)
    except Exception as exc:
        raise _handle_domain_error(exc) from exc


@router.post("/terminology/{factory_id}/{term_id}/confirm", response_model=TermEntry)
def confirm_term(
    request: Request,
    factory_id: str,
    term_id: str,
    body: TermNoteRequest,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> TermEntry:
    ip = to_industrial_principal(principal)
    _require_factory_access(ip, factory_id)
    store = get_terminology_store(request)
    book = TerminologyBook(store, factory_id)
    try:
        return book.confirm(ip, term_id, note=body.note)
    except Exception as exc:
        raise _handle_domain_error(exc) from exc


@router.post("/terminology/{factory_id}/{term_id}/reject", response_model=TermEntry)
def reject_term(
    request: Request,
    factory_id: str,
    term_id: str,
    body: TermNoteRequest,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> TermEntry:
    ip = to_industrial_principal(principal)
    _require_factory_access(ip, factory_id)
    store = get_terminology_store(request)
    book = TerminologyBook(store, factory_id)
    try:
        return book.reject(ip, term_id, note=body.note)
    except Exception as exc:
        raise _handle_domain_error(exc) from exc


@router.post("/terminology/{factory_id}/{term_id}/correct", response_model=TermEntry)
def correct_term(
    request: Request,
    factory_id: str,
    term_id: str,
    body: CorrectTermRequest,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> TermEntry:
    ip = to_industrial_principal(principal)
    _require_factory_access(ip, factory_id)
    store = get_terminology_store(request)
    book = TerminologyBook(store, factory_id)
    changes: dict[str, Any] = {}
    if body.meaning is not None:
        changes["meaning"] = body.meaning
    if body.concept is not None:
        changes["concept"] = body.concept
    if body.entity_type is not None:
        changes["entity_type"] = body.entity_type
    if body.canonical_id is not None:
        changes["canonical_id"] = body.canonical_id
    if body.line_id is not None:
        changes["line_id"] = body.line_id
    if body.department is not None:
        changes["department"] = body.department
    if body.aliases is not None:
        changes["aliases"] = tuple(body.aliases)
    if body.spelling_variants is not None:
        changes["spelling_variants"] = tuple(body.spelling_variants)
    try:
        return book.correct(ip, term_id, note=body.note, **changes)
    except Exception as exc:
        raise _handle_domain_error(exc) from exc


@router.post("/terminology/{factory_id}/{term_id}/deprecate", response_model=TermEntry)
def deprecate_term(
    request: Request,
    factory_id: str,
    term_id: str,
    body: TermNoteRequest,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> TermEntry:
    ip = to_industrial_principal(principal)
    _require_factory_access(ip, factory_id)
    store = get_terminology_store(request)
    book = TerminologyBook(store, factory_id)
    try:
        return book.deprecate(ip, term_id, note=body.note)
    except Exception as exc:
        raise _handle_domain_error(exc) from exc


@router.post("/terminology/{factory_id}/{term_id}/rollback", response_model=TermEntry)
def rollback_term(
    request: Request,
    factory_id: str,
    term_id: str,
    body: RollbackTermRequest,
    principal: Annotated[AuthPrincipal, Depends(get_principal)],
) -> TermEntry:
    ip = to_industrial_principal(principal)
    _require_factory_access(ip, factory_id)
    store = get_terminology_store(request)
    book = TerminologyBook(store, factory_id)
    try:
        return book.rollback(ip, term_id, to_version=body.to_version, note=body.note)
    except Exception as exc:
        raise _handle_domain_error(exc) from exc

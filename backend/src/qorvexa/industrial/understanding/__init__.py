# ruff: noqa: E501
"""Industrial Understanding & Factory Language Intelligence (advisory, untrusted-by-design).

Understands factory communication (Egyptian/formal Arabic, English, mixed, Arabizi) at phrase and
context level, preserves source evidence, resolves factory-scoped terminology, extracts typed claims,
verifies them against the original source, finds contradictions and gaps, plans clarifications and
emits UNTRUSTED proposals for the formulation ledger. It never validates mathematics, never solves
and never approves anything.
"""

from qorvexa.industrial.understanding.adapters import (
    LLMProvider,
    OntologyAdapter,
    ProviderRequest,
    ProviderResponse,
    ProviderUnavailable,
    StaticOntology,
    UnavailableProvider,
    propose_terms_with_llm,
)
from qorvexa.industrial.understanding.clarification import ClarificationState
from qorvexa.industrial.understanding.gemini_provider import GeminiLLMProvider
from qorvexa.industrial.understanding.mapping import emit_proposals
from qorvexa.industrial.understanding.models import (
    SCHEMA_VERSION,
    Claim,
    Grounding,
    Readiness,
    SourceDocument,
    UnderstandingReport,
)
from qorvexa.industrial.understanding.pipeline import UnderstandingSession
from qorvexa.industrial.understanding.source import UnderstandingInputError
from qorvexa.industrial.understanding.terminology import (
    InMemoryTerminologyStore,
    ResolutionContext,
    SqliteTerminologyStore,
    TerminologyBook,
    TerminologyError,
    TermStatus,
)

__all__ = [
    "SCHEMA_VERSION",
    "Claim",
    "ClarificationState",
    "Grounding",
    "GeminiLLMProvider",
    "InMemoryTerminologyStore",
    "LLMProvider",
    "OntologyAdapter",
    "ProviderRequest",
    "ProviderResponse",
    "ProviderUnavailable",
    "Readiness",
    "ResolutionContext",
    "SourceDocument",
    "SqliteTerminologyStore",
    "StaticOntology",
    "TermStatus",
    "TerminologyBook",
    "TerminologyError",
    "UnavailableProvider",
    "UnderstandingInputError",
    "UnderstandingReport",
    "UnderstandingSession",
    "emit_proposals",
    "propose_terms_with_llm",
]

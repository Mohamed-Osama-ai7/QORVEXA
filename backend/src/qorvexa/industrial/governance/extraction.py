"""Natural-language intake: any extractor (LLM or rules) yields UNTRUSTED, quote-grounded claims.

Mechanical grounding: a claim is accepted as a *proposal* only if its quote occurs verbatim
(whitespace/case-insensitive) in the source text and, for numeric values, the number appears in
the quote. Ungrounded claims are reported and dropped. Accepted claims are PROPOSALS: they never
reach a model until a human confirms them.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from qorvexa.industrial.formulation import Actor, FormulationError, Ledger
from qorvexa.industrial.formulation.ledger import normalize_value, parse_key
from qorvexa.industrial.governance.authz import Principal, Role

MAX_TEXT = 20_000
MAX_CLAIMS = 500
MAX_QUOTE = 500
_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


class ExtractedClaim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    key: str
    value: str | int
    quote: str


class Extractor(Protocol):
    def extract(self, text: str) -> Sequence[ExtractedClaim]: ...


@dataclass(frozen=True)
class IngestReport:
    ledger: Ledger
    source_sha256: str
    accepted: tuple[ExtractedClaim, ...]
    rejected: tuple[tuple[ExtractedClaim, str], ...]


def _norm(s: str) -> str:
    return " ".join(s.split()).casefold()


def _numbers(s: str) -> set[Decimal]:
    out: set[Decimal] = set()
    for m in _NUM.findall(s):
        try:
            out.add(Decimal(m.replace(",", ".")))
        except InvalidOperation:  # pragma: no cover - regex guarantees a number
            continue
    return out


def check_grounding(text: str, claim: ExtractedClaim) -> str | None:
    """None when grounded, else a stable rejection reason."""
    q = claim.quote.strip()
    if not q or len(q) > MAX_QUOTE:
        return "QUOTE_MISSING_OR_TOO_LONG"
    if _norm(q) not in _norm(text):
        return "QUOTE_NOT_IN_SOURCE"
    try:
        pk = parse_key(claim.key)
        value = normalize_value(pk, claim.value)
    except FormulationError as exc:
        return exc.code
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None  # text/enum value: grounded by its quote
    if number not in _numbers(q):
        return "VALUE_NOT_IN_QUOTE"
    return None


def ingest_text(
    ledger: Ledger, text: str, extractor: Extractor, principal: Principal
) -> IngestReport:
    """Run the extractor and append grounded claims as PROPOSALS (never as facts)."""
    principal.require(Role.EXTRACTOR)
    if not text.strip() or len(text) > MAX_TEXT:
        raise FormulationError("INVALID_SOURCE", f"source text must be 1..{MAX_TEXT} characters")
    claims = list(extractor.extract(text))
    if len(claims) > MAX_CLAIMS:
        raise FormulationError("TOO_MANY_CLAIMS", f"extractor returned more than {MAX_CLAIMS}")
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    actor = Actor(id=principal.id, kind=principal.kind)
    out = ledger
    ok: list[ExtractedClaim] = []
    bad: list[tuple[ExtractedClaim, str]] = []
    for c in claims:
        reason = check_grounding(text, c)
        if reason:
            bad.append((c, reason))
            continue
        out = out.propose(
            c.key, c.value, actor, source_ref=f"text:{sha[:16]}", quote=c.quote.strip()
        )
        ok.append(c)
    return IngestReport(out, sha, tuple(ok), tuple(bad))


def claims_from_json(items: Any) -> list[ExtractedClaim]:
    """Strictly parse an LLM's JSON output; anything malformed raises (never partially trusted)."""
    if not isinstance(items, list):
        raise FormulationError("INVALID_EXTRACTION", "extractor output must be a JSON list")
    try:
        return [ExtractedClaim.model_validate(i) for i in items]
    except ValueError as exc:
        raise FormulationError("INVALID_EXTRACTION", str(exc)[:300]) from exc

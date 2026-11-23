"""Append-only, hash-chained fact ledger for problem formulation.

Every statement that may enter a mathematical model is a *claim* with an actor and provenance.
Only a human can state a fact, confirm a proposal, approve an assumption, retract, or resolve a
contradiction. LLM/system actors can only *propose*, and a proposal never reaches the model
without an explicit human confirmation. The ledger is immutable (every operation returns a new
ledger) and ``verify()`` replays it, so a forged or edited ledger is rejected.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict

from qorvexa.industrial.production_planning.model import (
    ObjectiveMode,
    ResourceKind,
    _exact,
    _ident,
    _unit,
)

MAX_ENTRIES: Final = 5000
GENESIS: Final = "0" * 64
NONE_VALUE: Final = "NONE"  # explicit "no cap" for max_quantity


class FormulationError(ValueError):
    """Invalid ledger operation; ``code`` is stable and machine-readable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ActorKind(StrEnum):
    HUMAN = "HUMAN"
    LLM = "LLM"
    SYSTEM = "SYSTEM"


class Op(StrEnum):
    ASSERT = "ASSERT"  # a claim (basis says who stands behind it)
    CONFIRM = "CONFIRM"  # human confirms a PROPOSAL
    ASSUME = "ASSUME"  # human-approved closed-world assumption (allow-listed)
    RETRACT = "RETRACT"
    RESOLVE = "RESOLVE"  # human picks the winning claim among contradicting ones
    CONVERT = "CONVERT"  # human-approved exact unit conversion of one unit slot


class Basis(StrEnum):
    USER_STATEMENT = "USER_STATEMENT"  # a human states it as fact
    PROPOSAL = "PROPOSAL"  # extracted/suggested; untrusted until confirmed


class Actor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    kind: ActorKind


class Entry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    seq: int
    prev_sha256: str
    op: Op
    actor: Actor
    key: str | None = None
    value: str | None = None
    basis: Basis | None = None
    target: int | None = None
    supersedes: tuple[int, ...] = ()
    rationale: str = ""
    source_ref: str = ""
    quote: str = ""
    unit_from: str = ""  # CONVERT only
    factor: str = ""  # CONVERT only: exact "n/d", recomputed on replay
    at: str = ""  # CONVERT only: caller-supplied timestamp
    entry_sha256: str = ""


# ----------------------------------------------------------------------------- key grammar


@dataclass(frozen=True)
class ParsedKey:
    scope: str  # problem | product | resource | consumption | feature
    a: str = ""
    b: str = ""
    field: str = ""


PROBLEM_FIELDS: Final = ("id", "title", "mode", "currency", "period_label")
PRODUCT_FIELDS: Final = (
    "name",
    "unit",
    "value_per_unit",
    "min_quantity",
    "max_quantity",
    "value_unit",
    "bound_unit",
)
UNIT_TEXT_FIELDS: Final = ("unit", "value_unit", "bound_unit")
RESOURCE_FIELDS: Final = ("name", "kind", "unit", "capacity")

# the ONLY facts that may be assumed rather than stated, and the only value each may take
ASSUMABLE: Final = {
    ("product", "min_quantity"): "0",
    ("product", "max_quantity"): NONE_VALUE,
    ("consumption", ""): "0",
}


def parse_key(key: str) -> ParsedKey:
    parts = key.split(":")
    try:
        if parts[0] == "problem" and len(parts) == 2 and parts[1] in PROBLEM_FIELDS:
            return ParsedKey("problem", field=parts[1])
        if parts[0] == "product" and len(parts) == 3 and parts[2] in PRODUCT_FIELDS:
            return ParsedKey("product", _ident(parts[1]), field=parts[2])
        if parts[0] == "resource" and len(parts) == 3 and parts[2] in RESOURCE_FIELDS:
            return ParsedKey("resource", _ident(parts[1]), field=parts[2])
        if parts[0] == "consumption" and len(parts) == 3:
            return ParsedKey("consumption", _ident(parts[1]), _ident(parts[2]))
        if parts[0] == "consumption" and len(parts) == 4 and parts[3] == "unit":
            return ParsedKey("consumption", _ident(parts[1]), _ident(parts[2]), "unit")
        if parts[0] == "unitdef" and len(parts) == 2:
            return ParsedKey("unitdef", _ident(parts[1]))
        if parts[0] == "feature" and len(parts) == 2:
            return ParsedKey("feature", _ident(parts[1]))
    except ValueError:
        pass
    raise FormulationError("INVALID_KEY", f"unrecognized or malformed fact key {key!r}")


def _canon_decimal(d: Decimal) -> str:
    if d == 0:
        return "0"
    s = format(d.normalize(), "f")
    return s


def _text(v: Any, limit: int = 200) -> str:
    if not isinstance(v, str) or not v.strip() or len(v.strip()) > limit:
        raise ValueError(f"text must be a non-empty string of at most {limit} characters")
    return v.strip()


def normalize_value(pk: ParsedKey, raw: Any) -> str:
    """Validate and canonicalize a claimed value for its field (exact decimals only)."""
    try:
        if pk.scope == "feature":
            return _text(raw, 500)
        if pk.scope == "unitdef":
            if not isinstance(raw, str) or raw.count("|") != 1:
                raise ValueError("unit definition must be '<exact factor>|<unit expression>'")
            fac, expr = (x.strip() for x in raw.split("|"))
            d = _exact(fac)
            if d <= 0 or not expr or len(expr) > 200:
                raise ValueError("factor must be positive and expression 1..200 chars")
            return f"{_canon_decimal(d)}|{expr}"
        if pk.scope == "consumption" and pk.field == "unit":
            return _text(raw)
        if pk.scope == "consumption":
            d = _exact(raw)
            if d < 0:
                raise ValueError("amount per unit must be non-negative")
            return _canon_decimal(d)
        f = pk.field
        if f == "id":
            return _ident(raw)
        if f == "mode":
            return ObjectiveMode(str(raw).strip().upper()).value
        if f == "kind":
            return ResourceKind(str(raw).strip().upper()).value
        if f in ("value_unit", "bound_unit"):
            return _text(raw)
        if f in ("currency", "unit"):
            return _unit(raw)
        if f in ("title", "name"):
            return _text(raw)
        if f == "period_label":
            return _text(raw, 100)
        if f == "max_quantity" and isinstance(raw, str) and raw.strip().upper() == NONE_VALUE:
            return NONE_VALUE
        d = _exact(raw)
        if f != "value_per_unit" and d < 0:
            raise ValueError("value must be non-negative")
        return _canon_decimal(d)
    except (ValueError, KeyError) as exc:
        raise FormulationError("INVALID_VALUE", f"{pk}: {exc}") from exc


# ----------------------------------------------------------------------------- hashing


def entry_digest(e: Entry) -> str:
    # new optional fields are hashed only when set, so pre-existing ledgers keep their hashes
    skip = {"entry_sha256"} | {f for f in ("unit_from", "factor", "at") if not getattr(e, f)}
    body = e.model_dump(mode="json", exclude=skip)
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _human(actor_id: str) -> Actor:
    if not isinstance(actor_id, str) or not actor_id.strip():
        raise FormulationError("INVALID_ACTOR", "a human actor id is required")
    return Actor(id=actor_id.strip(), kind=ActorKind.HUMAN)


@dataclass(frozen=True)
class Ledger:
    entries: tuple[Entry, ...] = ()

    @property
    def head(self) -> str:
        return self.entries[-1].entry_sha256 if self.entries else GENESIS

    def get(self, seq: int) -> Entry:
        if not 1 <= seq <= len(self.entries):
            raise FormulationError("UNKNOWN_ENTRY", f"no ledger entry {seq}")
        return self.entries[seq - 1]

    # ---- state helpers (pure)
    def retracted(self) -> frozenset[int]:
        return frozenset(e.target for e in self.entries if e.op is Op.RETRACT and e.target)

    def superseded(self) -> frozenset[int]:
        return frozenset(s for e in self.entries if e.op is Op.RESOLVE for s in e.supersedes)

    def confirmed(self) -> frozenset[int]:
        gone = self.retracted()
        return frozenset(
            e.target for e in self.entries if e.op is Op.CONFIRM and e.target and e.seq not in gone
        )

    def is_active(self, seq: int) -> bool:
        return seq not in self.retracted() and seq not in self.superseded()

    # ---- appending
    def _append(self, **fields: Any) -> Ledger:
        if len(self.entries) >= MAX_ENTRIES:
            raise FormulationError("LEDGER_FULL", f"ledger exceeds {MAX_ENTRIES} entries")
        seq = len(self.entries) + 1
        draft = Entry(seq=seq, prev_sha256=self.head, **fields)
        entry = draft.model_copy(update={"entry_sha256": entry_digest(draft)})
        return Ledger((*self.entries, entry))

    def _claim(
        self, op: Op, actor: Actor, key: str, value: Any, basis: Basis | None, **extra: str
    ) -> Ledger:
        pk = parse_key(key)
        norm = normalize_value(pk, value)
        if op is Op.ASSUME:
            allowed = ASSUMABLE.get((pk.scope, pk.field))
            if allowed is None:
                raise FormulationError(
                    "ASSUMPTION_NOT_ALLOWED",
                    f"{key} cannot be assumed; it must be stated by a human",
                )
            if norm != allowed:
                raise FormulationError(
                    "ASSUMPTION_VALUE_NOT_ALLOWED", f"{key} may only be assumed as {allowed!r}"
                )
        return self._append(op=op, actor=actor, key=key, value=norm, basis=basis, **extra)

    def assert_fact(
        self, key: str, value: Any, actor_id: str, *, source_ref: str = "", quote: str = ""
    ) -> Ledger:
        """A human states a fact (authoritative)."""
        return self._claim(
            Op.ASSERT,
            _human(actor_id),
            key,
            value,
            Basis.USER_STATEMENT,
            source_ref=source_ref,
            quote=quote,
        )

    def propose(
        self, key: str, value: Any, actor: Actor, *, source_ref: str = "", quote: str = ""
    ) -> Ledger:
        """Any actor may suggest a value; it is untrusted until a human confirms it."""
        return self._claim(
            Op.ASSERT, actor, key, value, Basis.PROPOSAL, source_ref=source_ref, quote=quote
        )

    def assume(self, key: str, value: Any, actor_id: str, rationale: str) -> Ledger:
        if len(rationale.strip()) < 10:
            raise FormulationError("RATIONALE_REQUIRED", "an assumption needs a written rationale")
        return self._claim(
            Op.ASSUME, _human(actor_id), key, value, None, rationale=rationale.strip()
        )

    def confirm(self, seq: int, actor_id: str) -> Ledger:
        target = self.get(seq)
        if target.op is not Op.ASSERT or target.basis is not Basis.PROPOSAL:
            raise FormulationError("NOT_A_PROPOSAL", f"entry {seq} is not a proposal")
        if not self.is_active(seq):
            raise FormulationError("INACTIVE_ENTRY", f"entry {seq} is retracted or superseded")
        return self._append(op=Op.CONFIRM, actor=_human(actor_id), key=target.key, target=seq)

    def retract(self, seq: int, actor_id: str) -> Ledger:
        target = self.get(seq)
        if target.op is Op.RETRACT or seq in self.retracted():
            raise FormulationError("INACTIVE_ENTRY", f"entry {seq} is already retracted")
        return self._append(op=Op.RETRACT, actor=_human(actor_id), key=target.key, target=seq)

    def resolve(self, winner: int, actor_id: str) -> Ledger:
        """Pick the winning claim; every other active claim for the same key is superseded."""
        w = self.get(winner)
        if w.op not in (Op.ASSERT, Op.ASSUME) or not self.is_active(winner):
            raise FormulationError("INACTIVE_ENTRY", f"entry {winner} is not an active claim")
        losers = tuple(
            e.seq
            for e in self.entries
            if e.op in (Op.ASSERT, Op.ASSUME)
            and e.key == w.key
            and e.seq != winner
            and self.is_active(e.seq)
        )
        if not losers:
            raise FormulationError("NOTHING_TO_RESOLVE", f"no competing claims for {w.key}")
        return self._append(
            op=Op.RESOLVE, actor=_human(actor_id), key=w.key, target=winner, supersedes=losers
        )

    def verify(self) -> None:
        """Replay the chain: hashes, ordering, and every operation's rules are re-checked."""
        replay = Ledger()
        for e in self.entries:
            replay = replay._replay(e)
        if replay.entries != self.entries:
            raise FormulationError("LEDGER_TAMPERED", "ledger content differs from its replay")

    def _replay(self, e: Entry) -> Ledger:
        def bad(why: str) -> FormulationError:
            return FormulationError("LEDGER_TAMPERED", f"entry {e.seq}: {why}")

        if e.seq != len(self.entries) + 1 or e.prev_sha256 != self.head:
            raise bad("broken chain")
        if e.entry_sha256 != entry_digest(e):
            raise bad("hash mismatch")
        try:
            if e.op is Op.ASSERT:
                if e.key is None or e.value is None or e.basis is None:
                    raise bad("incomplete claim")
                if e.basis is Basis.USER_STATEMENT and e.actor.kind is not ActorKind.HUMAN:
                    raise bad("a user statement must come from a human actor")
                nxt = self._claim(
                    e.op,
                    e.actor,
                    e.key,
                    e.value,
                    e.basis,
                    source_ref=e.source_ref,
                    quote=e.quote,
                )
            elif e.op is Op.ASSUME:
                if e.key is None or e.value is None:
                    raise bad("incomplete claim")
                nxt = self.assume(e.key, e.value, self._human_id(e), e.rationale)
            elif e.op is Op.CONVERT:
                from qorvexa.industrial.formulation.unitflow import verify_conversion

                verify_conversion(self, e)
                nxt = self._append(
                    op=Op.CONVERT,
                    actor=e.actor,
                    key=e.key,
                    value=e.value,
                    unit_from=e.unit_from,
                    factor=e.factor,
                    at=e.at,
                    rationale=e.rationale,
                )
            elif e.op is Op.CONFIRM:
                nxt = self.confirm(e.target or 0, self._human_id(e))
            elif e.op is Op.RETRACT:
                nxt = self.retract(e.target or 0, self._human_id(e))
            else:
                nxt = self.resolve(e.target or 0, self._human_id(e))
        except FormulationError as exc:
            if exc.code == "LEDGER_TAMPERED":
                raise
            raise bad(exc.message) from exc
        return nxt

    @staticmethod
    def _human_id(e: Entry) -> str:
        if e.actor.kind is not ActorKind.HUMAN:
            raise FormulationError("LEDGER_TAMPERED", f"entry {e.seq}: operation needs a human")
        return e.actor.id

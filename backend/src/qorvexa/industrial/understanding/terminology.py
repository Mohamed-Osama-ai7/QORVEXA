"""Factory-scoped terminology: versioned, human-confirmed, auditable, strictly tenant-isolated.

LLMs/systems may only PROPOSE. Only a human TERMINOLOGY_REVIEWER scoped to the factory can
confirm, reject, deprecate, correct or roll back. Every change appends a version with a history
event (nothing is overwritten). All store access is keyed by factory id; there is no cross-factory
read path. Resolution uses factory, line, department, machine ids, speaker role and surrounding
text; it never trusts a proposed entry.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from qorvexa.industrial.governance.authz import AuthorizationError, Principal, Role
from qorvexa.industrial.understanding.source import normalize

F = ConfigDict(frozen=True, extra="forbid")
_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,39}$")


class TerminologyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class TermStatus(StrEnum):
    PROPOSED = "PROPOSED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    DEPRECATED = "DEPRECATED"


class Alternative(BaseModel):
    """One possible meaning of an ambiguous expression and the context cues that select it."""

    model_config = F
    concept: str
    meaning: str = ""
    cues: tuple[str, ...] = ()  # words in the surrounding text that favour this meaning


class TermEvent(BaseModel):
    model_config = F
    version: int
    action: str  # PROPOSE | CONFIRM | REJECT | DEPRECATE | CORRECT | ROLLBACK
    actor: str
    at: str
    note: str = ""
    from_status: str = ""


class TermEntry(BaseModel):
    model_config = F
    term_id: str
    factory_id: str
    expression: str
    aliases: tuple[str, ...] = ()
    spelling_variants: tuple[str, ...] = ()
    concept: str  # canonical concept / ontology reference
    meaning: str = ""
    entity_type: str | None = None  # MACHINE | LINE | PRODUCT | MATERIAL | RESOURCE | ...
    canonical_id: str | None = None  # stable identifier for entity-type terms
    line_id: str | None = None
    department: str | None = None
    language: str = ""
    dialect: str = ""
    source: str = ""
    status: TermStatus = TermStatus.PROPOSED
    version: int = 1
    reviewer: str | None = None
    alternatives: tuple[Alternative, ...] = ()
    history: tuple[TermEvent, ...] = ()

    def surfaces(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {surface_key(s) for s in (self.expression, *self.aliases, *self.spelling_variants)}
            )
        )


def surface_key(text: str) -> str:
    return normalize(text).text


class TerminologyStore(Protocol):
    def next_id(self, factory_id: str) -> str: ...
    def append(self, entry: TermEntry) -> None: ...
    def versions(self, factory_id: str, term_id: str) -> list[TermEntry]: ...
    def latest_all(self, factory_id: str) -> list[TermEntry]: ...


class InMemoryTerminologyStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], list[TermEntry]] = {}

    def next_id(self, factory_id: str) -> str:
        n = sum(1 for (f, _t) in self._rows if f == factory_id) + 1
        return f"T-{n:04d}"

    def append(self, entry: TermEntry) -> None:
        rows = self._rows.setdefault((entry.factory_id, entry.term_id), [])
        if entry.version != len(rows) + 1:
            raise TerminologyError("VERSION_CONFLICT", "stale terminology write")
        rows.append(entry)

    def versions(self, factory_id: str, term_id: str) -> list[TermEntry]:
        return list(self._rows.get((factory_id, term_id), []))

    def latest_all(self, factory_id: str) -> list[TermEntry]:
        return [v[-1] for (f, _t), v in sorted(self._rows.items()) if f == factory_id]


class SqliteTerminologyStore:
    """stdlib SQLite, same style as the governance store; its own table, factory-keyed."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        with closing(self._conn()) as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS term_versions(factory_id TEXT NOT NULL,"
                " term_id TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,"
                " PRIMARY KEY(factory_id, term_id, version))"
            )

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self._path, isolation_level=None)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def next_id(self, factory_id: str) -> str:
        with closing(self._conn()) as c:
            n = c.execute(
                "SELECT COUNT(DISTINCT term_id) FROM term_versions WHERE factory_id=?",
                (factory_id,),
            ).fetchone()[0]
        return f"T-{n + 1:04d}"

    def append(self, entry: TermEntry) -> None:
        with closing(self._conn()) as c:
            try:
                c.execute(
                    "INSERT INTO term_versions VALUES(?,?,?,?)",
                    (entry.factory_id, entry.term_id, entry.version, entry.model_dump_json()),
                )
            except sqlite3.IntegrityError as exc:
                raise TerminologyError("VERSION_CONFLICT", "stale terminology write") from exc

    def versions(self, factory_id: str, term_id: str) -> list[TermEntry]:
        with closing(self._conn()) as c:
            rows = c.execute(
                "SELECT body FROM term_versions WHERE factory_id=? AND term_id=? ORDER BY version",
                (factory_id, term_id),
            ).fetchall()
        out = [TermEntry.model_validate_json(r[0]) for r in rows]
        if any(e.factory_id != factory_id or e.term_id != term_id for e in out):
            raise TerminologyError("STORE_TAMPERED", "row does not belong to its key")
        return out

    def latest_all(self, factory_id: str) -> list[TermEntry]:
        with closing(self._conn()) as c:
            rows = c.execute(
                "SELECT body FROM term_versions t WHERE factory_id=? AND version="
                "(SELECT MAX(version) FROM term_versions WHERE factory_id=t.factory_id"
                " AND term_id=t.term_id) ORDER BY term_id",
                (factory_id,),
            ).fetchall()
        out = [TermEntry.model_validate_json(r[0]) for r in rows]
        if any(e.factory_id != factory_id for e in out):
            raise TerminologyError("STORE_TAMPERED", "cross-factory row detected")
        return out


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class TerminologyBook:
    """All terminology operations for ONE factory. A different factory needs a different book."""

    def __init__(
        self, store: TerminologyStore, factory_id: str, *, clock: Callable[[], str] = _now
    ) -> None:
        if not factory_id.strip():
            raise TerminologyError("INVALID_FACTORY", "factory id is required")
        self._store, self.factory_id, self._clock = store, factory_id, clock

    # ---- authorization
    def _scoped(self, p: Principal) -> None:
        if p.factories is None or self.factory_id not in p.factories:
            raise AuthorizationError(
                "FACTORY_FORBIDDEN", f"{p.id} is not authorized for factory {self.factory_id}"
            )

    def _reviewer(self, p: Principal) -> None:
        self._scoped(p)
        p.require(Role.TERMINOLOGY_REVIEWER)

    # ---- reads
    def entries(self) -> list[TermEntry]:
        return self._store.latest_all(self.factory_id)

    def get(self, term_id: str) -> TermEntry:
        v = self._store.versions(self.factory_id, term_id)
        if not v:
            raise TerminologyError("UNKNOWN_TERM", term_id)
        return v[-1]

    def history(self, term_id: str) -> list[TermEntry]:
        return self._store.versions(self.factory_id, term_id)

    def snapshot_sha256(self) -> str:
        """Content identity of the dictionary. Timestamps are excluded on purpose: wall-clock
        times must never change an understanding report."""
        body = [
            e.model_dump(mode="json", exclude={"history": {"__all__": {"at"}}})
            for e in self.entries()
        ]
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()

    # ---- writes
    def propose(
        self,
        p: Principal,
        expression: str,
        concept: str,
        *,
        meaning: str = "",
        source: str = "",
        **fields: object,
    ) -> TermEntry:
        """Any scoped actor (human or service) may propose; the result is never trusted."""
        self._scoped(p)
        if not p.roles & {Role.PLANNER, Role.EXTRACTOR, Role.TERMINOLOGY_REVIEWER}:
            raise AuthorizationError("FORBIDDEN", f"{p.id} may not propose terminology")
        if not expression.strip() or not concept.strip():
            raise TerminologyError("INVALID_TERM", "expression and concept are required")
        cid = fields.get("canonical_id")
        if cid is not None and not _IDENT.match(str(cid)):
            raise TerminologyError("INVALID_TERM", "canonical_id must be a valid identifier")
        term_id = self._store.next_id(self.factory_id)
        entry = TermEntry(
            term_id=term_id,
            factory_id=self.factory_id,
            expression=expression.strip(),
            concept=concept.strip(),
            meaning=meaning,
            source=source,
            status=TermStatus.PROPOSED,
            history=(TermEvent(version=1, action="PROPOSE", actor=p.id, at=self._clock()),),
            **fields,
        )
        self._store.append(entry)
        return entry

    def _conflicts(self, e: TermEntry) -> list[TermEntry]:
        mine = set(e.surfaces())
        return [
            o
            for o in self.entries()
            if o.term_id != e.term_id
            and o.status is TermStatus.CONFIRMED
            and (o.line_id, o.department) == (e.line_id, e.department)
            and mine & set(o.surfaces())
            and o.concept != e.concept
        ]

    def _step(
        self,
        p: Principal,
        term_id: str,
        action: str,
        status: TermStatus,
        note: str,
        **changes: object,
    ) -> TermEntry:
        self._reviewer(p)
        cur = self.get(term_id)
        new = cur.model_copy(
            update={
                **changes,
                "status": status,
                "version": cur.version + 1,
                "reviewer": p.id,
                "history": (
                    *cur.history,
                    TermEvent(
                        version=cur.version + 1,
                        action=action,
                        actor=p.id,
                        at=self._clock(),
                        note=note,
                        from_status=cur.status.value,
                    ),
                ),
            }
        )
        if status is TermStatus.CONFIRMED:
            clash = self._conflicts(new)
            if clash:
                raise TerminologyError(
                    "TERM_CONFLICT",
                    f"{new.expression!r} already confirmed with another meaning in the same scope "
                    f"({[c.term_id for c in clash]}); deprecate it first",
                )
        self._store.append(new)
        return new

    def confirm(self, p: Principal, term_id: str, note: str = "") -> TermEntry:
        cur = self.get(term_id)
        if cur.status is not TermStatus.PROPOSED:
            raise TerminologyError("INVALID_TRANSITION", f"{cur.status.value} cannot be confirmed")
        return self._step(p, term_id, "CONFIRM", TermStatus.CONFIRMED, note)

    def reject(self, p: Principal, term_id: str, note: str = "") -> TermEntry:
        cur = self.get(term_id)
        if cur.status is not TermStatus.PROPOSED:
            raise TerminologyError("INVALID_TRANSITION", f"{cur.status.value} cannot be rejected")
        return self._step(p, term_id, "REJECT", TermStatus.REJECTED, note)

    def deprecate(self, p: Principal, term_id: str, note: str = "") -> TermEntry:
        cur = self.get(term_id)
        if cur.status is not TermStatus.CONFIRMED:
            raise TerminologyError("INVALID_TRANSITION", f"{cur.status.value} cannot be deprecated")
        return self._step(p, term_id, "DEPRECATE", TermStatus.DEPRECATED, note)

    def correct(self, p: Principal, term_id: str, note: str, **changes: object) -> TermEntry:
        """Reviewer-approved correction: a NEW confirmed version (the old one stays in history)."""
        if not note.strip():
            raise TerminologyError("NOTE_REQUIRED", "a correction needs a reason")
        forbidden = set(changes) & {"term_id", "factory_id", "status", "version", "history"}
        if forbidden:
            raise TerminologyError("INVALID_CHANGE", f"cannot change {sorted(forbidden)}")
        return self._step(p, term_id, "CORRECT", TermStatus.CONFIRMED, note, **changes)

    def rollback(self, p: Principal, term_id: str, to_version: int, note: str) -> TermEntry:
        """Reversible history: restore the content of an earlier version as a NEW version."""
        versions = self.history(term_id)
        if not 1 <= to_version <= len(versions) or not note.strip():
            raise TerminologyError("INVALID_ROLLBACK", "unknown version or missing reason")
        old = versions[to_version - 1]
        restore = {
            k: getattr(old, k)
            for k in (
                "expression", "aliases", "spelling_variants", "concept", "meaning", "entity_type",
                "canonical_id", "line_id", "department", "language", "dialect", "alternatives",
            )
        }  # fmt: skip
        return self._step(p, term_id, "ROLLBACK", old.status, note, **restore)


# --------------------------------------------------------------------------- resolution


class ResolutionContext(BaseModel):
    model_config = F
    factory_id: str
    line_id: str | None = None
    department: str | None = None
    machine_ids: tuple[str, ...] = ()
    speaker_role: str | None = None
    surrounding_text: str = ""


class ResolutionStatus(StrEnum):
    RESOLVED = "RESOLVED"  # exactly one CONFIRMED meaning applies
    AMBIGUOUS = "AMBIGUOUS"
    UNKNOWN = "UNKNOWN"
    PROPOSED_ONLY = "PROPOSED_ONLY"  # only unconfirmed proposals exist: untrusted


class TermResolution(BaseModel):
    model_config = F
    surface: str
    status: ResolutionStatus
    term_id: str | None = None
    concept: str | None = None
    entity_type: str | None = None
    canonical_id: str | None = None
    candidates: tuple[str, ...] = ()  # concepts / term ids that remain possible
    reason: str = ""


def resolve_surface(
    book_entries: list[TermEntry], surface: str, ctx: ResolutionContext
) -> TermResolution:
    key = surface_key(surface)
    here = [e for e in book_entries if e.factory_id == ctx.factory_id and key in e.surfaces()]
    if not here:
        return TermResolution(surface=surface, status=ResolutionStatus.UNKNOWN, reason="no entry")

    def applicable(e: TermEntry) -> int | None:
        spec = 0
        for mine, theirs in ((e.line_id, ctx.line_id), (e.department, ctx.department)):
            if mine is None:
                continue
            if theirs is None:
                continue  # scope cannot be checked: weaker match
            if mine != theirs:
                return None
            spec += 2
        return spec

    confirmed = [(applicable(e), e) for e in here if e.status is TermStatus.CONFIRMED]
    confirmed = [(s, e) for s, e in confirmed if s is not None]
    if confirmed:
        top = max(s for s, _ in confirmed if s is not None)
        best = [e for s, e in confirmed if s == top]
        # a line/department-scoped entry whose scope the context cannot confirm is not conclusive
        unverified = [
            e
            for e in best
            if (e.line_id and not ctx.line_id) or (e.department and not ctx.department)
        ]
        concepts = sorted({e.concept for e in best})
        if len(concepts) > 1:
            return TermResolution(
                surface=surface,
                status=ResolutionStatus.AMBIGUOUS,
                candidates=tuple(concepts),
                reason="several confirmed meanings with equal specificity",
            )
        e = best[0]
        if e.alternatives:
            text = surface_key(ctx.surrounding_text)
            hits = [a for a in e.alternatives if any(surface_key(c) in text for c in a.cues if c)]
            if len(hits) == 1:
                return TermResolution(
                    surface=surface, status=ResolutionStatus.RESOLVED, term_id=e.term_id,
                    concept=hits[0].concept, entity_type=e.entity_type, canonical_id=e.canonical_id,
                    reason="resolved by surrounding-text cue",
                )  # fmt: skip
            return TermResolution(
                surface=surface,
                status=ResolutionStatus.AMBIGUOUS,
                term_id=e.term_id,
                candidates=tuple(sorted({e.concept, *(a.concept for a in e.alternatives)})),
                reason="declared ambiguous; context does not select exactly one meaning",
            )
        if unverified and len(best) == len(unverified):
            return TermResolution(
                surface=surface,
                status=ResolutionStatus.AMBIGUOUS,
                term_id=e.term_id,
                candidates=(e.concept,),
                reason="entry is line/department-scoped and the context does not state its scope",
            )
        return TermResolution(
            surface=surface, status=ResolutionStatus.RESOLVED, term_id=e.term_id, concept=e.concept,
            entity_type=e.entity_type, canonical_id=e.canonical_id,
        )  # fmt: skip
    proposed = [e for e in here if e.status is TermStatus.PROPOSED]
    if proposed:
        return TermResolution(
            surface=surface,
            status=ResolutionStatus.PROPOSED_ONLY,
            candidates=tuple(sorted({e.concept for e in proposed})),
            reason="only unconfirmed proposals exist",
        )
    return TermResolution(
        surface=surface, status=ResolutionStatus.UNKNOWN, reason="rejected or deprecated only"
    )

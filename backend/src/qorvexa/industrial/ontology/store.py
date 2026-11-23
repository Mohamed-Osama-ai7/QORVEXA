"""Repository contract and implementations for the append-only ontology event log.

Every call is keyed by factory id; there is no cross-factory read path. ``append`` is atomic and
optimistic: it succeeds only if the factory's current revision equals ``expected_revision``.
The SQLite store owns its own table (``ontology_events``) and never touches governance tables.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Protocol

from qorvexa.industrial.ontology.model import Event


class StoreError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class OntologyStore(Protocol):
    def events(self, factory_id: str) -> list[Event]: ...
    def revision(self, factory_id: str) -> int: ...
    def append(self, factory_id: str, events: list[Event], expected_revision: int) -> None: ...


def _check_batch(factory_id: str, events: list[Event], expected_revision: int) -> None:
    for i, ev in enumerate(events):
        if ev.factory_id != factory_id:
            raise StoreError("FACTORY_MISMATCH", f"event for {ev.factory_id} sent to {factory_id}")
        if ev.seq != expected_revision + 1 + i:
            raise StoreError("BAD_SEQUENCE", f"expected seq {expected_revision + 1 + i}")


class InMemoryOntologyStore:
    def __init__(self) -> None:
        self._log: dict[str, list[Event]] = {}

    def events(self, factory_id: str) -> list[Event]:
        return list(self._log.get(factory_id, []))

    def revision(self, factory_id: str) -> int:
        return len(self._log.get(factory_id, []))

    def append(self, factory_id: str, events: list[Event], expected_revision: int) -> None:
        if self.revision(factory_id) != expected_revision:
            raise StoreError(
                "REVISION_CONFLICT",
                f"expected revision {expected_revision}, store is at {self.revision(factory_id)}",
            )
        _check_batch(factory_id, events, expected_revision)
        self._log.setdefault(factory_id, []).extend(events)

    def tamper(self, factory_id: str, seq: int, ev: Event) -> None:  # test hook only
        self._log[factory_id][seq - 1] = ev


class SqliteOntologyStore:
    """Durable store. WAL journal; one transaction per append; a primary key on (factory, seq)
    makes a lost optimistic-concurrency race fail at the database level as well."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        with closing(self._conn()) as c, c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS ontology_events ("
                "factory_id TEXT NOT NULL, seq INTEGER NOT NULL, sha TEXT NOT NULL, "
                "body TEXT NOT NULL, PRIMARY KEY (factory_id, seq))"
            )

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self._path, timeout=10, isolation_level=None)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def events(self, factory_id: str) -> list[Event]:
        with closing(self._conn()) as c:
            rows = c.execute(
                "SELECT seq, sha, body FROM ontology_events WHERE factory_id=? ORDER BY seq",
                (factory_id,),
            ).fetchall()
        out = []
        for seq, sha, body in rows:
            ev = Event.model_validate_json(body)
            if ev.seq != seq or ev.factory_id != factory_id or ev.sha != sha:
                raise StoreError("ROW_TAMPERED", f"row ({factory_id},{seq}) does not match its key")
            out.append(ev)
        return out

    def revision(self, factory_id: str) -> int:
        with closing(self._conn()) as c:
            row = c.execute(
                "SELECT COALESCE(MAX(seq),0) FROM ontology_events WHERE factory_id=?",
                (factory_id,),
            ).fetchone()
        return int(row[0])

    def append(self, factory_id: str, events: list[Event], expected_revision: int) -> None:
        _check_batch(factory_id, events, expected_revision)
        with closing(self._conn()) as c:
            try:
                c.execute("BEGIN IMMEDIATE")
                cur = c.execute(
                    "SELECT COALESCE(MAX(seq),0) FROM ontology_events WHERE factory_id=?",
                    (factory_id,),
                ).fetchone()[0]
                if cur != expected_revision:
                    c.execute("ROLLBACK")
                    raise StoreError(
                        "REVISION_CONFLICT",
                        f"expected revision {expected_revision}, store at {cur}",
                    )
                for ev in events:
                    c.execute(
                        "INSERT INTO ontology_events(factory_id, seq, sha, body) VALUES (?,?,?,?)",
                        (factory_id, ev.seq, ev.sha, ev.model_dump_json()),
                    )
                c.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                c.execute("ROLLBACK")
                raise StoreError("REVISION_CONFLICT", "concurrent write won the race") from exc

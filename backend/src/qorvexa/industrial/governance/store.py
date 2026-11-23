"""SQLite persistence (stdlib). Everything loaded is re-verified; writes use optimistic heads."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

from qorvexa.industrial.decision_intelligence.explanation import DecisionBrief
from qorvexa.industrial.formulation import Entry, FormulationError, Ledger
from qorvexa.industrial.governance.decisions import (
    DecisionError,
    DecisionHistory,
    DecisionRecord,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, title TEXT NOT NULL,
  created_by TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ledger_entries(session_id TEXT NOT NULL, seq INTEGER NOT NULL,
  body TEXT NOT NULL, recorded_at TEXT NOT NULL, PRIMARY KEY(session_id, seq));
CREATE TABLE IF NOT EXISTS briefs(session_id TEXT NOT NULL, brief_sha256 TEXT NOT NULL,
  formulation_record_sha256 TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
  body TEXT NOT NULL, PRIMARY KEY(session_id, brief_sha256));
CREATE TABLE IF NOT EXISTS decisions(session_id TEXT NOT NULL, seq INTEGER NOT NULL,
  body TEXT NOT NULL, PRIMARY KEY(session_id, seq));
"""


class StoreError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class SqliteStore:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        with closing(self._conn()) as c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self._path, isolation_level=None)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def create_session(self, sid: str, title: str, created_by: str, now: str) -> None:
        with closing(self._conn()) as c:
            try:
                c.execute("INSERT INTO sessions VALUES(?,?,?,?)", (sid, title, created_by, now))
            except sqlite3.IntegrityError as exc:
                raise StoreError("SESSION_EXISTS", sid) from exc

    def _require(self, c: sqlite3.Connection, sid: str) -> None:
        if c.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone() is None:
            raise StoreError("UNKNOWN_SESSION", sid)

    def load_ledger(self, sid: str) -> Ledger:
        with closing(self._conn()) as c:
            self._require(c, sid)
            rows = c.execute(
                "SELECT body FROM ledger_entries WHERE session_id=? ORDER BY seq", (sid,)
            ).fetchall()
        ledger = Ledger(tuple(Entry.model_validate_json(r[0]) for r in rows))
        ledger.verify()  # raises FormulationError(LEDGER_TAMPERED)
        return ledger

    def save_ledger(self, sid: str, ledger: Ledger, expected_head: str, now: str) -> None:
        """Append the entries beyond the stored prefix; fails if the stored head moved."""
        with closing(self._conn()) as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                self._require(c, sid)
                row = c.execute(
                    "SELECT body FROM ledger_entries WHERE session_id=? ORDER BY seq DESC LIMIT 1",
                    (sid,),
                ).fetchone()
                stored = Entry.model_validate_json(row[0]).entry_sha256 if row else "0" * 64
                if stored != expected_head:
                    raise StoreError("STALE_HEAD", "ledger changed since it was read")
                n = c.execute(
                    "SELECT COUNT(*) FROM ledger_entries WHERE session_id=?", (sid,)
                ).fetchone()[0]
                for e in ledger.entries[n:]:
                    c.execute(
                        "INSERT INTO ledger_entries VALUES(?,?,?,?)",
                        (sid, e.seq, e.model_dump_json(), now),
                    )
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise

    def save_brief(
        self, sid: str, brief: DecisionBrief, formulation_sha: str, created_by: str, now: str
    ) -> None:
        with closing(self._conn()) as c:
            self._require(c, sid)
            c.execute(
                "INSERT OR IGNORE INTO briefs VALUES(?,?,?,?,?,?)",
                (
                    sid,
                    brief.brief_sha256,
                    formulation_sha,
                    created_by,
                    now,
                    brief.model_dump_json(),
                ),
            )

    def get_brief(self, sid: str, brief_sha: str) -> tuple[DecisionBrief, str, str]:
        with closing(self._conn()) as c:
            row = c.execute(
                "SELECT body, formulation_record_sha256, created_by FROM briefs "
                "WHERE session_id=? AND brief_sha256=?",
                (sid, brief_sha),
            ).fetchone()
        if row is None:
            raise StoreError("UNKNOWN_BRIEF", brief_sha)
        return DecisionBrief.model_validate_json(row[0]), row[1], row[2]

    def load_history(self, sid: str) -> DecisionHistory:
        with closing(self._conn()) as c:
            self._require(c, sid)
            rows = c.execute(
                "SELECT body FROM decisions WHERE session_id=? ORDER BY seq", (sid,)
            ).fetchall()
        h = DecisionHistory(tuple(DecisionRecord.model_validate_json(r[0]) for r in rows))
        h.verify()
        return h

    def save_history(self, sid: str, history: DecisionHistory, expected_head: str) -> None:
        with closing(self._conn()) as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                rows: Sequence[tuple[str]] = c.execute(
                    "SELECT body FROM decisions WHERE session_id=? ORDER BY seq", (sid,)
                ).fetchall()
                stored = (
                    DecisionRecord.model_validate_json(rows[-1][0]).record_sha256
                    if rows
                    else "0" * 64
                )
                if stored != expected_head:
                    raise StoreError("STALE_HEAD", "decision history changed since it was read")
                for r in history.records[len(rows) :]:
                    c.execute(
                        "INSERT INTO decisions VALUES(?,?,?)", (sid, r.seq, r.model_dump_json())
                    )
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise


__all__ = ["DecisionError", "FormulationError", "SqliteStore", "StoreError"]

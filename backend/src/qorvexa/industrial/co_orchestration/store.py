"""Persistent, hash-verified experiment history (own SQLite table ``osco_experiments``)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Protocol

from qorvexa.industrial.co_orchestration.contracts import DecisionPackage


class OscoStoreError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def package_sha256(p: DecisionPackage) -> str:
    body = p.model_dump(mode="json", exclude={"package_sha256"})
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def verify_package(p: DecisionPackage) -> bool:
    return p.package_sha256 == package_sha256(p)


class ExperimentStore(Protocol):
    def get(self, experiment_id: str) -> DecisionPackage | None: ...
    def save(self, package: DecisionPackage) -> None: ...


def _checked(p: DecisionPackage) -> DecisionPackage:
    if not verify_package(p):
        raise OscoStoreError("PACKAGE_TAMPERED", f"{p.experiment_id}: hash does not verify")
    return p


class InMemoryExperimentStore:
    def __init__(self) -> None:
        self._rows: dict[str, str] = {}

    def get(self, experiment_id: str) -> DecisionPackage | None:
        body = self._rows.get(experiment_id)
        return None if body is None else _checked(DecisionPackage.model_validate_json(body))

    def save(self, package: DecisionPackage) -> None:
        self._rows[package.experiment_id] = _checked(package).model_dump_json()

    def tamper(self, experiment_id: str, body: str) -> None:  # test hook
        self._rows[experiment_id] = body


class SqliteExperimentStore:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        with closing(self._conn()) as c, c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS osco_experiments (experiment_id TEXT PRIMARY KEY, "
                "package_sha256 TEXT NOT NULL, body TEXT NOT NULL)"
            )

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self._path, timeout=10)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def get(self, experiment_id: str) -> DecisionPackage | None:
        with closing(self._conn()) as c:
            row = c.execute(
                "SELECT package_sha256, body FROM osco_experiments WHERE experiment_id=?",
                (experiment_id,),
            ).fetchone()
        if row is None:
            return None
        p = DecisionPackage.model_validate_json(row[1])
        if p.package_sha256 != row[0] or p.experiment_id != experiment_id:
            raise OscoStoreError("ROW_TAMPERED", f"row for {experiment_id} does not match its key")
        return _checked(p)

    def save(self, package: DecisionPackage) -> None:
        _checked(package)
        with closing(self._conn()) as c, c:
            c.execute(
                "INSERT OR IGNORE INTO osco_experiments(experiment_id, package_sha256, body) "
                "VALUES (?,?,?)",
                (package.experiment_id, package.package_sha256, package.model_dump_json()),
            )

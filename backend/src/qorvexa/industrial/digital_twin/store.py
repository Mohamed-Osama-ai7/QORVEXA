"""Simulation run history: repository contract, in-memory and isolated SQLite implementations.

Reports are stored as canonical JSON with their hashes; every load re-verifies them. The SQLite
store owns its own table (``twin_runs``) and touches no application storage.

Checkpoints: ``CheckpointStore`` is the extension point for mid-run snapshots (to avoid replaying
long event histories); runs are currently replayed from the model and seed, which is exact.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Protocol

from qorvexa.industrial.digital_twin.report import SimulationReport, verify_report


class TwinStoreError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class SimulationStore(Protocol):
    def save(self, report: SimulationReport) -> None: ...
    def load(self, simulation_id: str, report_sha256: str | None = None) -> SimulationReport: ...
    def history(
        self, factory_id: str, model_sha256: str | None = None
    ) -> list[SimulationReport]: ...


class CheckpointStore(Protocol):  # pragma: no cover - extension point, not implemented yet
    def put(self, simulation_id: str, at_tick: int, state: bytes) -> None: ...
    def get(self, simulation_id: str, at_tick: int) -> bytes | None: ...


def _checked(r: SimulationReport) -> SimulationReport:
    if not verify_report(r):
        raise TwinStoreError("REPORT_TAMPERED", f"{r.simulation_id}: hash does not verify")
    return r


class InMemorySimulationStore:
    def __init__(self) -> None:
        self._rows: list[tuple[str, str]] = []  # (simulation_id, json)

    def save(self, report: SimulationReport) -> None:
        _checked(report)
        self._rows.append((report.simulation_id, report.model_dump_json()))

    def load(self, simulation_id: str, report_sha256: str | None = None) -> SimulationReport:
        for sid, body in reversed(self._rows):
            r = SimulationReport.model_validate_json(body)
            if sid == simulation_id and (report_sha256 is None or r.report_sha256 == report_sha256):
                return _checked(r)
        raise TwinStoreError("UNKNOWN_SIMULATION", simulation_id)

    def history(self, factory_id: str, model_sha256: str | None = None) -> list[SimulationReport]:
        out = [_checked(SimulationReport.model_validate_json(b)) for _, b in self._rows]
        return [
            r
            for r in out
            if r.factory_id == factory_id
            and (model_sha256 is None or model_sha256 in (r.model_sha256, r.baseline_model_sha256))
        ]

    def tamper(self, i: int, body: str) -> None:  # test hook
        self._rows[i] = (self._rows[i][0], body)


class SqliteSimulationStore:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        with closing(self._conn()) as c, c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS twin_runs ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "simulation_id TEXT NOT NULL, "
                "factory_id TEXT NOT NULL, "
                "model_sha256 TEXT NOT NULL, "
                "baseline_sha256 TEXT, "
                "report_sha256 TEXT NOT NULL, "
                "body TEXT NOT NULL, "
                "UNIQUE(simulation_id, report_sha256))"
            )

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self._path, timeout=10)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def save(self, report: SimulationReport) -> None:
        _checked(report)
        with closing(self._conn()) as c, c:
            c.execute(
                "INSERT OR IGNORE INTO twin_runs("
                "simulation_id, factory_id, model_sha256, "
                "baseline_sha256, report_sha256, body"
                ") VALUES (?,?,?,?,?,?)",
                (
                    report.simulation_id,
                    report.factory_id,
                    report.model_sha256,
                    report.baseline_model_sha256,
                    report.report_sha256,
                    report.model_dump_json(),
                ),
            )

    def _row(self, row: tuple[str, str, str]) -> SimulationReport:
        sid, sha, body = row
        r = SimulationReport.model_validate_json(body)
        if r.simulation_id != sid or r.report_sha256 != sha:
            raise TwinStoreError("ROW_TAMPERED", f"row for {sid} does not match its key")
        return _checked(r)

    def load(self, simulation_id: str, report_sha256: str | None = None) -> SimulationReport:
        q = "SELECT simulation_id, report_sha256, body FROM twin_runs WHERE simulation_id=?"
        args: tuple[str, ...] = (simulation_id,)
        if report_sha256 is not None:
            q += " AND report_sha256=?"
            args += (report_sha256,)
        with closing(self._conn()) as c:
            row = c.execute(q + " ORDER BY id DESC LIMIT 1", args).fetchone()
        if row is None:
            raise TwinStoreError("UNKNOWN_SIMULATION", simulation_id)
        return self._row(row)

    def history(self, factory_id: str, model_sha256: str | None = None) -> list[SimulationReport]:
        q = "SELECT simulation_id, report_sha256, body FROM twin_runs WHERE factory_id=?"
        args: tuple[str, ...] = (factory_id,)
        if model_sha256 is not None:
            q += " AND (model_sha256=? OR baseline_sha256=?)"
            args += (model_sha256, model_sha256)
        with closing(self._conn()) as c:
            rows = c.execute(q + " ORDER BY id", args).fetchall()
        return [self._row(r) for r in rows]

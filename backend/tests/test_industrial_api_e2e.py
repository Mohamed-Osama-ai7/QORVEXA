"""End-to-end acceptance test for the Industrial Decision Studio API.

Verifies the complete journey with real HiGHS subprocess optimization:
Factory Intake -> Proposals -> Confirmation -> Formulation -> Strict Admission ->
Real Optimization (Base 2600) -> Scenario MACH120 (2800) -> Decision Intelligence ->
Human Approval -> Persistent SQLite Audit Reload.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qorvexa.api.app import create_app
from qorvexa.industrial.governance import (
    GovernedSessions,
    SqliteStore,
    Verdict,
)
from qorvexa.industrial.governance.authz import Principal as IndustrialPrincipal
from qorvexa.industrial.governance.authz import Role as IndustrialRole
from qorvexa.industrial.production_planning.admission import AdmissionPolicy
from qorvexa.solver_fabric import LPOrchestrator
from qorvexa.solver_fabric.highs_adapter import HighsAdapter
from tests.auth_helpers import AuthKit
from tests.test_formulation import FACTS

# Standard product mix test claims
TEXT = "\n".join(f"{k} is {v}" for k, v in FACTS.items())
TEST_CLAIMS = [
    {"key": k, "value": v, "quote": f"{k} is {v}"}
    for k, v in FACTS.items()
]


@pytest.fixture
def auth_kit(tmp_path: Path) -> AuthKit:
    return AuthKit(tmp_path)


@pytest.fixture
def test_db_path(tmp_path: Path) -> Path:
    return tmp_path / "industrial_test.db"


@pytest.fixture
def app_client(auth_kit: AuthKit, test_db_path: Path) -> TestClient:
    settings = auth_kit.settings()
    app = create_app(settings)

    # Configure governed sessions backed by test sqlite database
    store = SqliteStore(test_db_path)
    orch = LPOrchestrator(HighsAdapter())
    app.state.governed_sessions = GovernedSessions(
        store,
        orch,
        policy=AdmissionPolicy.LEGACY_UNVERIFIED,
        allow_self_approval=True,
    )
    return TestClient(app)


def test_full_industrial_acceptance_journey(
    auth_kit: AuthKit,
    app_client: TestClient,
    test_db_path: Path,
) -> None:
    # 1. Authenticate as Planner / Engineer
    planner_token = auth_kit.token(sub="ana_planner", roles=("engineer",))
    planner_headers = {"Authorization": f"Bearer {planner_token}"}

    # 2. Check industrial health
    health_resp = app_client.get("/api/v1/industrial/health", headers=planner_headers)
    assert health_resp.status_code == 200
    assert health_resp.json()["solver"] == "HiGHS 1.16.0"

    # 3. Create Session
    sid = "SESS_ACCEPTANCE_01"
    create_resp = app_client.post(
        "/api/v1/industrial/sessions",
        headers=planner_headers,
        json={"session_id": sid, "title": "Quarterly Production Optimization"},
    )
    assert create_resp.status_code == 201
    assert create_resp.json()["session_id"] == sid

    # 4. Ingest factory text and verified claims (proposals)
    ingest_resp = app_client.post(
        f"/api/v1/industrial/sessions/{sid}/ingest",
        headers=planner_headers,
        json={"text": TEXT, "claims": TEST_CLAIMS},
    )
    assert ingest_resp.status_code == 200
    ingest_data = ingest_resp.json()
    assert ingest_data["accepted_count"] == len(FACTS)
    assert ingest_data["rejected_count"] == 0

    # 5. Check session state (should be NEEDS_CLARIFICATION until confirmed)
    state_resp = app_client.get(
        f"/api/v1/industrial/sessions/{sid}/state",
        headers=planner_headers,
    )
    assert state_resp.status_code == 200
    state_data = state_resp.json()
    assert state_data["status"] == "NEEDS_CLARIFICATION"
    assert len(state_data["facts"]) == 0  # no facts yet

    # 6. Planner confirms all proposals
    confirm_resp = app_client.post(
        f"/api/v1/industrial/sessions/{sid}/confirm",
        headers=planner_headers,
        json={"keys": list(FACTS.keys())},
    )
    assert confirm_resp.status_code == 200
    confirmed_state = confirm_resp.json()
    assert confirmed_state["status"] == "READY"
    assert len(confirmed_state["facts"]) == len(FACTS)

    # 7. Build mathematical formulation
    build_resp = app_client.post(
        f"/api/v1/industrial/sessions/{sid}/build",
        headers=planner_headers,
    )
    assert build_resp.status_code == 200
    build_data = build_resp.json()
    assert build_data["problem"] is not None
    assert build_data["compiled"] is not None
    assert build_data["record"] is not None

    # 8. Run Decision Intelligence with Baseline + Scenario MACH120
    # MACH120 expands MACHINE capacity from 100 to 120
    scenario_spec = {
        "scenario_id": "MACH120",
        "description": "Expand machine capacity to 120 hours",
        "changes": [
            {
                "kind": "capacity",
                "resource_id": "MACHINE",
                "capacity": "120",
            }
        ],
    }

    decide_resp = app_client.post(
        f"/api/v1/industrial/sessions/{sid}/decide",
        headers=planner_headers,
        json={
            "scenarios": [scenario_spec],
            "pareto_metrics": ["objective", "total_volume"],
        },
    )
    assert decide_resp.status_code == 200
    brief = decide_resp.json()
    assert brief["baseline_status"] == "VERIFIED_NUMERICAL_SOLUTION"
    assert brief["comparison"] is not None

    # Verify mathematical values solved by REAL HiGHS subprocess:
    # Baseline objective: 2600
    # MACH120 objective: 2800 (delta +200)
    compared_scenarios = {c["scenario_id"]: c for c in brief["comparison"]["compared"]}
    assert "MACH120" in compared_scenarios
    mach120 = compared_scenarios["MACH120"]
    assert math.isclose(float(mach120["objective"]["baseline"]), 2600.0, abs_tol=1e-5)
    assert math.isclose(float(mach120["objective"]["scenario"]), 2800.0, abs_tol=1e-5)
    assert math.isclose(float(mach120["objective"]["absolute_change"]), 200.0, abs_tol=1e-5)
    assert brief["recommendation"]["status"] in {
        "RECOMMENDED",
        "CONDITIONALLY_RECOMMENDED",
    }

    # 9. Approver reviews and records approval
    approver_token = auth_kit.token(sub="omar_approver", roles=("approver",))
    approver_headers = {"Authorization": f"Bearer {approver_token}"}

    brief_sha = brief["brief_sha256"]
    decide_on_resp = app_client.post(
        f"/api/v1/industrial/sessions/{sid}/decide-on",
        headers=approver_headers,
        json={
            "brief_sha256": brief_sha,
            "verdict": "APPROVED",
            "scenario_id": "MACH120",
            "acknowledged": [
                "CAPACITY_CHANGE_COST_NOT_MODELED",
                "UNIT_VALIDATION_NOT_PERFORMED",
            ],
            "reason": "Machine capacity expansion to 120 generates +200 net margin.",
        },
    )
    assert decide_on_resp.status_code == 200
    decision_record = decide_on_resp.json()
    assert decision_record["verdict"] == "APPROVED"
    assert decision_record["scenario_id"] == "MACH120"
    assert decision_record["record_sha256"] != ""

    # 10. Audit History inspection
    history_resp = app_client.get(
        f"/api/v1/industrial/sessions/{sid}/history",
        headers=planner_headers,
    )
    assert history_resp.status_code == 200
    history_data = history_resp.json()
    assert len(history_data["records"]) == 1
    assert history_data["records"][0]["record_sha256"] == decision_record["record_sha256"]

    # 11. Server Restart Persistence Verification
    # Re-open the SQLite database with an independent GovernedSessions instance
    reopened_store = SqliteStore(test_db_path)
    reopened_sessions = GovernedSessions(
        reopened_store,
        LPOrchestrator(HighsAdapter()),
        policy=AdmissionPolicy.LEGACY_UNVERIFIED,
    )
    reloaded_principal = IndustrialPrincipal(
        id="auditor",
        kind="HUMAN",  # type: ignore[arg-type]
        roles=frozenset({IndustrialRole.VIEWER}),
    )
    reloaded_state = reopened_sessions.state(reloaded_principal, sid)
    assert reloaded_state.status.value == "READY"
    assert len(reloaded_state.facts) == len(FACTS)

    reloaded_history = reopened_sessions.history(sid)
    assert len(reloaded_history.records) == 1
    assert reloaded_history.records[0].verdict == Verdict.APPROVED
    assert reloaded_history.records[0].scenario_id == "MACH120"

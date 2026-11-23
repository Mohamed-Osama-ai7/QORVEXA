"""Guards the advisory/read-only safety boundary of the API.

Any new state-changing route must be added to ``ALLOWED_MUTATING_OPERATIONS`` deliberately, and no
route may ever target industrial control (PLC writes, setpoints, actuation). A decision recorded
through the API is a human review record in FactoryMind's own database; it triggers nothing else.
"""

import re

from qorvexa.api.app import create_app
from tests.route_helpers import all_operations

ALLOWED_MUTATING_OPERATIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/v1/anomaly/score"),  # stateless scoring of submitted samples
        ("POST", "/api/v1/ml/predict"),  # stateless prediction & OOD/uncertainty gate
        ("POST", "/api/v1/twin/simulate"),  # stateless counterfactual digital-twin simulation
        ("POST", "/api/v1/optimization/compare"),  # stateless bounded maintenance option ranking
        ("POST", "/api/v1/copilot/query"),  # stateless read-only citation-grounded copilot
        ("POST", "/api/v1/edge/validate-sample"),  # stateless read-only edge cadence/buffer check
        ("POST", "/api/v1/incidents"),  # records an incident re-detected by the server
        ("POST", "/api/v1/incidents/{incident_id}/decisions"),  # human review record
        ("POST", "/api/v1/incidents/{incident_id}/observations"),  # human field observation
        ("POST", "/api/v1/incidents/{incident_id}/outcomes"),  # human outcome verification
        ("POST", "/api/v1/industrial/sessions"),  # creates governed planning session
        ("POST", "/api/v1/industrial/sessions/{session_id}/ingest"),  # intake proposals
        ("POST", "/api/v1/industrial/sessions/{session_id}/confirm"),  # fact confirmation
        ("POST", "/api/v1/industrial/sessions/{session_id}/assert"),  # asserted fact
        ("POST", "/api/v1/industrial/sessions/{session_id}/assume"),  # default assumption
        ("POST", "/api/v1/industrial/sessions/{session_id}/retract"),  # claim retraction
        ("POST", "/api/v1/industrial/sessions/{session_id}/resolve"),  # conflict resolution
        ("POST", "/api/v1/industrial/sessions/{session_id}/convert-units"),  # unit conversion
        ("POST", "/api/v1/industrial/sessions/{session_id}/build"),  # build formulation
        ("POST", "/api/v1/industrial/sessions/{session_id}/decide"),  # run optimization
        ("POST", "/api/v1/industrial/sessions/{session_id}/decide-on"),  # human approval record
        ("POST", "/api/v1/industrial/terminology/{factory_id}/propose"),  # propose term
        ("POST", "/api/v1/industrial/terminology/{factory_id}/{term_id}/confirm"),  # confirm
        ("POST", "/api/v1/industrial/terminology/{factory_id}/{term_id}/reject"),  # reject
        ("POST", "/api/v1/industrial/terminology/{factory_id}/{term_id}/correct"),  # correct
        ("POST", "/api/v1/industrial/terminology/{factory_id}/{term_id}/deprecate"),  # deprecate
        ("POST", "/api/v1/industrial/terminology/{factory_id}/{term_id}/rollback"),  # rollback
    }
)

FORBIDDEN_PATH_PATTERN = re.compile(
    r"(plc|setpoint|actuat|write[-_]?tag|command|control|opc|modbus)", re.IGNORECASE
)
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def test_the_guard_sees_the_applications_routes() -> None:
    operations = all_operations(create_app())
    assert len(operations) >= 11  # fails loudly if route discovery ever stops working
    assert ("GET", "/api/v1/health") in operations
    assert ("POST", "/api/v1/incidents") in operations


def test_no_route_targets_industrial_control() -> None:
    offending = [p for _, p in all_operations(create_app()) if FORBIDDEN_PATH_PATTERN.search(p)]
    assert offending == []


def test_mutating_routes_are_exactly_the_allowlist() -> None:
    mutating = {(m, p) for m, p in all_operations(create_app()) if m in MUTATING}
    assert mutating == ALLOWED_MUTATING_OPERATIONS  # no new write route, no stale allowlist entry

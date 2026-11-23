from fastapi.testclient import TestClient

from qorvexa import __version__
from qorvexa.api.app import create_app


def test_health_reports_version_and_read_only_mode() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["operating_mode"] == "advisory-read-only"
    assert body["auth"] in {"configured", "not_configured"}

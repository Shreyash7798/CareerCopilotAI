"""Version endpoint."""

from fastapi.testclient import TestClient

from app.main import create_app


def test_api_version():
    client = TestClient(create_app())
    res = client.get("/api/version")
    assert res.status_code == 200
    body = res.json()
    assert body["project"] == "CareerCopilotAI"
    assert "revision" in body


def test_api_health():
    client = TestClient(create_app())
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"ok": True}

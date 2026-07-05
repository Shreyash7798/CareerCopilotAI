"""Deploy hook API."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import create_app


def test_deploy_hook_disabled_without_token():
    client = TestClient(create_app())
    res = client.post("/api/deploy/hook", headers={"Authorization": "Bearer x"})
    assert res.status_code == 503


def test_deploy_hook_runs_with_token(monkeypatch):
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda refresh=False: {"app": {"deploy_token": "secret-token"}},
    )
    with patch("app.routers.api.run_deploy", return_value={"status": "ok", "revision_after": "abc"}):
        client = TestClient(create_app())
        res = client.post(
            "/api/deploy/hook",
            headers={"Authorization": "Bearer secret-token"},
        )
    assert res.status_code == 200
    assert res.json()["revision_after"] == "abc"

"""End-to-end smoke tests — run on every PR to catch login/dashboard regressions."""

import pytest

import app.db as db_mod


@pytest.fixture()
def smoke_client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "database_url", lambda: f"sqlite:///{tmp_path}/smoke.db")
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_SessionLocal", None)
    db_mod.init_db()

    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(), follow_redirects=False) as client:
        yield client


def test_smoke_public_pages(smoke_client):
    assert smoke_client.get("/login").status_code == 200
    assert smoke_client.get("/api/version").status_code == 200
    assert smoke_client.get("/api/health").status_code == 200


def test_smoke_login_and_dashboard(smoke_client):
    login = smoke_client.post(
        "/login",
        data={"email": "admin@careercopilot.local", "password": "changeme123", "next": "/"},
    )
    assert login.status_code == 303
    dash = smoke_client.get("/")
    assert dash.status_code == 200
    assert "journey" in dash.text.lower() or "dashboard" in dash.text.lower()


def test_smoke_authenticated_pages(smoke_client):
    smoke_client.post(
        "/login",
        data={"email": "admin@careercopilot.local", "password": "changeme123"},
    )
    for path in ("/jobs", "/applications", "/companies", "/profile", "/settings", "/help"):
        resp = smoke_client.get(path)
        assert resp.status_code == 200, f"{path} returned {resp.status_code}"


def test_smoke_admin_pages(smoke_client):
    smoke_client.post(
        "/login",
        data={"email": "admin@careercopilot.local", "password": "changeme123"},
    )
    assert smoke_client.get("/admin/users").status_code == 200
    assert smoke_client.get("/admin/status").status_code == 200

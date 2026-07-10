"""Jobs page filter query handling."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.db as db_mod
from app.main import app
from app.models import User
from app.routers.pages import _location_tokens
from app.users import COOKIE_NAME, create_session_token


@pytest.fixture()
def client_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "database_url", lambda: f"sqlite:///{tmp_path}/jobs_filters.db")
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_SessionLocal", None)
    db_mod.init_db()
    with db_mod.session_scope() as session:
        admin_id = session.query(User).one().id
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, create_session_token(admin_id))
        yield client


def test_location_tokens_split():
    assert _location_tokens("Mumbai, Pune") == ["Mumbai", "Pune"]
    assert _location_tokens("Mumbai") == ["Mumbai"]


def test_jobs_filter_empty_min_score_returns_200(client_db):
    r = client_db.get("/jobs", params={"q": "engineer", "min_score": ""})
    assert r.status_code == 200
    assert "engineer" in r.text or "matching" in r.text.lower()


def test_jobs_filter_high_priority(client_db):
    for param in ("true", "1", "yes"):
        r = client_db.get("/jobs", params={"high_priority": param})
        assert r.status_code == 200


def test_jobs_filter_source(client_db):
    r = client_db.get("/jobs", params={"source": "linkedin"})
    assert r.status_code == 200
    assert "linkedin" in r.text.lower() or "matching" in r.text.lower()


def test_jobs_query_string_filter():
    from app.routers.pages import _jobs_query_string

    qs = _jobs_query_string(
        {
            "q": "civil",
            "location": "Mumbai",
            "company": "Larsen & Toubro",
            "source": "linkedin",
            "min_score": 50,
            "high_priority": True,
            "page": 2,
        }
    )
    assert "q=civil" in qs
    assert "location=Mumbai" in qs
    assert "company=Larsen" in qs
    assert "source=linkedin" in qs
    assert "min_score=50" in qs
    assert "high_priority=true" in qs
    assert "page=2" in qs


def test_jobs_company_filter_special_chars(client_db):
    r = client_db.get("/jobs", params={"company": "Larsen & Toubro (L&T)"})
    assert r.status_code == 200

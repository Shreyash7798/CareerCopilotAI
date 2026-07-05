"""Password login and job re-scoring."""

import pytest

import app.db as db_mod
import app.pipeline as pipeline_mod
from app.auth import session_token
from app.models import Company, Job


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "database_url", lambda: f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_SessionLocal", None)
    db_mod.init_db()
    yield


def _settings(profile: dict) -> dict:
    return {
        "profile": profile,
        "scoring": {
            "weights": {},
            "high_priority_threshold": 70,
            "role_keywords": ["consultant"],
            "negative_role_keywords": [],
        },
    }


def test_rescore_changes_scores_for_new_profile(temp_db, monkeypatch):
    with db_mod.session_scope() as session:
        company = Company(name="Acme")
        session.add(company)
        session.flush()
        session.add(
            Job(
                company_id=company.id,
                title="Operations Consultant",
                location="Mumbai, India",
                description="3-5 years experience in supply chain.",
                dedup_key="k1",
                match_score=0.0,
            )
        )

    mumbai_profile = {
        "experience_years": 3,
        "preferred_locations": ["Mumbai"],
        "skills": ["Supply Chain"],
        "preferred_domains": [],
        "interests": [],
        "preferred_companies": [],
        "avoided_companies": [],
    }
    monkeypatch.setattr(pipeline_mod, "get_settings", lambda refresh=False: _settings(mumbai_profile))
    assert pipeline_mod.rescore_all_jobs() == 1
    with db_mod.session_scope() as session:
        from sqlalchemy import select

        job = session.execute(select(Job)).scalars().one()
        score_mumbai = job.match_score
        assert score_mumbai > 50
        assert job.score_breakdown

    berlin_profile = dict(mumbai_profile, preferred_locations=["Berlin"], skills=["Kubernetes"])
    monkeypatch.setattr(pipeline_mod, "get_settings", lambda refresh=False: _settings(berlin_profile))
    pipeline_mod.rescore_all_jobs()
    with db_mod.session_scope() as session:
        from sqlalchemy import select

        job = session.execute(select(Job)).scalars().one()
        assert job.match_score < score_mumbai  # profile change re-ranked the job


def test_session_token_depends_on_password():
    assert session_token("a") == session_token("a")
    assert session_token("a") != session_token("b")


def test_auth_middleware_blocks_and_allows(monkeypatch, tmp_path):
    monkeypatch.setattr(db_mod, "database_url", lambda: f"sqlite:///{tmp_path}/auth.db")
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_SessionLocal", None)

    import app.auth as auth_mod

    monkeypatch.setattr(auth_mod, "configured_password", lambda: "secret123")

    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app, follow_redirects=False) as client:
        # Unauthenticated page -> redirect to login
        resp = client.get("/jobs")
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/login")
        # Unauthenticated API -> 401
        assert client.get("/api/jobs").status_code == 401
        # Login page is public
        assert client.get("/login").status_code == 200
        # Wrong password rejected
        resp = client.post("/login", data={"password": "nope", "next": "/"})
        assert resp.status_code == 401
        # Correct password sets the session cookie and unlocks pages
        resp = client.post("/login", data={"password": "secret123", "next": "/jobs"})
        assert resp.status_code == 303
        assert resp.headers["location"] == "/jobs"
        assert client.get("/jobs").status_code == 200
        assert client.get("/api/jobs").status_code == 200


def test_auth_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(db_mod, "database_url", lambda: f"sqlite:///{tmp_path}/noauth.db")
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_SessionLocal", None)

    import app.auth as auth_mod

    monkeypatch.setattr(auth_mod, "configured_password", lambda: "")

    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app, follow_redirects=False) as client:
        assert client.get("/jobs").status_code == 200

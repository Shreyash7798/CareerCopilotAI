"""Password login and job re-scoring."""

import pytest

import app.db as db_mod
import app.pipeline as pipeline_mod
from app.models import Company, Job, User, UserCompanyMonitor, UserJobScore
from app.users import COOKIE_NAME, ROLE_ADMIN, create_session_token, hash_password


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
    import json

    with db_mod.session_scope() as session:
        admin = session.query(User).filter_by(role=ROLE_ADMIN).one()
        company = Company(name="Acme", ats_type="greenhouse", enabled=True)
        session.add(company)
        session.flush()
        session.add(UserCompanyMonitor(user_id=admin.id, company_id=company.id, enabled=True))
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
        session.flush()
        job = session.query(Job).one()
        session.add(UserJobScore(user_id=admin.id, job_id=job.id, match_score=0.0))
        admin_id = admin.id

    mumbai_profile = {
        "experience_years": 3,
        "preferred_locations": ["Mumbai"],
        "skills": ["Supply Chain"],
        "preferred_domains": [],
        "interests": [],
        "preferred_companies": [],
        "avoided_companies": [],
    }
    prefs = _settings(mumbai_profile)
    with db_mod.session_scope() as session:
        admin = session.get(User, admin_id)
        admin.preferences_json = json.dumps(
            {"profile": mumbai_profile, "scoring": prefs["scoring"], "notifications": {}}
        )

    monkeypatch.setattr(pipeline_mod, "get_settings", lambda refresh=False: prefs)
    assert pipeline_mod.rescore_all_jobs(admin_id) == 1
    with db_mod.session_scope() as session:
        from sqlalchemy import select

        score = session.execute(select(UserJobScore)).scalars().one()
        score_mumbai = score.match_score
        assert score_mumbai > 50
        assert score.score_breakdown

    berlin_profile = dict(mumbai_profile, preferred_locations=["Berlin"], skills=["Kubernetes"])
    prefs2 = _settings(berlin_profile)
    with db_mod.session_scope() as session:
        admin = session.get(User, admin_id)
        admin.preferences_json = json.dumps(
            {"profile": berlin_profile, "scoring": prefs2["scoring"], "notifications": {}}
        )
    monkeypatch.setattr(pipeline_mod, "get_settings", lambda refresh=False: prefs2)
    pipeline_mod.rescore_all_jobs(admin_id)
    with db_mod.session_scope() as session:
        from sqlalchemy import select

        score = session.execute(select(UserJobScore)).scalars().one()
        assert score.match_score < score_mumbai


def test_auth_middleware_blocks_and_allows(monkeypatch, tmp_path):
    monkeypatch.setattr(db_mod, "database_url", lambda: f"sqlite:///{tmp_path}/auth.db")
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_SessionLocal", None)
    db_mod.init_db()

    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app, follow_redirects=False) as client:
        resp = client.get("/jobs")
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/login")
        assert client.get("/api/jobs").status_code == 401
        assert client.get("/login").status_code == 200
        resp = client.post(
            "/login",
            data={"email": "admin@careercopilot.local", "password": "changeme123", "next": "/jobs"},
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/jobs"
        assert client.get("/jobs").status_code == 200
        assert client.get("/api/jobs").status_code == 200


def test_session_token_roundtrip():
    token = create_session_token(42)
    from app.users import parse_session_token

    assert parse_session_token(token) == 42

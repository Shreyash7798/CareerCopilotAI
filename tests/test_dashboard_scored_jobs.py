"""Regression: dashboard must load when the user has scored jobs (production path)."""

import pytest
from sqlalchemy import select

import app.db as db_mod
from app.models import Company, Job, User, UserJobScore
from app.users import COOKIE_NAME, create_session_token


@pytest.fixture()
def client_with_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "database_url", lambda: f"sqlite:///{tmp_path}/dash.db")
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_SessionLocal", None)
    db_mod.init_db()

    with db_mod.session_scope() as session:
        user = session.execute(
            select(User).where(User.email == "admin@careercopilot.local")
        ).scalar_one()
        company = Company(name="DashCo", ats_type="greenhouse", enabled=True)
        session.add(company)
        session.flush()
        job = Job(
            company_id=company.id,
            title="Consultant",
            location="Mumbai",
            dedup_key="dash-k1",
            is_active=True,
            score_breakdown='[{"name":"role_fit","score":null,"reason":"x"}]',
            jd_fit_breakdown='[{"name":"skills","score":0.8}]',
        )
        session.add(job)
        session.flush()
        session.add(
            UserJobScore(
                user_id=user.id,
                job_id=job.id,
                match_score=None,  # NULL must not crash analytics or templates
                jd_fit_score=72.0,
                is_high_priority=True,
                score_breakdown=job.score_breakdown,
                jd_fit_breakdown=job.jd_fit_breakdown,
            )
        )
        user_id = user.id

    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(), follow_redirects=False) as client:
        client.cookies.set(COOKIE_NAME, create_session_token(user_id))
        yield client


def test_dashboard_with_scored_jobs_returns_200(client_with_jobs):
    resp = client_with_jobs.get("/")
    assert resp.status_code == 200, resp.text[:500]
    assert "DashCo" in resp.text or "Consultant" in resp.text
    assert "Internal Server Error" not in resp.text


def test_jobs_page_with_scored_jobs_returns_200(client_with_jobs):
    resp = client_with_jobs.get("/jobs")
    assert resp.status_code == 200, resp.text[:500]
    assert "Consultant" in resp.text


def test_companies_page_with_scored_jobs_returns_200(client_with_jobs):
    resp = client_with_jobs.get("/companies")
    assert resp.status_code == 200, resp.text[:500]


def test_analytics_null_scores_safe(client_with_jobs):
    from app.analytics import compute_analytics

    with db_mod.session_scope() as session:
        user = session.execute(
            select(User).where(User.email == "admin@careercopilot.local")
        ).scalar_one()
        data = compute_analytics(session, user_id=user.id)
    assert data["active_jobs"] >= 1
    assert "high" in data["score_buckets"]
    assert isinstance(data["avg_match_score"], float)

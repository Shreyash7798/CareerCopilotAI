"""Tests for single LinkedIn job import."""

import pytest

import app.db as db_mod
from app.linkedin_import import import_linkedin_job
from app.models import Job
from app.sources.base import RawJob


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "database_url", lambda: f"sqlite:///{tmp_path}/li.db")
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_SessionLocal", None)
    monkeypatch.setattr(
        "app.linkedin_import.get_settings",
        lambda refresh=False: {
            "profile": {
                "experience_years": 3,
                "preferred_locations": ["Mumbai"],
                "skills": ["Operations", "Strategy"],
                "preferred_domains": ["Consulting"],
                "interests": ["Operations Consulting"],
                "preferred_companies": ["EY"],
                "avoided_companies": [],
            },
            "scoring": {
                "weights": {},
                "high_priority_threshold": 70,
                "role_keywords": ["consultant"],
                "negative_role_keywords": [],
            },
        },
    )
    db_mod.init_db()
    yield


def test_import_linkedin_job_creates(temp_db, monkeypatch):
    from datetime import datetime

    raw = RawJob(
        company="EY India",
        title="Operations Consultant",
        location="Mumbai, India",
        description="Operations and supply chain consulting role.",
        url="https://www.linkedin.com/jobs/view/4434425496",
        source="linkedin",
        external_id="4434425496",
        posted_at=datetime(2026, 6, 29),
    )
    monkeypatch.setattr("app.linkedin_import.parse_job_url", lambda url: raw)

    result = import_linkedin_job("https://www.linkedin.com/jobs/view/4434425496")
    assert result["status"] == "created"
    assert result["job_id"] > 0

    with db_mod.session_scope() as session:
        from sqlalchemy import select

        job = session.execute(select(Job)).scalar_one()
        assert job.source == "linkedin"
        assert job.title == "Operations Consultant"

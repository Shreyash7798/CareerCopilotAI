"""Startup bootstrap and location backfill."""

import pytest
from sqlalchemy import func, select

import app.db as db_mod
from app.location_infer import infer_location
from app.models import Company, Job
from app.startup import backfill_job_locations, bootstrap_starter_pack, ensure_accenture
from app.scoring import score_job


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "database_url", lambda: f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_SessionLocal", None)
    db_mod.init_db()
    yield


def test_bootstrap_starter_pack_loads_companies(temp_db):
    with db_mod.session_scope() as session:
        added = bootstrap_starter_pack(session)
        assert added >= 4
        names = {c.name for c in session.execute(select(Company)).scalars()}
        assert "Accenture India" in names
        accenture = session.execute(
            select(Company).where(Company.name == "Accenture India")
        ).scalar_one()
        assert accenture.ats_type == "accenture"
        assert accenture.enabled is True


def test_ensure_accenture_upgrades_legacy_row(temp_db):
    with db_mod.session_scope() as session:
        session.add(
            Company(
                name="Accenture India",
                ats_type="careers_page",
                enabled=False,
                career_url="https://www.accenture.com/in-en/careers/jobsearch",
            )
        )
        session.flush()
        assert ensure_accenture(session)
        company = session.execute(
            select(Company).where(Company.name == "Accenture India")
        ).scalar_one()
        assert company.ats_type == "accenture"
        assert company.enabled is True


def test_backfill_location_boosts_score(temp_db):
    profile = {
        "experience_years": 3,
        "preferred_locations": ["Mumbai", "Pune"],
        "acceptable_locations": ["Remote"],
        "preferred_domains": ["Global Business Consulting"],
        "interests": ["Operations Consulting"],
        "skills": ["Operations", "Strategy", "Excel"],
        "preferred_companies": ["EY"],
        "avoided_companies": [],
        "current_employer": "PwC India",
    }
    scoring_cfg = {
        "role_keywords": ["consultant", "consulting"],
        "negative_role_keywords": [],
        "weights": {},
        "high_priority_threshold": 70,
    }
    title = "Senior Consultant | Mumbai | Technology Strategy"
    before = score_job(
        title=title,
        description="consulting role",
        location="",
        company="EY India",
        profile=profile,
        scoring_cfg=scoring_cfg,
    )[0]
    after = score_job(
        title=title,
        description="consulting role",
        location=infer_location(title=title),
        company="EY India",
        profile=profile,
        scoring_cfg=scoring_cfg,
    )[0]
    assert after > before
    assert after - before >= 10

    with db_mod.session_scope() as session:
        session.add(
            Job(
                title=title,
                company=Company(name="EY India"),
                dedup_key="ey-1",
                match_score=before,
                location="",
            )
        )
        session.flush()
        updated = backfill_job_locations(session)
        job = session.execute(select(Job)).scalar_one()
        assert updated == 1
        assert job.location == "Mumbai"

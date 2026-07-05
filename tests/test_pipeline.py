"""End-to-end pipeline test with a fake connector and a temporary database,
using database-backed company configuration (Company Management MVP)."""

import pytest

import app.db as db_mod
import app.pipeline as pipeline_mod
from app.models import Company
from app.sources.base import RawJob


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "database_url", lambda: f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_SessionLocal", None)
    db_mod.init_db()
    yield


FAKE_JOBS = [
    RawJob(
        company="Acme Consulting",
        title="Operations Consultant",
        location="Mumbai, India",
        description="3-5 years experience. Supply chain, strategy, Excel required.",
        url="https://acme.example/jobs/1",
        source="fake",
        external_id="1",
    ),
    RawJob(
        company="Acme Consulting",
        title="Operations Consultant",  # exact duplicate
        location="Mumbai, India",
        url="https://acme.example/jobs/1b",
        source="fake",
        external_id="1b",
    ),
    RawJob(
        company="Acme Consulting",
        title="Software Engineer",  # filtered out by company keywords
        location="Mumbai, India",
        url="https://acme.example/jobs/2",
        source="fake",
        external_id="2",
    ),
]


CURRENT_JOBS: list[RawJob] = []


@pytest.fixture()
def fake_setup(temp_db, monkeypatch):
    # Register a fake connector and map the 'greenhouse' ats_type to it.
    CURRENT_JOBS[:] = FAKE_JOBS
    monkeypatch.setitem(pipeline_mod.REGISTRY, "greenhouse", lambda entry: list(CURRENT_JOBS))
    monkeypatch.setattr(pipeline_mod, "sources_yaml_exists", lambda: False)
    monkeypatch.setattr(
        pipeline_mod,
        "get_settings",
        lambda refresh=False: {
            "profile": {
                "experience_years": 3,
                "preferred_locations": ["Mumbai", "Pune"],
                "skills": ["Supply Chain", "Excel", "Strategy"],
                "preferred_domains": ["Consulting"],
                "interests": ["Operations Consulting"],
                "preferred_companies": [],
                "avoided_companies": [],
            },
            "scoring": {
                "weights": {},
                "high_priority_threshold": 70,
                "role_keywords": ["consultant", "operations"],
                "negative_role_keywords": ["intern"],
            },
        },
    )
    with db_mod.session_scope() as session:
        session.add(
            Company(
                name="Acme Consulting",
                ats_type="greenhouse",
                ats_config='{"board": "acme"}',
                enabled=True,
                keywords="consultant, operations",
            )
        )


def test_pipeline_end_to_end_db_config(fake_setup):
    result = pipeline_mod.run_pipeline(notify=False, export=False)
    assert result.sources_run == 1
    assert result.fetched == 3
    assert result.filtered_out == 1  # software engineer (company keywords)
    assert result.duplicates == 1  # exact duplicate
    assert result.new_jobs == 1
    assert result.high_priority == 1  # strong profile match

    # Second run: everything is a duplicate now.
    result2 = pipeline_mod.run_pipeline(notify=False, export=False)
    assert result2.new_jobs == 0
    assert result2.duplicates == 2

    from sqlalchemy import select

    from app.models import ActivityLog, Job

    with db_mod.session_scope() as session:
        jobs = session.execute(select(Job)).scalars().all()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.match_score >= 70
        assert job.score_breakdown  # explainable
        company = session.execute(select(Company)).scalars().one()
        assert company.last_run_at is not None
        assert "3 jobs fetched" in (company.last_run_status or "")
        logs = session.execute(select(ActivityLog)).scalars().all()
        assert any("Pipeline run" in log.message for log in logs)


def test_disabled_company_is_skipped(fake_setup):
    with db_mod.session_scope() as session:
        from sqlalchemy import select

        company = session.execute(select(Company)).scalars().one()
        company.enabled = False
    result = pipeline_mod.run_pipeline(notify=False, export=False)
    assert result.sources_run == 0
    assert result.new_jobs == 0


def test_stale_jobs_deactivated_and_reactivated(fake_setup):
    result = pipeline_mod.run_pipeline(notify=False, export=False)
    assert result.new_jobs == 1

    # The consultant role disappears from the board; a new one appears.
    CURRENT_JOBS[:] = [
        RawJob(
            company="Acme Consulting",
            title="Strategy Consultant",
            location="Pune, India",
            description="2-4 years of strategy experience.",
            url="https://acme.example/jobs/9",
            source="fake",
            external_id="9",
        )
    ]
    result2 = pipeline_mod.run_pipeline(notify=False, export=False)
    assert result2.new_jobs == 1
    assert result2.deactivated == 1  # the operations consultant closed

    from sqlalchemy import select

    from app.models import Job

    with db_mod.session_scope() as session:
        by_title = {j.title: j for j in session.execute(select(Job)).scalars()}
        assert by_title["Operations Consultant"].is_active is False
        assert by_title["Strategy Consultant"].is_active is True

    # The original posting comes back -> reactivated, not duplicated.
    CURRENT_JOBS[:] = FAKE_JOBS
    result3 = pipeline_mod.run_pipeline(notify=False, export=False)
    assert result3.new_jobs == 0
    assert result3.reactivated == 1
    with db_mod.session_scope() as session:
        by_title = {j.title: j for j in session.execute(select(Job)).scalars()}
        assert by_title["Operations Consultant"].is_active is True


def test_refresh_interval_skips_recent(fake_setup):
    result = pipeline_mod.run_pipeline(notify=False, export=False)
    assert result.sources_run == 1
    with db_mod.session_scope() as session:
        from sqlalchemy import select

        company = session.execute(select(Company)).scalars().one()
        company.refresh_interval_minutes = 999
    result2 = pipeline_mod.run_pipeline(notify=False, export=False)
    assert result2.sources_run == 0
    assert result2.sources_skipped == 1


def test_aged_untracked_jobs_deactivated(fake_setup, monkeypatch):
    from datetime import datetime, timedelta

    from sqlalchemy import select

    from app.models import Application, Job

    monkeypatch.setattr(
        "app.job_visibility.get_settings",
        lambda refresh=False: {
            "profile": {
                "experience_years": 3,
                "preferred_locations": ["Mumbai", "Pune"],
                "skills": ["Supply Chain", "Excel", "Strategy"],
                "preferred_domains": ["Consulting"],
                "interests": ["Operations Consulting"],
                "preferred_companies": [],
                "avoided_companies": [],
            },
            "scoring": {
                "weights": {},
                "high_priority_threshold": 70,
                "role_keywords": ["consultant", "operations"],
                "negative_role_keywords": ["intern"],
            },
            "job_visibility": {
                "hide_stale_untracked": True,
                "max_posted_age_days": 30,
                "max_discovered_age_days": 14,
            },
        },
    )
    pipeline_mod.run_pipeline(notify=False, export=False)
    tracked_job_id = None
    with db_mod.session_scope() as session:
        job = session.execute(select(Job)).scalars().one()
        tracked_job_id = job.id
        job.posted_at = datetime.utcnow() - timedelta(days=45)
        session.add(
            Application(job_id=job.id, company_name="Acme Consulting", role=job.title, status="Applied")
        )
        job2 = Job(
            company_id=job.company_id,
            title="Old Strategy Role",
            location="Mumbai",
            description="old",
            url="https://acme.example/old",
            source="fake",
            external_id="old",
            dedup_key="oldkey123",
            posted_at=datetime.utcnow() - timedelta(days=45),
            match_score=50,
            is_high_priority=False,
        )
        session.add(job2)

    result = pipeline_mod.run_pipeline(notify=False, export=False)
    assert result.deactivated >= 1
    with db_mod.session_scope() as session:
        tracked = session.get(Job, tracked_job_id)
        stale = session.execute(select(Job).where(Job.title == "Old Strategy Role")).scalars().one()
        assert tracked.is_active is True  # tracked jobs stay active
        assert stale.is_active is False

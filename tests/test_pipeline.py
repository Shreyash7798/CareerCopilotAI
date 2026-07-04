"""End-to-end pipeline test with a fake source and a temporary database."""

import pytest

import app.db as db_mod
import app.pipeline as pipeline_mod
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
        title="Software Engineer",  # filtered out by title filter
        location="Mumbai, India",
        url="https://acme.example/jobs/2",
        source="fake",
        external_id="2",
    ),
]


@pytest.fixture()
def fake_sources(monkeypatch):
    monkeypatch.setitem(pipeline_mod.REGISTRY, "fake", lambda entry: list(FAKE_JOBS))
    monkeypatch.setattr(
        pipeline_mod,
        "get_sources_config",
        lambda refresh=False: {
            "sources": [{"type": "fake", "company": "Acme Consulting", "enabled": True}],
            "filters": {"title_must_contain_any": ["consultant"]},
        },
    )
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


def test_pipeline_end_to_end(temp_db, fake_sources):
    result = pipeline_mod.run_pipeline(notify=False, export=False)
    assert result.sources_run == 1
    assert result.fetched == 3
    assert result.filtered_out == 1  # software engineer
    assert result.duplicates == 1  # exact duplicate
    assert result.new_jobs == 1
    assert result.high_priority == 1  # strong profile match

    # Second run: everything is a duplicate now.
    result2 = pipeline_mod.run_pipeline(notify=False, export=False)
    assert result2.new_jobs == 0
    assert result2.duplicates == 2

    from sqlalchemy import select

    from app.models import ActivityLog, Company, Job

    with db_mod.session_scope() as session:
        jobs = session.execute(select(Job)).scalars().all()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.match_score >= 70
        assert job.score_breakdown  # explainable
        companies = session.execute(select(Company)).scalars().all()
        assert [c.name for c in companies] == ["Acme Consulting"]
        logs = session.execute(select(ActivityLog)).scalars().all()
        assert any("Pipeline run" in log.message for log in logs)

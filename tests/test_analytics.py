"""Analytics page metrics."""

from datetime import datetime, timedelta

import pytest

import app.db as db_mod
from app.analytics import compute_analytics
from app.models import Application, Company, Job


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "database_url", lambda: f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_SessionLocal", None)
    db_mod.init_db()
    yield


def test_compute_analytics_extended(temp_db):
    with db_mod.session_scope() as db_session:
        company = Company(
            name="Accenture India", sector="Consulting & Advisory", ats_type="accenture", enabled=True
        )
        db_session.add(company)
        db_session.flush()

        db_session.add_all(
            [
                Job(
                    company_id=company.id,
                    title="Consulting Analyst",
                    source="accenture",
                    dedup_key="accenture-analyst",
                    match_score=75,
                    is_high_priority=True,
                    is_active=True,
                    description="Python SQL consulting strategy",
                    discovered_at=datetime.utcnow() - timedelta(days=2),
                ),
                Job(
                    company_id=company.id,
                    title="Tech Consultant",
                    source="accenture",
                    dedup_key="accenture-tech",
                    match_score=50,
                    is_active=True,
                    description="Java agile delivery",
                    discovered_at=datetime.utcnow() - timedelta(days=20),
                ),
                Job(
                    company_id=company.id,
                    title="Old Role",
                    source="accenture",
                    dedup_key="accenture-old",
                    match_score=30,
                    is_active=False,
                    discovered_at=datetime.utcnow() - timedelta(days=40),
                ),
            ]
        )
        db_session.add_all(
            [
                Application(company_name="Accenture India", role="Consulting Analyst", status="Applied"),
                Application(company_name="Accenture India", role="Tech Consultant", status="Interviewing"),
                Application(company_name="Other Co", role="PM", status="Planned"),
            ]
        )
        db_session.commit()

        data = compute_analytics(db_session)

    assert data["total_jobs"] == 3
    assert data["active_jobs"] == 2
    assert data["high_priority_jobs"] == 1
    assert data["applications_submitted"] == 2
    assert data["interviews_received"] == 1
    assert data["jobs_last_7_days"] == 1
    assert data["jobs_last_30_days"] == 2
    assert data["monitored_companies"] == 1
    assert data["enabled_companies"] == 1
    assert data["score_buckets"]["high"] == 1
    assert data["score_buckets"]["medium"] == 1
    assert data["jobs_by_source"][0]["source"] == "accenture"
    assert data["jobs_by_sector"][0]["sector"] == "Consulting & Advisory"
    assert any(row["status"] == "Applied" for row in data["applications_by_status"])

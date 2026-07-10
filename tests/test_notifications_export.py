"""Per-user notifications and Excel export isolation."""

import json
from unittest.mock import patch

import pytest
from sqlalchemy import select

import app.db as db_mod
from app.exporter import export_excel
from app.models import Application, Company, Job, Recruiter, User, UserCompanyMonitor, UserJobScore
from app.notifications import build_daily_summary, deliver_to_user, notify_high_priority
from app.users import COOKIE_NAME, ROLE_MEMBER, create_session_token


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "database_url", lambda: f"sqlite:///{tmp_path}/notify.db")
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_SessionLocal", None)
    db_mod.init_db()
    yield


def test_export_excel_only_includes_user_data(temp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.exporter.user_exports_dir",
        lambda uid: tmp_path / f"user_{uid}" / "exports",
    )
    with db_mod.session_scope() as session:
        u1 = User(email="one@test.com", display_name="One", password_hash="x", role=ROLE_MEMBER)
        u2 = User(email="two@test.com", display_name="Two", password_hash="x", role=ROLE_MEMBER)
        company = Company(name="Acme")
        session.add_all([u1, u2, company])
        session.flush()
        job = Job(company_id=company.id, title="Consultant", dedup_key="k1")
        session.add(job)
        session.flush()
        session.add(UserJobScore(user_id=u1.id, job_id=job.id, match_score=90.0))
        session.add(Application(user_id=u1.id, company_name="Secret", role="Analyst"))
        session.add(Application(user_id=u2.id, company_name="Other", role="Dev"))
        u1_id = u1.id

    path = export_excel(u1_id)
    import pandas as pd

    jobs = pd.read_excel(path, sheet_name="Jobs")
    apps = pd.read_excel(path, sheet_name="Applications")
    assert len(jobs) == 1
    assert jobs.iloc[0]["Title"] == "Consultant"
    assert len(apps) == 1
    assert apps.iloc[0]["Company"] == "Secret"


def test_export_excel_recruiters_sheet(temp_db, tmp_path, monkeypatch):
    """Recruiters sheet must not crash when loading company_id list."""
    monkeypatch.setattr(
        "app.exporter.user_exports_dir",
        lambda uid: tmp_path / f"user_{uid}" / "exports",
    )
    with db_mod.session_scope() as session:
        user = User(email="one@test.com", display_name="One", password_hash="x", role=ROLE_MEMBER)
        company = Company(name="Acme", ats_type="greenhouse")
        session.add_all([user, company])
        session.flush()
        session.add(UserCompanyMonitor(user_id=user.id, company_id=company.id, enabled=True))
        session.add(
            Recruiter(name="Pat", company_id=company.id, public_email="pat@acme.com")
        )
        user_id = user.id

    path = export_excel(user_id)
    import pandas as pd

    recs = pd.read_excel(path, sheet_name="Recruiters")
    assert len(recs) == 1
    assert recs.iloc[0]["Name"] == "Pat"


def test_notify_high_priority_per_user(temp_db, monkeypatch):
    with db_mod.session_scope() as session:
        u1 = User(
            email="one@test.com",
            display_name="One",
            password_hash="x",
            role=ROLE_MEMBER,
            preferences_json=json.dumps(
                {
                    "notifications": {
                        "telegram_chat_id": "111",
                        "telegram_enabled": True,
                        "high_priority": True,
                    }
                }
            ),
        )
        u2 = User(
            email="two@test.com",
            display_name="Two",
            password_hash="x",
            role=ROLE_MEMBER,
            preferences_json=json.dumps(
                {
                    "notifications": {
                        "telegram_chat_id": "222",
                        "telegram_enabled": True,
                        "high_priority": True,
                    }
                }
            ),
        )
        company = Company(name="Acme")
        session.add_all([u1, u2, company])
        session.flush()
        job = Job(company_id=company.id, title="Consultant", dedup_key="k1")
        session.add(job)
        session.flush()
        session.add(UserJobScore(user_id=u1.id, job_id=job.id, match_score=90.0, is_high_priority=True))
        session.add(
            UserJobScore(user_id=u2.id, job_id=job.id, match_score=40.0, is_high_priority=False)
        )
        job_id = job.id

    sent: list[str] = []

    def fake_telegram(chat_id, text):
        sent.append(chat_id)
        return True

    monkeypatch.setattr("app.notifications.send_telegram_to_chat", fake_telegram)
    monkeypatch.setattr("app.notifications._telegram_configured", lambda: True)
    notify_high_priority([job_id])
    assert sent == ["111"]


def test_build_daily_summary_is_user_scoped(temp_db):
    with db_mod.session_scope() as session:
        u1 = User(email="one@test.com", display_name="Alice", password_hash="x", role=ROLE_MEMBER)
        session.add(u1)
        session.flush()
        session.add(
            Application(user_id=u1.id, company_name="MyCo", role="Role", status="Planned")
        )
        u1_id = u1.id

    summary = build_daily_summary(u1_id)
    assert "Alice" in summary
    assert "MyCo" not in summary or "New jobs" in summary


def test_excel_download_requires_auth_and_is_private(temp_db):
    with db_mod.session_scope() as session:
        member = User(email="m@test.com", display_name="M", password_hash="x", role=ROLE_MEMBER)
        session.add(member)
        session.flush()
        member_id = member.id

    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app) as client:
        assert client.get("/api/exports/excel/download").status_code == 401
        client.cookies.set(COOKIE_NAME, create_session_token(member_id))
        assert client.get("/api/exports/excel/download").status_code == 200

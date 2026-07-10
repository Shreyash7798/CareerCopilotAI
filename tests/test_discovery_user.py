"""Per-user discovery cadence and monitor counts."""

import pytest

import app.db as db_mod
from app.discovery_schedule import compute_discovery_interval_minutes, discovery_summary_for_user, format_interval_label
from app.models import Company, User, UserCompanyMonitor
from app.user_access import enabled_monitor_count


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "database_url", lambda: f"sqlite:///{tmp_path}/disc.db")
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_SessionLocal", None)
    db_mod.init_db()
    yield


def test_format_interval_label():
    assert format_interval_label(160) == "every 2h 40m"
    assert format_interval_label(60) == "every hour"
    assert format_interval_label(120) == "every 2 hours"


def test_enabled_monitor_count_per_user(temp_db):
    with db_mod.session_scope() as session:
        u1 = User(email="a@test.com", display_name="A", password_hash="x", role="member")
        u2 = User(email="b@test.com", display_name="B", password_hash="x", role="member")
        c1 = Company(name="Co1", ats_type="greenhouse")
        c2 = Company(name="Co2", ats_type="lever")
        session.add_all([u1, u2, c1, c2])
        session.flush()
        session.add(UserCompanyMonitor(user_id=u1.id, company_id=c1.id, enabled=True))
        session.add(UserCompanyMonitor(user_id=u2.id, company_id=c1.id, enabled=True))
        session.add(UserCompanyMonitor(user_id=u2.id, company_id=c2.id, enabled=True))

        assert enabled_monitor_count(session, u1.id) == 1
        assert enabled_monitor_count(session, u2.id) == 2
        assert enabled_monitor_count(session) == 2


def test_discovery_summary_uses_user_company_count(temp_db):
    with db_mod.session_scope() as session:
        user = User(email="solo@test.com", display_name="Solo", password_hash="x", role="member")
        companies = [
            Company(name=f"Co{i}", ats_type="greenhouse") for i in range(5)
        ]
        session.add(user)
        session.add_all(companies)
        session.flush()
        for c in companies:
            session.add(UserCompanyMonitor(user_id=user.id, company_id=c.id, enabled=True))

        summary = discovery_summary_for_user(session, user.id)
        assert summary["user_monitored_companies"] == 5
        assert summary["user_discovery_interval_minutes"] == compute_discovery_interval_minutes(5)
        assert summary["target_polls_per_company_per_day"] == 3
        assert "every" in summary["user_interval_label"]


def test_fewer_user_companies_means_shorter_interval(temp_db):
    with db_mod.session_scope() as session:
        admin = User(email="admin@test.com", display_name="Admin", password_hash="x", role="admin")
        member = User(email="mem@test.com", display_name="Mem", password_hash="x", role="member")
        many = [Company(name=f"Big{i}", ats_type="greenhouse") for i in range(69)]
        few = Company(name="SmallCo", ats_type="greenhouse")
        session.add_all([admin, member, few, *many])
        session.flush()
        for c in many:
            session.add(UserCompanyMonitor(user_id=admin.id, company_id=c.id, enabled=True))
        session.add(UserCompanyMonitor(user_id=member.id, company_id=few.id, enabled=True))

        admin_summary = discovery_summary_for_user(session, admin.id)
        member_summary = discovery_summary_for_user(session, member.id)

        assert admin_summary["user_discovery_interval_minutes"] == 160
        assert member_summary["user_discovery_interval_minutes"] == 360
        assert member_summary["shared_server"] is True

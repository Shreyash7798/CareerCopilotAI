"""Catalog sector bulk disable."""

import pytest

import app.db as db_mod
from app.models import Company, User, UserCompanyMonitor
from app.users import COOKIE_NAME, ROLE_MEMBER, create_session_token
from sqlalchemy import select


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "database_url", lambda: f"sqlite:///{tmp_path}/catalog.db")
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_SessionLocal", None)
    db_mod.init_db()
    yield


def test_disable_sector_stops_user_monitors(temp_db):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with db_mod.session_scope() as session:
        user = User(email="u@test.com", display_name="U", password_hash="x", role=ROLE_MEMBER)
        # In the catalog under Consulting & Advisory
        c1 = Company(name="Accenture India", sector="Consulting & Advisory", ats_type="accenture")
        c2 = Company(name="PwC", sector="Consulting & Advisory", ats_type="greenhouse")
        # NOT in the catalog, but same sector in the DB (manually added / renamed)
        c3 = Company(name="My Custom Consultancy", sector="Consulting & Advisory", ats_type="lever")
        # Different sector — must stay enabled
        c4 = Company(name="Stripe", sector="Technology", ats_type="greenhouse")
        session.add_all([user, c1, c2, c3, c4])
        session.flush()
        for c in (c1, c2, c3, c4):
            session.add(UserCompanyMonitor(user_id=user.id, company_id=c.id, enabled=True))
        user_id = user.id
        other_id = c4.id

    app = create_app()
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, create_session_token(user_id))
        resp = client.post(
            "/companies/catalog/disable-sector",
            data={"sector": "Consulting & Advisory"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    with db_mod.session_scope() as session:
        monitors = session.execute(
            select(UserCompanyMonitor).where(UserCompanyMonitor.user_id == user_id)
        ).scalars().all()
        by_company = {m.company_id: m.enabled for m in monitors}
        # Sector companies (catalog or DB-sector match) all disabled
        disabled = [enabled for cid, enabled in by_company.items() if cid != other_id]
        assert disabled and not any(disabled)
        # Other sector untouched
        assert by_company[other_id] is True


def test_disable_sector_does_not_affect_other_users(temp_db):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with db_mod.session_scope() as session:
        u1 = User(email="u1@test.com", display_name="U1", password_hash="x", role=ROLE_MEMBER)
        u2 = User(email="u2@test.com", display_name="U2", password_hash="x", role=ROLE_MEMBER)
        company = Company(name="PwC", sector="Consulting & Advisory", ats_type="greenhouse")
        session.add_all([u1, u2, company])
        session.flush()
        session.add(UserCompanyMonitor(user_id=u1.id, company_id=company.id, enabled=True))
        session.add(UserCompanyMonitor(user_id=u2.id, company_id=company.id, enabled=True))
        u1_id, u2_id, company_id = u1.id, u2.id, company.id

    app = create_app()
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, create_session_token(u1_id))
        client.post(
            "/companies/catalog/disable-sector",
            data={"sector": "Consulting & Advisory"},
            follow_redirects=False,
        )

    with db_mod.session_scope() as session:
        m1 = session.execute(
            select(UserCompanyMonitor).where(
                UserCompanyMonitor.user_id == u1_id,
                UserCompanyMonitor.company_id == company_id,
            )
        ).scalar_one()
        m2 = session.execute(
            select(UserCompanyMonitor).where(
                UserCompanyMonitor.user_id == u2_id,
                UserCompanyMonitor.company_id == company_id,
            )
        ).scalar_one()
        assert m1.enabled is False
        assert m2.enabled is True

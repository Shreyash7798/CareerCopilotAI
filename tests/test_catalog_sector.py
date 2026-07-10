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
        c1 = Company(name="Accenture India", sector="Consulting & Advisory", ats_type="accenture")
        c2 = Company(name="PwC", sector="Consulting & Advisory", ats_type="greenhouse")
        session.add_all([user, c1, c2])
        session.flush()
        session.add(UserCompanyMonitor(user_id=user.id, company_id=c1.id, enabled=True))
        session.add(UserCompanyMonitor(user_id=user.id, company_id=c2.id, enabled=True))
        user_id = user.id

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
        assert all(not m.enabled for m in monitors)

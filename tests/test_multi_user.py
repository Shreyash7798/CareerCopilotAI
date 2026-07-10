"""Multi-user auth, privacy isolation, and account limits."""

import pytest
from sqlalchemy import select

import app.db as db_mod
from app.models import Application, Company, Job, User, UserJobScore
from app.users import COOKIE_NAME, ROLE_ADMIN, ROLE_MEMBER, create_session_token, create_user, max_users


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "database_url", lambda: f"sqlite:///{tmp_path}/multi.db")
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_SessionLocal", None)
    db_mod.init_db()
    yield


def _login(client, user_id: int):
    client.cookies.set(COOKIE_NAME, create_session_token(user_id))


def test_bootstrap_creates_admin(temp_db):
    with db_mod.session_scope() as session:
        users = session.query(User).all()
        assert len(users) == 1
        assert users[0].role == ROLE_ADMIN


def test_user_limit_enforced(temp_db):
    with db_mod.session_scope() as session:
        admin = session.query(User).filter_by(role=ROLE_ADMIN).one()
        admin_id = admin.id
    limit = max_users()
    for i in range(limit - 1):
        create_user(
            email=f"user{i}@test.com",
            password="password123",
            actor_user_id=admin_id,
        )
    with pytest.raises(ValueError, match="User limit"):
        create_user(email="overflow@test.com", password="password123", actor_user_id=admin_id)


def test_applications_isolated_between_users(temp_db):
    with db_mod.session_scope() as session:
        u1 = User(email="one@test.com", display_name="One", password_hash="x", role=ROLE_MEMBER)
        u2 = User(email="two@test.com", display_name="Two", password_hash="x", role=ROLE_MEMBER)
        session.add_all([u1, u2])
        session.flush()
        session.add(Application(user_id=u1.id, company_name="Acme", role="Analyst"))
        session.add(Application(user_id=u2.id, company_name="Beta", role="Consultant"))

    with db_mod.session_scope() as session:
        u2_id = session.execute(select(User.id).where(User.email == "two@test.com")).scalar_one()

    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app, follow_redirects=False) as client:
        _login(client, u2_id)
        resp = client.get("/api/applications")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["company_name"] == "Beta"


def test_cross_user_application_update_blocked(temp_db):
    with db_mod.session_scope() as session:
        u1 = User(email="one@test.com", display_name="One", password_hash="x", role=ROLE_MEMBER)
        u2 = User(email="two@test.com", display_name="Two", password_hash="x", role=ROLE_MEMBER)
        session.add_all([u1, u2])
        session.flush()
        session.add(Application(user_id=u1.id, company_name="Secret Co", role="Role"))

    with db_mod.session_scope() as session:
        u2_id = session.execute(select(User.id).where(User.email == "two@test.com")).scalar_one()
        app_id = session.execute(
            select(Application.id).where(Application.company_name == "Secret Co")
        ).scalar_one()

    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app) as client:
        _login(client, u2_id)
        resp = client.put(
            f"/api/applications/{app_id}",
            json={"company_name": "Hacked", "role": "X", "status": "Planned"},
        )
        assert resp.status_code == 404


def test_jobs_scoped_to_user_scores(temp_db):
    with db_mod.session_scope() as session:
        u1 = User(email="one@test.com", display_name="One", password_hash="x", role=ROLE_MEMBER)
        u2 = User(email="two@test.com", display_name="Two", password_hash="x", role=ROLE_MEMBER)
        company = Company(name="Acme")
        session.add_all([u1, u2, company])
        session.flush()
        job = Job(company_id=company.id, title="Consultant", dedup_key="k1")
        session.add(job)
        session.flush()
        session.add(UserJobScore(user_id=u1.id, job_id=job.id, match_score=80.0, is_high_priority=True))
        session.add(UserJobScore(user_id=u2.id, job_id=job.id, match_score=40.0, is_high_priority=False))
        u1_id = u1.id
        u2_id = u2.id

    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app) as client:
        _login(client, u1_id)
        jobs = client.get("/api/jobs").json()
        assert len(jobs) == 1
        assert jobs[0]["match_score"] == 80.0

        _login(client, u2_id)
        jobs2 = client.get("/api/jobs").json()
        assert jobs2[0]["match_score"] == 40.0


def test_admin_users_page_requires_admin(temp_db):
    with db_mod.session_scope() as session:
        member = User(email="m@test.com", display_name="M", password_hash="x", role=ROLE_MEMBER)
        session.add(member)
        session.flush()
        member_id = member.id

    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app, follow_redirects=False) as client:
        _login(client, member_id)
        assert client.get("/admin/users").status_code == 403

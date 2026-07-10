"""Full-site feature walkthrough with realistic data.

Backtests every user-facing feature end to end: pages render, forms submit,
per-user isolation holds. Run on every PR so 'works in demo, breaks with
real data' bugs get caught before deploy.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

import app.db as db_mod
from app.models import (
    ActivityLog,
    Application,
    Company,
    Job,
    Recruiter,
    User,
    UserCompanyMonitor,
    UserJobScore,
)
from app.users import COOKIE_NAME, ROLE_ADMIN, create_session_token, hash_password


@pytest.fixture()
def site(tmp_path, monkeypatch):
    """App + admin/member clients over a realistically-populated database."""
    monkeypatch.setattr(db_mod, "database_url", lambda: f"sqlite:///{tmp_path}/site.db")
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_SessionLocal", None)
    db_mod.init_db()

    with db_mod.session_scope() as s:
        admin = s.execute(
            select(User).where(User.email == "admin@careercopilot.local")
        ).scalar_one()
        member = User(
            email="member@test.com",
            display_name="Member One",
            password_hash=hash_password("memberpass1"),
            role="member",
        )
        s.add(member)
        s.flush()

        catalog_co = Company(
            name="PwC", sector="Consulting & Advisory", ats_type="greenhouse", enabled=True
        )
        custom_co = Company(
            name="Local Boutique Firm", sector="Consulting & Advisory", ats_type="lever", enabled=True
        )
        bank_co = Company(
            name="Morgan Stanley", sector="Banking & Financial Services",
            ats_type="workday", enabled=True,
        )
        s.add_all([catalog_co, custom_co, bank_co])
        s.flush()

        for co in (catalog_co, custom_co, bank_co):
            s.add(UserCompanyMonitor(user_id=admin.id, company_id=co.id, enabled=True))
        s.add(UserCompanyMonitor(user_id=member.id, company_id=catalog_co.id, enabled=True))

        jobs = []
        for i, (co, score, hp) in enumerate(
            [(catalog_co, 88.0, True), (custom_co, 55.5, False), (bank_co, None, False)]
        ):
            job = Job(
                company_id=co.id,
                title=f"Consultant {i}",
                location="Mumbai" if i % 2 == 0 else "Pune",
                dedup_key=f"walk-{i}",
                is_active=True,
                description="Strategy consulting role requiring analytics and stakeholder management",
                score_breakdown='[{"name":"role_fit","score":0.9,"reason":"strong"}]',
            )
            s.add(job)
            s.flush()
            jobs.append(job)
            s.add(
                UserJobScore(
                    user_id=admin.id,
                    job_id=job.id,
                    match_score=score,
                    jd_fit_score=score,
                    is_high_priority=hp,
                    score_breakdown=job.score_breakdown,
                )
            )

        s.add(
            Application(
                user_id=admin.id,
                job_id=jobs[0].id,
                company_name="PwC",
                role="Consultant 0",
                status="Applied",
            )
        )
        s.add(Recruiter(name="Rita Recruiter", company_id=catalog_co.id, public_email="r@pwc.com"))
        s.add(ActivityLog(category="discovery", message="Pipeline run: walkthrough", user_id=None))
        admin_id, member_id = admin.id, member.id

    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app, follow_redirects=False) as admin_client:
        admin_client.cookies.set(COOKIE_NAME, create_session_token(admin_id))
        with TestClient(app, follow_redirects=False) as member_client:
            member_client.cookies.set(COOKIE_NAME, create_session_token(member_id))
            yield {
                "admin": admin_client,
                "member": member_client,
                "admin_id": admin_id,
                "member_id": member_id,
            }


# ---------------------------------------------------------------- pages render

ADMIN_PAGES = [
    "/", "/jobs", "/jobs?high_priority=true", "/jobs?q=consultant&location=Mumbai",
    "/applications", "/applications?status=Applied", "/companies", "/analytics",
    "/recruiters", "/activity", "/profile", "/settings", "/help",
    "/admin/users", "/admin/status",
]


def test_all_admin_pages_render(site):
    for path in ADMIN_PAGES:
        resp = site["admin"].get(path)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}: {resp.text[:200]}"
        assert "Internal Server Error" not in resp.text, path


def test_member_pages_render_and_admin_pages_forbidden(site):
    for path in ("/", "/jobs", "/applications", "/companies", "/profile", "/settings", "/help"):
        resp = site["member"].get(path)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"
    for path in ("/admin/users", "/admin/status"):
        resp = site["member"].get(path, follow_redirects=False)
        assert resp.status_code in (303, 403), f"{path} -> {resp.status_code}"


def test_job_detail_renders(site):
    listing = site["admin"].get("/jobs")
    assert listing.status_code == 200
    with db_mod.session_scope() as s:
        job_id = s.execute(select(Job.id).order_by(Job.id)).scalars().first()
    detail = site["admin"].get(f"/jobs/{job_id}")
    assert detail.status_code == 200


# ---------------------------------------------------------------- applications

def test_application_crud_via_forms(site):
    c = site["admin"]
    create = c.post(
        "/applications/new",
        data={"company_name": "FormCo", "role": "Analyst", "status": "Planned"},
    )
    assert create.status_code == 303
    with db_mod.session_scope() as s:
        app_row = s.execute(
            select(Application).where(Application.company_name == "FormCo")
        ).scalar_one()
        app_id = app_row.id
    update = c.post(
        f"/applications/{app_id}/update",
        data={"company_name": "FormCo", "role": "Senior Analyst", "status": "Applied"},
    )
    assert update.status_code == 303
    delete = c.post(f"/applications/{app_id}/delete")
    assert delete.status_code == 303
    with db_mod.session_scope() as s:
        assert s.get(Application, app_id) is None


def test_member_cannot_touch_admin_application(site):
    with db_mod.session_scope() as s:
        app_id = s.execute(
            select(Application.id).where(Application.user_id == site["admin_id"])
        ).scalars().first()
    resp = site["member"].post(f"/applications/{app_id}/delete")
    assert resp.status_code == 303
    with db_mod.session_scope() as s:
        assert s.get(Application, app_id) is not None, "member deleted another user's application!"


# ---------------------------------------------------------------- companies

def test_companies_add_manual_and_unmonitor(site):
    c = site["admin"]
    add = c.post(
        "/companies/new",
        data={
            "name": "Manual Added Co", "sector": "Technology", "ats_type": "greenhouse",
            "board": "manualco", "priority": "normal", "enabled": "true",
        },
    )
    assert add.status_code == 303
    with db_mod.session_scope() as s:
        co = s.execute(select(Company).where(Company.name == "Manual Added Co")).scalar_one()
        monitor = s.execute(
            select(UserCompanyMonitor).where(
                UserCompanyMonitor.user_id == site["admin_id"],
                UserCompanyMonitor.company_id == co.id,
            )
        ).scalar_one()
        assert monitor.enabled is True


def test_add_all_then_disable_all_roundtrip(site):
    """The exact flow the user reported: Add all → Disable all must disable > 0."""
    c = site["member"]
    sector = "Banking & Financial Services"

    add = c.post("/companies/catalog/add-sector", data={"sector": sector})
    assert add.status_code == 303
    with db_mod.session_scope() as s:
        enabled_before = s.execute(
            select(UserCompanyMonitor)
            .join(Company, Company.id == UserCompanyMonitor.company_id)
            .where(
                UserCompanyMonitor.user_id == site["member_id"],
                UserCompanyMonitor.enabled.is_(True),
                Company.sector == sector,
            )
        ).scalars().all()
    assert len(enabled_before) > 0, "Add all created no monitors for this user"

    disable = c.post("/companies/catalog/disable-sector", data={"sector": sector})
    assert disable.status_code == 303
    with db_mod.session_scope() as s:
        still_enabled = s.execute(
            select(UserCompanyMonitor)
            .join(Company, Company.id == UserCompanyMonitor.company_id)
            .where(
                UserCompanyMonitor.user_id == site["member_id"],
                UserCompanyMonitor.enabled.is_(True),
                Company.sector == sector,
            )
        ).scalars().all()
        assert still_enabled == [], f"{len(still_enabled)} monitors still enabled after Disable all"

        log = s.execute(
            select(ActivityLog)
            .where(ActivityLog.message.like("Catalog bulk disable:%"))
            .order_by(ActivityLog.timestamp.desc())
        ).scalars().first()
        assert log is not None and "0 companies" not in log.message, log.message


def test_disable_all_does_not_touch_other_user(site):
    with db_mod.session_scope() as s:
        admin_enabled = s.execute(
            select(UserCompanyMonitor)
            .join(Company, Company.id == UserCompanyMonitor.company_id)
            .where(
                UserCompanyMonitor.user_id == site["admin_id"],
                Company.sector == "Banking & Financial Services",
                UserCompanyMonitor.enabled.is_(True),
            )
        ).scalars().all()
    assert admin_enabled, "admin monitors were wiped by the member's Disable all"


# ---------------------------------------------------------------- recruiters

def test_recruiter_create_and_delete(site):
    c = site["admin"]
    add = c.post(
        "/recruiters/new",
        data={"name": "New Recruiter", "company_name": "PwC", "public_email": "n@pwc.com"},
    )
    assert add.status_code == 303
    with db_mod.session_scope() as s:
        rec = s.execute(select(Recruiter).where(Recruiter.name == "New Recruiter")).scalar_one()
        rec_id = rec.id
    delete = c.post(f"/recruiters/{rec_id}/delete")
    assert delete.status_code == 303


# ---------------------------------------------------------------- settings

def test_settings_profile_and_notifications_save(site):
    c = site["admin"]
    prof = c.post(
        "/settings/profile",
        data={
            "full_name": "Admin Tester", "email": "admin@t.com", "phone": "",
            "current_employer": "", "experience_years": "4",
            "preferred_locations": "Mumbai, Pune", "acceptable_locations": "Remote",
            "preferred_domains": "consulting", "interests": "strategy",
            "skills": "excel, sql", "preferred_companies": "", "avoided_companies": "",
        },
    )
    assert prof.status_code == 303
    notif = c.post(
        "/settings/notifications",
        data={"telegram_chat_id": "12345", "telegram_enabled": "1", "weekly_summary": "1"},
    )
    assert notif.status_code == 303
    with db_mod.session_scope() as s:
        user = s.get(User, site["admin_id"])
        prefs = json.loads(user.preferences_json)
        assert prefs["profile"]["full_name"] == "Admin Tester"
        assert prefs["notifications"]["telegram_chat_id"] == "12345"
        assert prefs["notifications"]["weekly_summary"] is True
        assert prefs["notifications"]["email_enabled"] is False  # unchecked box saved as off


# ---------------------------------------------------------------- API surface

def test_api_endpoints_respond(site):
    c = site["admin"]
    assert c.get("/api/version").status_code == 200
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/jobs").status_code == 200
    assert c.get("/api/applications").status_code == 200
    assert c.get("/api/companies").status_code == 200
    assert c.get("/api/analytics").status_code == 200
    assert c.get("/api/admin/status").status_code == 200
    assert c.get("/api/summary/daily").status_code == 200
    assert c.get("/api/exports/excel/download").status_code == 200


def test_member_api_admin_forbidden(site):
    resp = site["member"].get("/api/admin/status")
    assert resp.status_code == 403


# ---------------------------------------------------------------- admin users

def test_admin_creates_member_account(site):
    resp = site["admin"].post(
        "/admin/users/create",
        data={"email": "newuser@test.com", "display_name": "New User", "password": "newpass123"},
    )
    assert resp.status_code == 303
    with db_mod.session_scope() as s:
        assert s.execute(select(User).where(User.email == "newuser@test.com")).scalar_one()

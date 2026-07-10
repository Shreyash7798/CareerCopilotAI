"""Server-rendered dashboard pages (mobile-first, works on iOS Safari and
desktop browsers; installable as a PWA)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app import company_sources, resume_engine
from app.analytics import compute_analytics
from app.config import (
    get_company_catalog,
    get_profile,
    get_settings,
    get_sources_config,
    save_settings,
)
from app.deps import get_current_user, require_admin
from app.discovery_schedule import discovery_schedule_summary, effective_discovery_interval_minutes
from app.scheduler import refresh_discovery_schedule
from app.job_visibility import job_age_label, job_status_badge, visible_jobs_filter
from app.db import get_db, session_scope
from app.models import (
    APPLICATION_STATUSES,
    ATS_TYPES,
    COMPANY_PRIORITIES,
    ActivityLog,
    Application,
    Company,
    InterviewPrep,
    Job,
    Recruiter,
    Resume,
    User,
    UserJobScore,
    log_activity,
)
from app.user_access import (
    apply_monitor_to_company,
    attach_user_scores,
    company_monitor_map,
    hydrate_job_from_score,
    monitor_for_user,
    sync_monitor_from_company,
    user_job_score,
)
from app.user_prefs import get_user_preferences, get_user_profile_dict, save_user_preferences
from app.users import (
    ROLE_ADMIN,
    ROLE_MEMBER,
    create_session_token,
    create_user,
    list_users,
    max_users,
    set_user_active,
    set_user_password,
    user_count,
)
from app.users import authenticate as authenticate_user

router = APIRouter(include_in_schema=False)

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["job_age_label"] = job_age_label


def _parse_optional_float(value: str | None) -> float:
    if value is None or str(value).strip() == "":
        return 0.0
    try:
        return max(0.0, min(100.0, float(value)))
    except ValueError:
        return 0.0


def _parse_bool_query(value: str | None) -> bool:
    if value is None:
        return False
    return str(value).lower() in ("1", "true", "yes", "on")


def _location_tokens(location: str) -> list[str]:
    """Split a location filter into matchable parts (Mumbai, Pune, Remote, …)."""
    tokens = [t.strip() for t in re.split(r"[,/|]+", location) if t.strip()]
    return tokens or [location.strip()]


def _apply_job_filters(
    stmt,
    *,
    q: str,
    location: str,
    company: str,
    source: str,
    high_priority: bool,
    user_scored: bool = False,
):
    q = (q or "").strip()
    location = (location or "").strip()
    company = (company or "").strip()
    source = (source or "").strip()

    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Job.title.ilike(like),
                Job.description.ilike(like),
                Job.company.has(Company.name.ilike(like)),
            )
        )
    if location:
        tokens = _location_tokens(location)
        stmt = stmt.where(or_(*[Job.location.ilike(f"%{token}%") for token in tokens]))
    if company:
        stmt = stmt.where(Job.company.has(Company.name.ilike(f"%{company}%")))
    if source:
        stmt = stmt.where(Job.source == source)
    if high_priority:
        if user_scored:
            stmt = stmt.where(UserJobScore.is_high_priority.is_(True))
        else:
            stmt = stmt.where(Job.is_high_priority.is_(True))
    return stmt


def _jobs_query_string(filters: dict, page: int | None = None) -> str:
    """Build a URL query string for jobs list filters (pagination-safe)."""
    params: list[tuple[str, str]] = []
    for key in ("q", "location", "company", "source"):
        val = (filters.get(key) or "").strip()
        if val:
            params.append((key, val))
    min_score = filters.get("min_score") or 0
    if min_score and float(min_score) > 0:
        params.append(("min_score", str(int(min_score) if float(min_score) == int(min_score) else min_score)))
    if filters.get("high_priority"):
        params.append(("high_priority", "true"))
    page_num = page if page is not None else filters.get("page")
    if page_num and int(page_num) > 1:
        params.append(("page", str(int(page_num))))
    return urlencode(params)


templates.env.filters["jobs_query"] = _jobs_query_string


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    from app.auth import auth_required

    if not auth_required():
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"error": None, "next_path": next}
    )


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    from app.auth import COOKIE_NAME, auth_required
    from app.users import authenticate

    if not auth_required():
        return RedirectResponse("/", status_code=303)
    user = authenticate(email, password)
    if user is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid email or password.", "next_path": next},
            status_code=401,
        )
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        create_session_token(user.id),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return response


@router.post("/logout")
def logout():
    from app.auth import COOKIE_NAME

    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    analytics = compute_analytics(db, user_id=user.id)
    from app.user_access import enabled_monitor_count

    monitored_count = enabled_monitor_count(db)
    has_cv = bool(user.cv_path)
    visible = visible_jobs_filter()
    score_join = and_(UserJobScore.job_id == Job.id, UserJobScore.user_id == user.id)
    recent_high = attach_user_scores(
        db,
        db.execute(
            select(Job)
            .join(UserJobScore, score_join)
            .where(visible, UserJobScore.is_high_priority.is_(True))
            .order_by(UserJobScore.match_score.desc(), Job.discovered_at.desc())
            .limit(8)
        ).scalars().all(),
        user.id,
    )
    recent_top = attach_user_scores(
        db,
        db.execute(
            select(Job)
            .join(UserJobScore, score_join)
            .where(visible)
            .order_by(UserJobScore.match_score.desc(), Job.discovered_at.desc())
            .limit(8)
        ).scalars().all(),
        user.id,
    )
    recent_logs = (
        db.execute(
            select(ActivityLog)
            .where(or_(ActivityLog.user_id == user.id, ActivityLog.user_id.is_(None)))
            .order_by(ActivityLog.timestamp.desc())
            .limit(10)
        )
        .scalars()
        .all()
    )
    follow_ups = (
        db.execute(
            select(Application)
            .where(
                Application.user_id == user.id,
                Application.follow_up_date.isnot(None),
                Application.status.notin_(["Rejected", "Withdrawn", "Offer"]),
            )
            .order_by(Application.follow_up_date)
            .limit(5)
        )
        .scalars()
        .all()
    )
    discovery_mins = effective_discovery_interval_minutes()
    profile = get_user_profile_dict(user)
    display_name = (profile.get("full_name") or user.display_name or "").strip()
    if display_name:
        display_name = display_name.split()[0]
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "active": "dashboard",
            "analytics": analytics,
            "recent_high": recent_high,
            "recent_top": recent_top,
            "recent_logs": recent_logs,
            "follow_ups": follow_ups,
            "monitored_count": monitored_count,
            "has_cv": has_cv,
            "display_name": display_name,
            "discovery_mins": discovery_mins,
            "current_user": user,
            "is_admin": user.role == ROLE_ADMIN,
        },
    )


@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(
    request: Request,
    db: Session = Depends(get_db),
    q: str = "",
    location: str = "",
    company: str = "",
    source: str = "",
    min_score: str | None = Query(None),
    high_priority: str | None = Query(None),
    page: int = 1,
):
    user = get_current_user(request)
    page_size = 25
    min_score_val = _parse_optional_float(min_score)
    high_priority_val = _parse_bool_query(high_priority)
    visible = visible_jobs_filter()
    score_join = and_(UserJobScore.job_id == Job.id, UserJobScore.user_id == user.id)
    stmt = (
        select(Job)
        .options(joinedload(Job.company))
        .join(UserJobScore, score_join)
        .where(visible, UserJobScore.match_score >= min_score_val)
    )
    count_stmt = (
        select(func.count(Job.id))
        .join(UserJobScore, score_join)
        .where(visible, UserJobScore.match_score >= min_score_val)
    )
    q = (q or "").strip()
    location = (location or "").strip()
    company = (company or "").strip()
    source = (source or "").strip()
    stmt = _apply_job_filters(
        stmt,
        q=q,
        location=location,
        company=company,
        source=source,
        high_priority=high_priority_val,
        user_scored=True,
    )
    count_stmt = _apply_job_filters(
        count_stmt,
        q=q,
        location=location,
        company=company,
        source=source,
        high_priority=high_priority_val,
        user_scored=True,
    )
    total = db.execute(count_stmt).scalar() or 0
    jobs = attach_user_scores(
        db,
        db.execute(
            stmt.order_by(UserJobScore.match_score.desc(), Job.discovered_at.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        .scalars()
        .all(),
        user.id,
    )
    filters = {
        "q": q,
        "location": location,
        "company": company,
        "source": source,
        "min_score": min_score_val,
        "high_priority": high_priority_val,
        "page": page,
    }
    has_filters = bool(
        q or location or company or source or min_score_val > 0 or high_priority_val
    )
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {
            "active": "jobs",
            "jobs": jobs,
            "total": total,
            "page": page,
            "pages": max(1, -(-total // page_size)),
            "filters": filters,
            "has_filters": has_filters,
        },
    )


@router.post("/jobs/import-linkedin")
def jobs_import_linkedin(request: Request, url: str = Form(...)):
    from fastapi.responses import JSONResponse

    from app.linkedin_import import import_linkedin_job

    user = get_current_user(request)
    try:
        result = import_linkedin_job(url, user_id=user.id)
        return JSONResponse(result)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"detail": str(exc)}, status_code=400)


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request)
    job = db.get(Job, job_id)
    if job is None:
        return RedirectResponse("/jobs", status_code=303)
    score = user_job_score(db, user.id, job_id)
    if score is None:
        return RedirectResponse("/jobs", status_code=303)
    hydrate_job_from_score(job, score)
    breakdown = json.loads(job.score_breakdown) if job.score_breakdown else []
    jd_breakdown = json.loads(job.jd_fit_breakdown) if job.jd_fit_breakdown else []
    resumes = (
        db.execute(
            select(Resume)
            .where(Resume.job_id == job_id, Resume.user_id == user.id)
            .order_by(Resume.created_at.desc())
        )
        .scalars()
        .all()
    )
    tailored = [r for r in resumes if r.kind == "tailored"]
    cover_letters = [r for r in resumes if r.kind == "cover_letter"]
    prep_row = (
        db.execute(
            select(InterviewPrep)
            .where(InterviewPrep.job_id == job_id, InterviewPrep.user_id == user.id)
            .order_by(InterviewPrep.created_at.desc())
        )
        .scalars()
        .first()
    )
    interview_prep = json.loads(prep_row.content_json) if prep_row else None
    recruiters = []
    if job.company_id:
        recruiters = (
            db.execute(select(Recruiter).where(Recruiter.company_id == job.company_id).order_by(Recruiter.name))
            .scalars()
            .all()
        )
    has_master = resume_engine.resolve_master_docx_path(
        user.cv_path,
        profile_json=user.profile_json,
    ) is not None
    profile = get_user_profile_dict(user)
    has_profile = bool(profile.get("full_name") or user.profile_json)
    is_tracked = (
        db.execute(
            select(Application.id).where(
                Application.job_id == job_id, Application.user_id == user.id
            )
        ).scalar()
        is not None
    )
    status_badge = job_status_badge(job, is_tracked=is_tracked)
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "active": "jobs",
            "job": job,
            "breakdown": breakdown,
            "jd_breakdown": jd_breakdown,
            "resumes": tailored,
            "cover_letters": cover_letters,
            "interview_prep": interview_prep,
            "recruiters": recruiters,
            "has_master": has_master,
            "has_profile": has_profile,
            "is_tracked": is_tracked,
            "status_badge": status_badge,
        },
    )


@router.post("/jobs/{job_id}/track")
def track_job(request: Request, job_id: int):
    user = get_current_user(request)
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is not None and user_job_score(session, user.id, job_id) is not None:
            existing = session.execute(
                select(Application).where(
                    Application.job_id == job_id, Application.user_id == user.id
                )
            ).scalars().first()
            if existing is None:
                session.add(
                    Application(
                        user_id=user.id,
                        job_id=job.id,
                        company_name=job.company.name if job.company else None,
                        role=job.title,
                        status="Planned",
                    )
                )
                log_activity(session, "app", f"Tracked job #{job.id}", user_id=user.id)
    return RedirectResponse("/applications", status_code=303)


@router.get("/applications", response_class=HTMLResponse)
def applications_page(request: Request, db: Session = Depends(get_db), status: str = ""):
    user = get_current_user(request)
    stmt = (
        select(Application)
        .options(joinedload(Application.job))
        .where(Application.user_id == user.id)
        .order_by(Application.updated_at.desc())
    )
    if status:
        stmt = stmt.where(Application.status == status)
    apps = db.execute(stmt).scalars().all()
    return templates.TemplateResponse(
        request,
        "applications.html",
        {
            "active": "applications",
            "applications": apps,
            "statuses": APPLICATION_STATUSES,
            "status_filter": status,
        },
    )


@router.post("/applications/new")
def application_create(
    request: Request,
    company_name: str = Form(""),
    role: str = Form(""),
    status: str = Form("Planned"),
    date_applied: str = Form(""),
    follow_up_date: str = Form(""),
    notes: str = Form(""),
):
    user = get_current_user(request)
    with session_scope() as session:
        session.add(
            Application(
                user_id=user.id,
                company_name=company_name or None,
                role=role or None,
                status=status if status in APPLICATION_STATUSES else "Planned",
                date_applied=_parse_date(date_applied),
                follow_up_date=_parse_date(follow_up_date),
                notes=notes or None,
            )
        )
        log_activity(session, "app", f"Application created: {company_name}", user_id=user.id)
    return RedirectResponse("/applications", status_code=303)


@router.post("/applications/{app_id}/update")
def application_update(
    request: Request,
    app_id: int,
    company_name: str = Form(""),
    role: str = Form(""),
    status: str = Form("Planned"),
    date_applied: str = Form(""),
    follow_up_date: str = Form(""),
    interview_stages: str = Form(""),
    outcome: str = Form(""),
    notes: str = Form(""),
):
    user = get_current_user(request)
    with session_scope() as session:
        application = session.get(Application, app_id)
        if application is not None and application.user_id == user.id:
            application.company_name = company_name or None
            application.role = role or None
            if status in APPLICATION_STATUSES:
                application.status = status
            application.date_applied = _parse_date(date_applied)
            application.follow_up_date = _parse_date(follow_up_date)
            application.interview_stages = interview_stages or None
            application.outcome = outcome or None
            application.notes = notes or None
            log_activity(session, "app", f"Application updated: {application.company_name}", user_id=user.id)
    return RedirectResponse("/applications", status_code=303)


@router.post("/applications/{app_id}/delete")
def application_delete(request: Request, app_id: int):
    user = get_current_user(request)
    with session_scope() as session:
        application = session.get(Application, app_id)
        if application is not None and application.user_id == user.id:
            session.delete(application)
    return RedirectResponse("/applications", status_code=303)


@router.get("/companies", response_class=HTMLResponse)
def companies_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    monitors = company_monitor_map(db, user.id)
    companies = db.execute(select(Company).order_by(Company.name)).scalars().all()
    display_companies = []
    stats = {}
    for company in companies:
        monitor = monitors.get(company.id)
        display = apply_monitor_to_company(company, monitor)
        display_companies.append(display)
        active_jobs = [j for j in company.jobs if j.is_active]
        user_scores = {
            s.job_id: s
            for s in db.execute(
                select(UserJobScore).where(
                    UserJobScore.user_id == user.id,
                    UserJobScore.job_id.in_([j.id for j in active_jobs] or [0]),
                )
            ).scalars()
        }
        scored_jobs = [hydrate_job_from_score(j, user_scores[j.id]) for j in active_jobs if j.id in user_scores]
        applied = db.execute(
            select(func.count(Application.id)).where(
                Application.company_name == company.name,
                Application.user_id == user.id,
            )
        ).scalar() or 0
        stats[company.id] = {
            "active_jobs": len(scored_jobs),
            "high_priority": sum(1 for j in scored_jobs if j.is_high_priority),
            "top_locations": sorted(
                {(j.location or "").split(",")[0].strip() for j in scored_jobs if j.location}
            )[:4],
            "applications": applied,
            "avg_score": round(sum(j.match_score for j in scored_jobs) / len(scored_jobs), 1)
            if scored_jobs
            else 0,
            "ats_config": company_sources.parse_ats_config(company),
        }
    catalog = get_company_catalog()
    existing_names = {c.name.lower() for c in companies if c.ats_type}
    return templates.TemplateResponse(
        request,
        "companies.html",
        {
            "active": "companies",
            "companies": display_companies,
            "stats": stats,
            "catalog": catalog.get("sectors", []),
            "existing_names": existing_names,
            "ats_types": ATS_TYPES,
            "priorities": COMPANY_PRIORITIES,
        },
    )


def _apply_company_form(
    company: Company,
    *,
    name: str,
    sector: str,
    country: str,
    ats_type: str,
    career_url: str,
    board: str,
    host: str,
    tenant: str,
    site: str,
    search_text: str,
    link_selector: str,
    location: str,
    f_TPR: str,
    render: bool,
    keywords: str,
    refresh_interval_minutes: str,
    priority: str,
    enabled: bool,
    recruiter_search_enabled: bool,
    notes: str,
) -> None:
    company.name = name.strip()
    company.sector = sector.strip() or None
    company.country = country.strip() or None
    company.ats_type = ats_type if ats_type in ATS_TYPES else None
    company.career_url = career_url.strip() or None
    ats_config = {
        k: v
        for k, v in {
            "board": board.strip(),
            "host": host.strip(),
            "tenant": tenant.strip(),
            "site": site.strip(),
            "search_text": search_text.strip(),
            "keywords": search_text.strip(),
            "link_selector": link_selector.strip(),
            "location": location.strip(),
            "f_TPR": f_TPR.strip(),
        }.items()
        if v
    }
    if render:
        ats_config["render"] = True
    company.ats_config = json.dumps(ats_config) if ats_config else None
    company.keywords = keywords.strip() or None
    company.refresh_interval_minutes = (
        int(refresh_interval_minutes) if refresh_interval_minutes.strip().isdigit() else None
    )
    company.priority = priority if priority in COMPANY_PRIORITIES else "normal"
    company.enabled = enabled
    company.recruiter_search_enabled = recruiter_search_enabled
    company.notes = notes.strip() or None


@router.post("/companies/new")
def company_create(
    request: Request,
    name: str = Form(...),
    sector: str = Form(""),
    country: str = Form(""),
    ats_type: str = Form(""),
    career_url: str = Form(""),
    board: str = Form(""),
    host: str = Form(""),
    tenant: str = Form(""),
    site: str = Form(""),
    search_text: str = Form(""),
    link_selector: str = Form(""),
    location: str = Form(""),
    f_TPR: str = Form(""),
    render: bool = Form(False),
    keywords: str = Form(""),
    refresh_interval_minutes: str = Form(""),
    priority: str = Form("normal"),
    enabled: bool = Form(False),
    recruiter_search_enabled: bool = Form(False),
    notes: str = Form(""),
):
    user = get_current_user(request)
    with session_scope() as session:
        company = session.execute(
            select(Company).where(Company.name == name.strip())
        ).scalar_one_or_none()
        if company is None:
            company = Company(name=name.strip())
            session.add(company)
        _apply_company_form(
            company,
            name=name, sector=sector, country=country, ats_type=ats_type,
            career_url=career_url, board=board, host=host, tenant=tenant,
            site=site, search_text=search_text, link_selector=link_selector,
            location=location, f_TPR=f_TPR,
            render=render, keywords=keywords,
            refresh_interval_minutes=refresh_interval_minutes,
            priority=priority, enabled=enabled,
            recruiter_search_enabled=recruiter_search_enabled, notes=notes,
        )
        sync_monitor_from_company(session, user.id, company)
        log_activity(session, "config", f"Company added/updated: {name}", user_id=user.id)
    refresh_discovery_schedule()
    return RedirectResponse("/companies", status_code=303)


@router.post("/companies/{company_id}/update")
def company_update(
    request: Request,
    company_id: int,
    name: str = Form(...),
    sector: str = Form(""),
    country: str = Form(""),
    ats_type: str = Form(""),
    career_url: str = Form(""),
    board: str = Form(""),
    host: str = Form(""),
    tenant: str = Form(""),
    site: str = Form(""),
    search_text: str = Form(""),
    link_selector: str = Form(""),
    location: str = Form(""),
    f_TPR: str = Form(""),
    render: bool = Form(False),
    keywords: str = Form(""),
    refresh_interval_minutes: str = Form(""),
    priority: str = Form("normal"),
    enabled: bool = Form(False),
    recruiter_search_enabled: bool = Form(False),
    notes: str = Form(""),
):
    user = get_current_user(request)
    with session_scope() as session:
        company = session.get(Company, company_id)
        if company is not None:
            _apply_company_form(
                company,
                name=name, sector=sector, country=country, ats_type=ats_type,
                career_url=career_url, board=board, host=host, tenant=tenant,
                site=site, search_text=search_text, link_selector=link_selector,
                location=location, f_TPR=f_TPR,
                render=render, keywords=keywords,
                refresh_interval_minutes=refresh_interval_minutes,
                priority=priority, enabled=enabled,
                recruiter_search_enabled=recruiter_search_enabled, notes=notes,
            )
            sync_monitor_from_company(session, user.id, company)
            log_activity(session, "config", f"Company updated: {company.name}", user_id=user.id)
    refresh_discovery_schedule()
    return RedirectResponse("/companies", status_code=303)


@router.post("/companies/{company_id}/toggle")
def company_toggle(request: Request, company_id: int):
    user = get_current_user(request)
    with session_scope() as session:
        company = session.get(Company, company_id)
        if company is not None:
            monitor = sync_monitor_from_company(session, user.id, company)
            monitor.enabled = not monitor.enabled
            log_activity(
                session,
                "config",
                f"Company {'enabled' if monitor.enabled else 'disabled'}: {company.name}",
                user_id=user.id,
            )
    refresh_discovery_schedule()
    return RedirectResponse("/companies", status_code=303)


@router.post("/companies/{company_id}/delete")
def company_delete(request: Request, company_id: int):
    user = get_current_user(request)
    with session_scope() as session:
        company = session.get(Company, company_id)
        monitor = monitor_for_user(session, user.id, company_id) if company else None
        if company is not None and monitor is not None:
            monitor.enabled = False
            log_activity(session, "config", f"Company unmonitored: {company.name}", user_id=user.id)
    refresh_discovery_schedule()
    return RedirectResponse("/companies", status_code=303)


@router.post("/companies/catalog/add")
def company_catalog_add(request: Request, sector: str = Form(...), name: str = Form(...)):
    user = get_current_user(request)
    catalog = get_company_catalog()
    entry = None
    for sec in catalog.get("sectors", []):
        if sec.get("name") != sector:
            continue
        for item in sec.get("companies", []):
            if item.get("name") == name:
                entry = dict(item)
                entry["sector"] = sector
                break
    if entry is not None:
        with session_scope() as session:
            company = session.execute(
                select(Company).where(Company.name == entry["name"])
            ).scalar_one_or_none()
            if company is None:
                company = Company(name=entry["name"])
                session.add(company)
            company.sector = entry.get("sector")
            company.country = entry.get("country")
            company.ats_type = entry.get("ats_type")
            company.career_url = entry.get("career_url")
            company.ats_config = (
                json.dumps(entry["ats_config"]) if entry.get("ats_config") else None
            )
            # Entries with caveats (bot-protected sites) start disabled so the
            # user can validate them with Test Connection first.
            company.enabled = not entry.get("caveat")
            company.notes = entry.get("caveat") or company.notes
            sync_monitor_from_company(session, user.id, company)
            log_activity(session, "config", f"Company added from catalog: {entry['name']}", user_id=user.id)
    return RedirectResponse("/companies", status_code=303)


@router.post("/companies/catalog/add-sector")
def company_catalog_add_sector(request: Request, sector: str = Form(...)):
    user = get_current_user(request)
    catalog = get_company_catalog()
    added = 0
    with session_scope() as session:
        for sec in catalog.get("sectors", []):
            if sec.get("name") != sector:
                continue
            for item in sec.get("companies", []):
                name = item.get("name")
                if not name:
                    continue
                company = session.execute(
                    select(Company).where(Company.name == name)
                ).scalar_one_or_none()
                if company is not None and company.ats_type:
                    continue
                if company is None:
                    company = Company(name=name)
                    session.add(company)
                company.sector = sector
                company.country = item.get("country")
                company.ats_type = item.get("ats_type")
                company.career_url = item.get("career_url")
                company.ats_config = (
                    json.dumps(item["ats_config"]) if item.get("ats_config") else None
                )
                company.enabled = not item.get("caveat")
                company.notes = item.get("caveat") or company.notes
                sync_monitor_from_company(session, user.id, company)
                added += 1
            log_activity(session, "config", f"Catalog bulk add: {added} companies from {sector}", user_id=user.id)
            break
    refresh_discovery_schedule()
    return RedirectResponse("/companies", status_code=303)


@router.get("/analytics", response_class=HTMLResponse)
def analytics_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    return templates.TemplateResponse(
        request,
        "analytics.html",
        {"active": "analytics", "analytics": compute_analytics(db, user_id=user.id)},
    )


@router.get("/recruiters", response_class=HTMLResponse)
def recruiters_page(request: Request, db: Session = Depends(get_db)):
    recruiters = db.execute(select(Recruiter).order_by(Recruiter.name)).scalars().all()
    return templates.TemplateResponse(
        request,
        "recruiters.html",
        {"active": "recruiters", "recruiters": recruiters},
    )


@router.post("/recruiters/new")
def recruiter_create(
    name: str = Form(...),
    company_name: str = Form(""),
    department: str = Form(""),
    linkedin_url: str = Form(""),
    public_email: str = Form(""),
    notes: str = Form(""),
):
    with session_scope() as session:
        company_id = None
        if company_name:
            company = session.execute(
                select(Company).where(Company.name == company_name)
            ).scalar_one_or_none()
            if company is None:
                company = Company(name=company_name)
                session.add(company)
                session.flush()
            company_id = company.id
        session.add(
            Recruiter(
                name=name,
                company_id=company_id,
                department=department or None,
                linkedin_url=linkedin_url or None,
                public_email=public_email or None,
                notes=notes or None,
            )
        )
    return RedirectResponse("/recruiters", status_code=303)


@router.post("/recruiters/{rec_id}/delete")
def recruiter_delete(rec_id: int):
    with session_scope() as session:
        recruiter = session.get(Recruiter, rec_id)
        if recruiter is not None:
            session.delete(recruiter)
    return RedirectResponse("/recruiters", status_code=303)


@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    profile = get_user_profile_dict(user)
    parsed = json.loads(user.profile_json) if user.profile_json else None
    return templates.TemplateResponse(
        request,
        "profile.html",
        {
            "active": "profile",
            "profile": profile,
            "cv_path": user.cv_path,
            "parsed": parsed,
        },
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    user = get_current_user(request)
    prefs = get_user_preferences(user)
    discovery_summary = discovery_schedule_summary()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "active": "settings",
            "settings": {"profile": prefs.get("profile", {}), "scoring": prefs.get("scoring", {}), "notifications": prefs.get("notifications", {})},
            "sources": get_sources_config(refresh=True) if user.role == ROLE_ADMIN else {},
            "discovery_summary": discovery_summary,
            "is_admin": user.role == ROLE_ADMIN,
            "server_settings": get_settings(refresh=True) if user.role == ROLE_ADMIN else None,
        },
    )


@router.post("/settings/profile")
def settings_profile_update(
    request: Request,
    full_name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    current_employer: str = Form(""),
    experience_years: str = Form("0"),
    preferred_locations: str = Form(""),
    acceptable_locations: str = Form(""),
    preferred_domains: str = Form(""),
    interests: str = Form(""),
    skills: str = Form(""),
    preferred_companies: str = Form(""),
    avoided_companies: str = Form(""),
):
    user = get_current_user(request)
    def as_list(raw: str) -> list[str]:
        return [item.strip() for item in raw.split(",") if item.strip()]

    prefs = get_user_preferences(user)
    profile = prefs.get("profile", {}) or {}
    profile.update(
        {
            "full_name": full_name,
            "email": email,
            "phone": phone,
            "current_employer": current_employer,
            "experience_years": float(experience_years or 0),
            "preferred_locations": as_list(preferred_locations),
            "acceptable_locations": as_list(acceptable_locations),
            "preferred_domains": as_list(preferred_domains),
            "interests": as_list(interests),
            "skills": as_list(skills),
            "preferred_companies": as_list(preferred_companies),
            "avoided_companies": as_list(avoided_companies),
        }
    )
    prefs["profile"] = profile
    save_user_preferences(user.id, prefs)
    from app.pipeline import rescore_all_jobs

    rescore_all_jobs(user.id)
    return RedirectResponse("/settings", status_code=303)


@router.get("/activity", response_class=HTMLResponse)
def activity_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    logs = (
        db.execute(
            select(ActivityLog)
            .where(or_(ActivityLog.user_id == user.id, ActivityLog.user_id.is_(None)))
            .order_by(ActivityLog.timestamp.desc())
            .limit(200)
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "activity.html",
        {"active": "activity", "logs": logs},
    )


@router.get("/admin/users", response_class=HTMLResponse)
def admin_users_page(request: Request):
    admin = require_admin(request)
    return templates.TemplateResponse(
        request,
        "admin_users.html",
        {
            "active": "admin",
            "users": list_users(),
            "max_users": max_users(),
            "user_count": user_count(),
            "current_user": admin,
        },
    )


@router.post("/admin/users/create")
def admin_create_user(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(""),
    role: str = Form(ROLE_MEMBER),
):
    admin = require_admin(request)
    try:
        create_user(
            email=email,
            password=password,
            display_name=display_name,
            role=role,
            actor_user_id=admin.id,
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "admin_users.html",
            {
                "active": "admin",
                "users": list_users(),
                "max_users": max_users(),
                "user_count": user_count(),
                "current_user": admin,
                "error": str(exc),
            },
            status_code=400,
        )
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/password")
def admin_reset_password(
    request: Request,
    user_id: int,
    new_password: str = Form(...),
):
    admin = require_admin(request)
    try:
        set_user_password(user_id, new_password, actor_user_id=admin.id)
    except ValueError as exc:
        return RedirectResponse(f"/admin/users?error={exc}", status_code=303)
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/toggle")
def admin_toggle_user(request: Request, user_id: int):
    admin = require_admin(request)
    users = list_users()
    target = next((u for u in users if u["id"] == user_id), None)
    if target is not None:
        try:
            set_user_active(user_id, not target["is_active"], actor_user_id=admin.id)
        except ValueError:
            pass
    return RedirectResponse("/admin/users", status_code=303)

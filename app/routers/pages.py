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
from sqlalchemy import func, or_, select
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
    UserProfile,
    log_activity,
)

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
    from app.auth import configured_password

    if not configured_password():
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"error": None, "next_path": next}
    )


@router.post("/login")
def login_submit(request: Request, password: str = Form(...), next: str = Form("/")):
    from app.auth import COOKIE_NAME, configured_password, session_token
    import hmac as hmac_mod

    expected = configured_password()
    if not expected:
        return RedirectResponse("/", status_code=303)
    if not hmac_mod.compare_digest(password, expected):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Wrong password.", "next_path": next},
            status_code=401,
        )
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        session_token(expected),
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
    analytics = compute_analytics(db)
    monitored_count = db.execute(
        select(func.count(Company.id)).where(
            Company.ats_type.isnot(None), Company.enabled.is_(True)
        )
    ).scalar() or 0
    has_cv = db.execute(select(UserProfile)).scalars().first() is not None
    recent_high = (
        db.execute(
            select(Job)
            .where(Job.is_high_priority.is_(True), visible_jobs_filter())
            .order_by(Job.discovered_at.desc())
            .limit(8)
        )
        .scalars()
        .all()
    )
    recent_top = (
        db.execute(
            select(Job)
            .where(visible_jobs_filter())
            .order_by(Job.match_score.desc(), Job.discovered_at.desc())
            .limit(8)
        )
        .scalars()
        .all()
    )
    recent_logs = (
        db.execute(select(ActivityLog).order_by(ActivityLog.timestamp.desc()).limit(10)).scalars().all()
    )
    follow_ups = (
        db.execute(
            select(Application)
            .where(
                Application.follow_up_date.isnot(None),
                Application.status.notin_(["Rejected", "Withdrawn", "Offer"]),
            )
            .order_by(Application.follow_up_date)
            .limit(5)
        )
        .scalars()
        .all()
    )
    settings = get_settings()
    discovery_mins = effective_discovery_interval_minutes()
    discovery_summary = discovery_schedule_summary()
    profile = get_profile()
    display_name = (profile.get("full_name") or "").strip()
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
    page_size = 25
    min_score_val = _parse_optional_float(min_score)
    high_priority_val = _parse_bool_query(high_priority)
    visible = visible_jobs_filter()
    stmt = (
        select(Job)
        .options(joinedload(Job.company))
        .where(visible, Job.match_score >= min_score_val)
    )
    count_stmt = select(func.count(Job.id)).where(visible, Job.match_score >= min_score_val)
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
    )
    count_stmt = _apply_job_filters(
        count_stmt,
        q=q,
        location=location,
        company=company,
        source=source,
        high_priority=high_priority_val,
    )
    total = db.execute(count_stmt).scalar() or 0
    jobs = (
        db.execute(
            stmt.order_by(Job.match_score.desc(), Job.discovered_at.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        .scalars()
        .all()
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
def jobs_import_linkedin(url: str = Form(...)):
    from fastapi.responses import JSONResponse

    from app.linkedin_import import import_linkedin_job

    try:
        result = import_linkedin_job(url)
        return JSONResponse(result)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"detail": str(exc)}, status_code=400)


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        return RedirectResponse("/jobs", status_code=303)
    breakdown = json.loads(job.score_breakdown) if job.score_breakdown else []
    resumes = (
        db.execute(select(Resume).where(Resume.job_id == job_id).order_by(Resume.created_at.desc()))
        .scalars()
        .all()
    )
    tailored = [r for r in resumes if r.kind == "tailored"]
    cover_letters = [r for r in resumes if r.kind == "cover_letter"]
    prep_row = (
        db.execute(
            select(InterviewPrep).where(InterviewPrep.job_id == job_id).order_by(InterviewPrep.created_at.desc())
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
    has_master = False
    has_profile = False
    profile_row = db.execute(select(UserProfile)).scalars().first()
    if profile_row:
        has_master = resume_engine.resolve_master_docx_path(
            profile_row.cv_path,
            profile_json=profile_row.profile_json,
        ) is not None
    if profile_row and (profile_row.profile_json or profile_row.full_name):
        has_profile = True
    if get_profile().get("full_name"):
        has_profile = True
    is_tracked = (
        db.execute(select(Application.id).where(Application.job_id == job_id)).scalar()
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
def track_job(job_id: int):
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is not None:
            existing = session.execute(
                select(Application).where(Application.job_id == job_id)
            ).scalars().first()
            if existing is None:
                session.add(
                    Application(
                        job_id=job.id,
                        company_name=job.company.name if job.company else None,
                        role=job.title,
                        status="Planned",
                    )
                )
    return RedirectResponse("/applications", status_code=303)


@router.get("/applications", response_class=HTMLResponse)
def applications_page(request: Request, db: Session = Depends(get_db), status: str = ""):
    stmt = (
        select(Application)
        .options(joinedload(Application.job))
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
    company_name: str = Form(""),
    role: str = Form(""),
    status: str = Form("Planned"),
    date_applied: str = Form(""),
    follow_up_date: str = Form(""),
    notes: str = Form(""),
):
    with session_scope() as session:
        session.add(
            Application(
                company_name=company_name or None,
                role=role or None,
                status=status if status in APPLICATION_STATUSES else "Planned",
                date_applied=_parse_date(date_applied),
                follow_up_date=_parse_date(follow_up_date),
                notes=notes or None,
            )
        )
    return RedirectResponse("/applications", status_code=303)


@router.post("/applications/{app_id}/update")
def application_update(
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
    with session_scope() as session:
        application = session.get(Application, app_id)
        if application is not None:
            application.company_name = company_name or None
            application.role = role or None
            if status in APPLICATION_STATUSES:
                application.status = status
            application.date_applied = _parse_date(date_applied)
            application.follow_up_date = _parse_date(follow_up_date)
            application.interview_stages = interview_stages or None
            application.outcome = outcome or None
            application.notes = notes or None
    return RedirectResponse("/applications", status_code=303)


@router.post("/applications/{app_id}/delete")
def application_delete(app_id: int):
    with session_scope() as session:
        application = session.get(Application, app_id)
        if application is not None:
            session.delete(application)
    return RedirectResponse("/applications", status_code=303)


@router.get("/companies", response_class=HTMLResponse)
def companies_page(request: Request, db: Session = Depends(get_db)):
    companies = db.execute(select(Company).order_by(Company.name)).scalars().all()
    stats = {}
    for company in companies:
        active_jobs = [j for j in company.jobs if j.is_active]
        applied = db.execute(
            select(func.count(Application.id)).where(Application.company_name == company.name)
        ).scalar() or 0
        stats[company.id] = {
            "active_jobs": len(active_jobs),
            "high_priority": sum(1 for j in active_jobs if j.is_high_priority),
            "top_locations": sorted(
                {(j.location or "").split(",")[0].strip() for j in active_jobs if j.location}
            )[:4],
            "applications": applied,
            "avg_score": round(
                sum(j.match_score for j in active_jobs) / len(active_jobs), 1
            )
            if active_jobs
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
            "companies": companies,
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
        log_activity(session, "config", f"Company added/updated: {name}")
    refresh_discovery_schedule()
    return RedirectResponse("/companies", status_code=303)


@router.post("/companies/{company_id}/update")
def company_update(
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
            log_activity(session, "config", f"Company updated: {company.name}")
    refresh_discovery_schedule()
    return RedirectResponse("/companies", status_code=303)


@router.post("/companies/{company_id}/toggle")
def company_toggle(company_id: int):
    with session_scope() as session:
        company = session.get(Company, company_id)
        if company is not None:
            company.enabled = not company.enabled
            log_activity(
                session,
                "config",
                f"Company {'enabled' if company.enabled else 'disabled'}: {company.name}",
            )
    refresh_discovery_schedule()
    return RedirectResponse("/companies", status_code=303)


@router.post("/companies/{company_id}/delete")
def company_delete(company_id: int):
    with session_scope() as session:
        company = session.get(Company, company_id)
        if company is not None:
            if company.jobs or company.recruiters:
                # Keep the employer-intelligence record; just stop monitoring.
                company.ats_type = None
                company.enabled = False
                log_activity(session, "config", f"Company unmonitored: {company.name}")
            else:
                session.delete(company)
                log_activity(session, "config", f"Company deleted: {company.name}")
    return RedirectResponse("/companies", status_code=303)


@router.post("/companies/catalog/add")
def company_catalog_add(sector: str = Form(...), name: str = Form(...)):
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
            log_activity(session, "config", f"Company added from catalog: {entry['name']}")
    return RedirectResponse("/companies", status_code=303)


@router.post("/companies/catalog/add-sector")
def company_catalog_add_sector(sector: str = Form(...)):
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
                added += 1
            log_activity(session, "config", f"Catalog bulk add: {added} companies from {sector}")
            break
    refresh_discovery_schedule()
    return RedirectResponse("/companies", status_code=303)


@router.get("/analytics", response_class=HTMLResponse)
def analytics_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "analytics.html",
        {"active": "analytics", "analytics": compute_analytics(db)},
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
    profile = get_profile()
    row = db.execute(select(UserProfile)).scalars().first()
    parsed = json.loads(row.profile_json) if row and row.profile_json else None
    return templates.TemplateResponse(
        request,
        "profile.html",
        {
            "active": "profile",
            "profile": profile,
            "cv_path": row.cv_path if row else None,
            "parsed": parsed,
        },
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    settings = get_settings(refresh=True)
    sources = get_sources_config(refresh=True)
    discovery_summary = discovery_schedule_summary()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "active": "settings",
            "settings": settings,
            "sources": sources,
            "discovery_summary": discovery_summary,
        },
    )


@router.post("/settings/profile")
def settings_profile_update(
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
    def as_list(raw: str) -> list[str]:
        return [item.strip() for item in raw.split(",") if item.strip()]

    settings = get_settings(refresh=True)
    profile = settings.get("profile", {}) or {}
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
    settings["profile"] = profile
    save_settings(settings)
    # Re-rank existing jobs for the updated preferences.
    from app.pipeline import rescore_all_jobs

    rescore_all_jobs()
    return RedirectResponse("/settings", status_code=303)


@router.get("/activity", response_class=HTMLResponse)
def activity_page(request: Request, db: Session = Depends(get_db)):
    logs = (
        db.execute(select(ActivityLog).order_by(ActivityLog.timestamp.desc()).limit(200)).scalars().all()
    )
    return templates.TemplateResponse(
        request,
        "activity.html",
        {"active": "activity", "logs": logs},
    )

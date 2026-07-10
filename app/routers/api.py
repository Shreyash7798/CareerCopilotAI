"""JSON REST API under /api. Everything the dashboard does is also available
programmatically, which keeps the platform scriptable and extensible."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app import cv_parser, resume_engine
from app.analytics import compute_analytics
from app.config import data_dir, get_company_catalog, get_profile, save_profile
from app.db import get_db, session_scope
from app.deps import get_current_user, require_admin
from app.cover_letter_engine import generate_cover_letter
from app.job_visibility import job_age_dict, visible_jobs_filter
from app.exporter import export_excel, export_google_sheets
from app.interview_prep import build_interview_prep
from app.models import (
    APPLICATION_STATUSES,
    ATS_TYPES,
    COMPANY_PRIORITIES,
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
    application_owned,
    attach_user_scores,
    hydrate_job_from_score,
    interview_prep_owned,
    resume_owned,
    sync_monitor_from_company,
    user_job_score,
)
from app.user_prefs import get_user_preferences, get_user_profile_dict, save_user_preferences, user_cv_dir
from app.notifications import build_daily_summary, send_daily_summary, send_test_notification
from app.pipeline import rescore_all_jobs, run_pipeline
from app.startup import (
    backfill_job_locations,
    bootstrap_starter_pack_for_user,
    enabled_company_count,
    ensure_accenture,
)

from app.deploy_hook import run_deploy
from app.ops import get_system_status
from app.users import list_users, max_users, user_count
from app.version import deploy_info

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/version")
def api_version():
    """Deployed git revision — use to confirm OCI pulled the latest main."""
    return deploy_info()


@router.get("/health")
def api_health():
    """Lightweight liveness probe — does not run discovery or heavy DB work."""
    return {"ok": True}


@router.get("/crawl4ai/health")
def api_crawl4ai_health():
    """Check whether the optional Crawl4AI sidecar is reachable."""
    from app.sources.crawl4ai_client import health_check

    return health_check()


@router.post("/deploy/hook")
def api_deploy_hook(authorization: str | None = Header(None)):
    """Pull latest main and restart. Requires `app.deploy_token` bearer auth."""
    import hmac as hmac_mod

    from app.config import get_settings

    expected = str((get_settings().get("app", {}) or {}).get("deploy_token", "") or "")
    if not expected:
        raise HTTPException(503, "Deploy hook disabled (set app.deploy_token in settings.yaml)")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")
    token = authorization[7:].strip()
    if not hmac_mod.compare_digest(token, expected):
        raise HTTPException(401, "Invalid deploy token")
    try:
        return run_deploy()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, str(exc)) from exc


def _job_dict(job: Job) -> dict:
    data = {
        "id": job.id,
        "title": job.title,
        "company": job.company.name if job.company else None,
        "location": job.location,
        "url": job.url,
        "source": job.source,
        "match_score": job.match_score,
        "jd_fit_score": job.jd_fit_score,
        "is_high_priority": job.is_high_priority,
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
        "discovered_at": job.discovered_at.isoformat() if job.discovered_at else None,
        "score_breakdown": json.loads(job.score_breakdown) if job.score_breakdown else [],
        "jd_fit_breakdown": json.loads(job.jd_fit_breakdown) if job.jd_fit_breakdown else [],
    }
    data.update(job_age_dict(job))
    return data


# ---------------------------------------------------------------- pipeline


@router.post("/pipeline/run")
def api_run_pipeline(request: Request, db: Session = Depends(get_db)):
    """Trigger a discovery run in a background subprocess."""
    from datetime import datetime, timedelta

    from sqlalchemy import select

    from app.discovery_runner import is_discovery_running, run_discovery_subprocess
    from app.models import ActivityLog

    user = get_current_user(request)
    now = datetime.utcnow()

    def _naive(ts):
        return ts.replace(tzinfo=None) if getattr(ts, "tzinfo", None) else ts

    if is_discovery_running():
        return {
            "status": "skipped",
            "message": "Discovery is already running. Check Activity in a few minutes.",
        }

    recent = db.execute(
        select(ActivityLog.timestamp)
        .where(
            ActivityLog.category == "discovery",
            ActivityLog.message.like("Discovery started:%"),
            ActivityLog.user_id == user.id,
        )
        .order_by(ActivityLog.timestamp.desc())
        .limit(1)
    ).scalar_one_or_none()
    if recent is not None and now - _naive(recent) < timedelta(minutes=15):
        return {
            "status": "skipped",
            "message": "You triggered discovery recently. Please wait ~15 minutes.",
        }

    recent_global = db.execute(
        select(ActivityLog.timestamp)
        .where(
            ActivityLog.category == "discovery",
            ActivityLog.message.like("Pipeline run:%"),
        )
        .order_by(ActivityLog.timestamp.desc())
        .limit(1)
    ).scalar_one_or_none()
    if recent_global is not None and now - _naive(recent_global) < timedelta(minutes=15):
        return {
            "status": "skipped",
            "message": "Discovery ran recently. It auto-runs every 3 hours — no need to click again.",
        }

    started = db.execute(
        select(ActivityLog.timestamp)
        .where(
            ActivityLog.category == "discovery",
            ActivityLog.message.like("Discovery started:%"),
        )
        .order_by(ActivityLog.timestamp.desc())
        .limit(1)
    ).scalar_one_or_none()
    if started is not None:
        started_naive = _naive(started)
        recent_naive = _naive(recent) if recent is not None else None
        in_progress = recent_naive is None or started_naive > recent_naive
        if in_progress and now - started_naive < timedelta(minutes=30):
            return {
                "status": "skipped",
                "message": "Discovery is already running. Check Activity in a few minutes.",
            }

    outcome = run_discovery_subprocess()
    if not outcome.get("started"):
        return {
            "status": "skipped",
            "message": "Discovery is already running. Check Activity in a few minutes.",
        }
    return {"status": "started", "pid": outcome.get("pid")}


@router.post("/pipeline/run-sync")
def api_run_pipeline_sync():
    result = run_pipeline()
    return {
        "sources_run": result.sources_run,
        "sources_failed": result.sources_failed,
        "sources_skipped": result.sources_skipped,
        "fetched": result.fetched,
        "filtered_out": result.filtered_out,
        "duplicates": result.duplicates,
        "new_jobs": result.new_jobs,
        "high_priority": result.high_priority,
        "deactivated": result.deactivated,
        "reactivated": result.reactivated,
        "errors": result.errors,
    }


@router.post("/quick-start")
def api_quick_start(request: Request):
    """Backfill locations + rescore, then run discovery in a subprocess."""
    from app.discovery_runner import run_discovery_subprocess
    from app.user_access import enabled_monitor_count

    user = get_current_user(request)
    companies_changed = 0
    locations_backfilled = 0

    with session_scope() as session:
        if enabled_monitor_count(session, user.id) == 0:
            companies_changed = bootstrap_starter_pack_for_user(session, user.id)
            log_activity(
                session,
                "config",
                f"Quick start: starter pack enabled ({companies_changed} companies)",
                user_id=user.id,
            )
        if ensure_accenture(session):
            companies_changed += 1
        locations_backfilled = backfill_job_locations(session)
        if locations_backfilled:
            log_activity(
                session,
                "discovery",
                f"Quick start: backfilled location on {locations_backfilled} jobs",
                user_id=user.id,
            )

    if locations_backfilled:
        rescore_all_jobs()

    from sqlalchemy import func

    with session_scope() as session:
        jobs_total = session.execute(select(func.count(Job.id))).scalar() or 0
        high_priority = (
            session.execute(select(func.count(Job.id)).where(Job.is_high_priority.is_(True))).scalar() or 0
        )
        enabled = enabled_monitor_count(session, user.id)

    discovery = run_discovery_subprocess()

    return {
        "status": "started",
        "companies_enabled": enabled,
        "companies_changed": companies_changed,
        "locations_backfilled": locations_backfilled,
        "jobs_total": jobs_total,
        "high_priority": high_priority,
        "message": "Discovery running in background; refresh the dashboard in a minute.",
    }


# ---------------------------------------------------------------- jobs


class LinkedInImportIn(BaseModel):
    url: str


@router.post("/jobs/import-linkedin")
def api_import_linkedin(request: Request, payload: LinkedInImportIn):
    from app.linkedin_import import import_linkedin_job

    user = get_current_user(request)
    try:
        return import_linkedin_job(payload.url, user_id=user.id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc


@router.get("/jobs")
def api_jobs(
    request: Request,
    db: Session = Depends(get_db),
    q: str | None = None,
    location: str | None = None,
    min_score: float = 0,
    high_priority: bool = False,
    limit: int = 100,
    offset: int = 0,
):
    user = get_current_user(request)
    score_join = and_(UserJobScore.job_id == Job.id, UserJobScore.user_id == user.id)
    stmt = (
        select(Job)
        .join(UserJobScore, score_join)
        .where(visible_jobs_filter(), UserJobScore.match_score >= min_score)
    )
    if q:
        stmt = stmt.where(Job.title.ilike(f"%{q}%"))
    if location:
        stmt = stmt.where(Job.location.ilike(f"%{location}%"))
    if high_priority:
        stmt = stmt.where(UserJobScore.is_high_priority.is_(True))
    stmt = (
        stmt.order_by(UserJobScore.match_score.desc(), Job.discovered_at.desc())
        .limit(limit)
        .offset(offset)
    )
    jobs = attach_user_scores(db, db.execute(stmt).scalars().all(), user.id)
    return [_job_dict(j) for j in jobs]


@router.get("/jobs/{job_id}")
def api_job(request: Request, job_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request)
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    score = user_job_score(db, user.id, job_id)
    if score is None:
        raise HTTPException(404, "Job not found")
    hydrate_job_from_score(job, score)
    data = _job_dict(job)
    data["description"] = job.description
    return data


# ---------------------------------------------------------------- applications


class ApplicationIn(BaseModel):
    job_id: int | None = None
    company_name: str | None = None
    role: str | None = None
    date_applied: datetime | None = None
    status: str = "Planned"
    notes: str | None = None
    follow_up_date: datetime | None = None
    interview_stages: str | None = None
    outcome: str | None = None


@router.get("/applications")
def api_applications(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    apps = (
        db.execute(
            select(Application)
            .where(Application.user_id == user.id)
            .order_by(Application.updated_at.desc())
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": a.id,
            "job_id": a.job_id,
            "company_name": a.company_name,
            "role": a.role,
            "date_applied": a.date_applied.isoformat() if a.date_applied else None,
            "status": a.status,
            "notes": a.notes,
            "follow_up_date": a.follow_up_date.isoformat() if a.follow_up_date else None,
            "interview_stages": a.interview_stages,
            "outcome": a.outcome,
        }
        for a in apps
    ]


@router.post("/applications")
def api_create_application(request: Request, payload: ApplicationIn):
    user = get_current_user(request)
    if payload.status not in APPLICATION_STATUSES:
        raise HTTPException(400, f"Status must be one of {APPLICATION_STATUSES}")
    with session_scope() as session:
        data = payload.model_dump()
        data["user_id"] = user.id
        if payload.job_id:
            job = session.get(Job, payload.job_id)
            if job is None or user_job_score(session, user.id, payload.job_id) is None:
                raise HTTPException(404, "Job not found")
            data.setdefault("company_name", None)
            data["company_name"] = data["company_name"] or (job.company.name if job.company else None)
            data["role"] = data["role"] or job.title
        application = Application(**data)
        session.add(application)
        session.flush()
        log_activity(
            session,
            "app",
            f"Application created: {application.company_name} / {application.role}",
            user_id=user.id,
        )
        return {"id": application.id}


@router.put("/applications/{app_id}")
def api_update_application(request: Request, app_id: int, payload: ApplicationIn):
    user = get_current_user(request)
    if payload.status not in APPLICATION_STATUSES:
        raise HTTPException(400, f"Status must be one of {APPLICATION_STATUSES}")
    with session_scope() as session:
        application = application_owned(session, app_id, user.id)
        if application is None:
            raise HTTPException(404, "Application not found")
        for key, value in payload.model_dump().items():
            setattr(application, key, value)
        log_activity(
            session,
            "app",
            f"Application updated: {application.company_name} / {application.role}",
            user_id=user.id,
        )
        return {"id": application.id}


@router.delete("/applications/{app_id}")
def api_delete_application(request: Request, app_id: int):
    user = get_current_user(request)
    with session_scope() as session:
        application = application_owned(session, app_id, user.id)
        if application is None:
            raise HTTPException(404, "Application not found")
        session.delete(application)
        return {"deleted": app_id}


# ---------------------------------------------------------------- companies


class CompanyIn(BaseModel):
    name: str
    sector: str | None = None
    country: str | None = None
    ats_type: str | None = None
    career_url: str | None = None
    ats_config: dict | None = None
    keywords: str | None = None
    refresh_interval_minutes: int | None = None
    priority: str = "normal"
    enabled: bool = True
    recruiter_search_enabled: bool = False
    notes: str | None = None


def _company_dict(company: Company) -> dict:
    from app.company_sources import parse_ats_config

    return {
        "id": company.id,
        "name": company.name,
        "sector": company.sector,
        "country": company.country,
        "ats_type": company.ats_type,
        "career_url": company.career_url,
        "ats_config": parse_ats_config(company),
        "keywords": company.keywords,
        "refresh_interval_minutes": company.refresh_interval_minutes,
        "priority": company.priority,
        "enabled": company.enabled,
        "recruiter_search_enabled": company.recruiter_search_enabled,
        "is_preferred": company.is_preferred,
        "last_run_at": company.last_run_at.isoformat() if company.last_run_at else None,
        "last_run_status": company.last_run_status,
        "notes": company.notes,
    }


def _apply_company_payload(company: Company, payload: CompanyIn) -> None:
    if payload.ats_type is not None and payload.ats_type not in ATS_TYPES + [""]:
        raise HTTPException(400, f"ats_type must be one of {ATS_TYPES}")
    company.name = payload.name.strip()
    company.sector = payload.sector
    company.country = payload.country
    company.ats_type = payload.ats_type or None
    company.career_url = payload.career_url
    company.ats_config = json.dumps(payload.ats_config) if payload.ats_config else None
    company.keywords = payload.keywords
    company.refresh_interval_minutes = payload.refresh_interval_minutes
    company.priority = payload.priority if payload.priority in COMPANY_PRIORITIES else "normal"
    company.enabled = payload.enabled
    company.recruiter_search_enabled = payload.recruiter_search_enabled
    company.notes = payload.notes


@router.get("/companies")
def api_companies(db: Session = Depends(get_db), monitored_only: bool = False):
    stmt = select(Company).order_by(Company.name)
    if monitored_only:
        stmt = stmt.where(Company.ats_type.isnot(None))
    return [_company_dict(c) for c in db.execute(stmt).scalars()]


@router.post("/companies")
def api_create_company(request: Request, payload: CompanyIn):
    user = get_current_user(request)
    with session_scope() as session:
        company = session.execute(
            select(Company).where(Company.name == payload.name.strip())
        ).scalar_one_or_none()
        if company is None:
            company = Company(name=payload.name.strip())
            session.add(company)
        _apply_company_payload(company, payload)
        sync_monitor_from_company(session, user.id, company)
        session.flush()
        log_activity(session, "config", f"Company added/updated via API: {company.name}", user_id=user.id)
        return {"id": company.id}


@router.put("/companies/{company_id}")
def api_update_company(request: Request, company_id: int, payload: CompanyIn):
    user = get_current_user(request)
    with session_scope() as session:
        company = session.get(Company, company_id)
        if company is None:
            raise HTTPException(404, "Company not found")
        _apply_company_payload(company, payload)
        sync_monitor_from_company(session, user.id, company)
        log_activity(session, "config", f"Company updated via API: {company.name}", user_id=user.id)
        return {"id": company.id}


@router.delete("/companies/{company_id}")
def api_delete_company(request: Request, company_id: int):
    user = get_current_user(request)
    with session_scope() as session:
        company = session.get(Company, company_id)
        if company is None:
            raise HTTPException(404, "Company not found")
        from app.user_access import monitor_for_user

        monitor = monitor_for_user(session, user.id, company_id)
        if monitor is not None:
            monitor.enabled = False
            return {"unmonitored": company_id}
        return {"status": "not_monitored"}


@router.post("/companies/{company_id}/test")
def api_test_company(company_id: int, db: Session = Depends(get_db)):
    """Run the company's connector once and report what came back, so users
    can validate a configuration from the dashboard without SSH."""
    from app.company_sources import entry_from_company
    from app.sources import REGISTRY

    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(404, "Company not found")
    entry = entry_from_company(company)
    if entry is None:
        raise HTTPException(400, "Company has no ATS type configured")
    fetcher = REGISTRY.get(entry["type"])
    if fetcher is None:
        raise HTTPException(400, f"No connector for type '{entry['type']}'")
    try:
        jobs = fetcher(entry)
    except Exception as exc:  # noqa: BLE001 - reported to the user
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "jobs_found": 0}
    sample = f"{jobs[0].title} ({jobs[0].location})" if jobs else None
    return {"ok": True, "jobs_found": len(jobs), "sample": sample}


@router.get("/companies/catalog")
def api_company_catalog():
    return get_company_catalog()


# ---------------------------------------------------------------- recruiters


class RecruiterIn(BaseModel):
    name: str
    company_name: str | None = None
    department: str | None = None
    linkedin_url: str | None = None
    public_email: str | None = None
    related_requisitions: str | None = None
    notes: str | None = None


@router.get("/recruiters")
def api_recruiters(db: Session = Depends(get_db)):
    recs = db.execute(select(Recruiter).order_by(Recruiter.name)).scalars().all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "company": r.company.name if r.company else None,
            "department": r.department,
            "linkedin_url": r.linkedin_url,
            "public_email": r.public_email,
            "related_requisitions": r.related_requisitions,
            "notes": r.notes,
        }
        for r in recs
    ]


@router.post("/recruiters")
def api_create_recruiter(payload: RecruiterIn):
    with session_scope() as session:
        company_id = None
        if payload.company_name:
            company = session.execute(
                select(Company).where(Company.name == payload.company_name)
            ).scalar_one_or_none()
            if company is None:
                company = Company(name=payload.company_name)
                session.add(company)
                session.flush()
            company_id = company.id
        recruiter = Recruiter(
            name=payload.name,
            company_id=company_id,
            department=payload.department,
            linkedin_url=payload.linkedin_url,
            public_email=payload.public_email,
            related_requisitions=payload.related_requisitions,
            notes=payload.notes,
        )
        session.add(recruiter)
        session.flush()
        return {"id": recruiter.id}


@router.delete("/recruiters/{rec_id}")
def api_delete_recruiter(rec_id: int):
    with session_scope() as session:
        recruiter = session.get(Recruiter, rec_id)
        if recruiter is None:
            raise HTTPException(404, "Recruiter not found")
        session.delete(recruiter)
        return {"deleted": rec_id}


# ---------------------------------------------------------------- profile / CV


@router.get("/profile")
def api_profile():
    return get_profile()


@router.post("/profile/cv")
async def api_upload_cv(request: Request, file: UploadFile):
    user = get_current_user(request)
    suffix = Path(file.filename or "cv").suffix.lower()
    if suffix not in (".pdf", ".docx", ".doc", ".txt"):
        raise HTTPException(400, "Upload a PDF, DOCX or TXT resume")
    cv_dir = user_cv_dir(user.id)
    target = cv_dir / f"master{suffix}"
    target.write_bytes(await file.read())

    stored_path = target
    if suffix == ".doc":
        converted = resume_engine.convert_doc_to_docx(target)
        if converted:
            stored_path = converted

    parsed = cv_parser.parse_cv(stored_path)

    prefs = get_user_preferences(user)
    profile = prefs.get("profile", {}) or {}
    for field in ("full_name", "email", "phone"):
        if parsed.get(field):
            profile[field] = parsed[field]
    if parsed.get("experience_years"):
        profile["experience_years"] = parsed["experience_years"]
    if parsed.get("skills"):
        merged = list(dict.fromkeys((profile.get("skills") or []) + parsed["skills"]))
        profile["skills"] = merged
    prefs["profile"] = profile

    with session_scope() as session:
        db_user = session.get(User, user.id)
        if db_user is None:
            raise HTTPException(404, "User not found")
        db_user.cv_path = str(stored_path)
        db_user.profile_json = json.dumps(parsed)
        db_user.preferences_json = json.dumps(prefs)
        if stored_path.suffix.lower() != ".docx":
            resume_engine.write_tailor_master_docx(parsed.get("raw_text") or "", parsed=parsed)
        log_activity(session, "profile", f"CV uploaded and parsed: {file.filename}", user_id=user.id)

    rescored = rescore_all_jobs(user.id)
    return {
        "parsed": {k: v for k, v in parsed.items() if k != "raw_text"},
        "cv_path": str(target),
        "rescored_jobs": rescored,
    }


@router.post("/jobs/rescore")
def api_rescore_jobs(request: Request):
    user = get_current_user(request)
    return {"rescored": rescore_all_jobs(user.id)}


# ---------------------------------------------------------------- resumes


@router.post("/resumes/tailor/{job_id}")
def api_tailor_resume(request: Request, job_id: int):
    user = get_current_user(request)
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None or user_job_score(session, user.id, job_id) is None:
            raise HTTPException(404, "Job not found")
        db_user = session.get(User, user.id)
        master = resume_engine.resolve_master_docx_path(
            db_user.cv_path if db_user else None,
            profile_json=db_user.profile_json if db_user else None,
        )
        if master is None:
            raise HTTPException(
                400,
                "Upload your master resume on the Profile page (DOCX preferred; PDF/TXT also work).",
            )

        try:
            result = resume_engine.tailor_resume(
                master,
                job_title=job.title,
                company=job.company.name if job.company else "company",
                job_description=job.description or "",
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(500, f"Could not write tailored resume: {exc}") from exc
        resume = Resume(
            user_id=user.id,
            job_id=job.id,
            kind="tailored",
            file_path=result["docx"],
            pdf_path=result["pdf"],
            matched_keywords=result["matched_keywords_json"],
        )
        session.add(resume)
        session.flush()
        log_activity(session, "resume", f"Tailored resume generated for job #{job.id}: {job.title}", user_id=user.id)
        return {
            "id": resume.id,
            "docx": result["docx"],
            "pdf": result["pdf"],
            "matched_keywords": result["matched_keywords"],
        }


@router.post("/jobs/{job_id}/cover-letter")
def api_cover_letter(request: Request, job_id: int):
    user = get_current_user(request)
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None or user_job_score(session, user.id, job_id) is None:
            raise HTTPException(404, "Job not found")
        cv_json = json.loads(user.profile_json) if user.profile_json else None
        profile = get_user_profile_dict(user)
        if not profile.get("full_name") and not (cv_json and cv_json.get("full_name")):
            raise HTTPException(400, "Upload your CV on the Profile page first")
        result = generate_cover_letter(
            job_title=job.title,
            company=job.company.name if job.company else "the company",
            job_description=job.description or "",
            cv_json=cv_json,
        )
        resume = Resume(
            user_id=user.id,
            job_id=job.id,
            kind="cover_letter",
            file_path=result["docx"],
            matched_keywords=result["matched_keywords_json"],
        )
        session.add(resume)
        session.flush()
        log_activity(session, "resume", f"Cover letter generated for job #{job.id}: {job.title}", user_id=user.id)
        return {
            "id": resume.id,
            "docx": result["docx"],
            "matched_keywords": result["matched_keywords"],
            "preview": result["text"][:2000],
        }


@router.post("/jobs/{job_id}/interview-prep")
def api_interview_prep(request: Request, job_id: int):
    user = get_current_user(request)
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None or user_job_score(session, user.id, job_id) is None:
            raise HTTPException(404, "Job not found")
        score = user_job_score(session, user.id, job_id)
        breakdown = json.loads(score.score_breakdown) if score and score.score_breakdown else []
        prep = build_interview_prep(
            job_title=job.title,
            company=job.company.name if job.company else "",
            job_description=job.description or "",
            location=job.location or "",
            score_breakdown=breakdown,
        )
        row = InterviewPrep(user_id=user.id, job_id=job.id, content_json=prep["content_json"])
        session.add(row)
        session.flush()
        log_activity(session, "app", f"Interview prep generated for job #{job.id}", user_id=user.id)
        prep["id"] = row.id
        return prep


@router.get("/jobs/{job_id}/interview-prep")
def api_get_interview_prep(request: Request, job_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request)
    row = (
        db.execute(
            select(InterviewPrep)
            .where(InterviewPrep.job_id == job_id, InterviewPrep.user_id == user.id)
            .order_by(InterviewPrep.created_at.desc())
        )
        .scalars()
        .first()
    )
    if row is None:
        raise HTTPException(404, "No interview prep for this job yet")
    data = json.loads(row.content_json)
    data["id"] = row.id
    data["created_at"] = row.created_at.isoformat()
    return data


@router.get("/resumes")
def api_resumes(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    rows = (
        db.execute(
            select(Resume).where(Resume.user_id == user.id).order_by(Resume.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "job_id": r.job_id,
            "kind": r.kind,
            "file_path": r.file_path,
            "pdf_path": r.pdf_path,
            "created_at": r.created_at.isoformat(),
            "matched_keywords": json.loads(r.matched_keywords) if r.matched_keywords else [],
        }
        for r in rows
    ]


@router.get("/resumes/{resume_id}/download")
def api_download_resume(request: Request, resume_id: int, fmt: str = "docx", db: Session = Depends(get_db)):
    user = get_current_user(request)
    resume = resume_owned(db, resume_id, user.id)
    if resume is None:
        raise HTTPException(404, "Resume not found")
    path = resume.pdf_path if fmt == "pdf" else resume.file_path
    if not path or not Path(path).exists():
        raise HTTPException(404, f"No {fmt} file available for this resume")
    return FileResponse(path, filename=Path(path).name)


# ---------------------------------------------------------------- exports & summaries


@router.post("/exports/excel")
def api_export_excel(request: Request):
    user = get_current_user(request)
    path = export_excel(user.id)
    return {"path": str(path)}


@router.get("/exports/excel/download")
def api_download_excel(request: Request):
    user = get_current_user(request)
    path = export_excel(user.id)
    return FileResponse(str(path), filename=f"careercopilot_{user.id}.xlsx")


@router.post("/exports/google-sheets")
def api_export_google_sheets(request: Request):
    user = get_current_user(request)
    spreadsheet_id = export_google_sheets(user.id)
    if not spreadsheet_id:
        return {"status": "disabled"}
    return {"status": "synced", "spreadsheet_id": spreadsheet_id}


@router.get("/summary/daily")
def api_daily_summary(request: Request):
    user = get_current_user(request)
    return {"summary": build_daily_summary(user.id)}


@router.post("/summary/daily/send")
def api_send_daily_summary(request: Request):
    user = get_current_user(request)
    from app.notifications import build_daily_summary, deliver_to_user

    text = build_daily_summary(user.id)
    outcome = deliver_to_user(user, "CareerCopilot daily summary", text)
    with session_scope() as session:
        log_activity(
            session,
            "notify",
            f"Daily summary sent manually (telegram={outcome['telegram']}, email={outcome['email']})",
            user_id=user.id,
        )
    return {"status": "sent", **outcome}


@router.post("/notifications/test")
def api_test_notifications(request: Request):
    user = get_current_user(request)
    return send_test_notification(user)


# ---------------------------------------------------------------- analytics


@router.get("/analytics")
def api_analytics(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    return compute_analytics(db, user_id=user.id)


@router.get("/admin/users")
def api_admin_users(request: Request):
    require_admin(request)
    return {"users": list_users(), "max_users": max_users(), "user_count": user_count()}


@router.get("/admin/status")
def api_admin_status(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    return get_system_status(db)

"""JSON REST API under /api. Everything the dashboard does is also available
programmatically, which keeps the platform scriptable and extensible."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import cv_parser, resume_engine
from app.analytics import compute_analytics
from app.config import data_dir, get_company_catalog, get_profile, save_profile
from app.cover_letter_engine import generate_cover_letter
from app.db import get_db, session_scope
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
    UserProfile,
    log_activity,
)
from app.notifications import build_daily_summary, send_daily_summary, send_test_notification
from app.pipeline import rescore_all_jobs, run_pipeline
from app.startup import (
    backfill_job_locations,
    bootstrap_starter_pack,
    enabled_company_count,
    ensure_accenture,
)

router = APIRouter(prefix="/api", tags=["api"])


def _job_dict(job: Job) -> dict:
    return {
        "id": job.id,
        "title": job.title,
        "company": job.company.name if job.company else None,
        "location": job.location,
        "url": job.url,
        "source": job.source,
        "match_score": job.match_score,
        "is_high_priority": job.is_high_priority,
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
        "discovered_at": job.discovered_at.isoformat() if job.discovered_at else None,
        "score_breakdown": json.loads(job.score_breakdown) if job.score_breakdown else [],
    }


# ---------------------------------------------------------------- pipeline


@router.post("/pipeline/run")
def api_run_pipeline(background: BackgroundTasks):
    """Trigger a discovery run in the background."""
    background.add_task(run_pipeline)
    return {"status": "started"}


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
def api_quick_start():
    """Load starter companies if needed, backfill locations, rescore, and discover."""
    companies_changed = 0
    locations_backfilled = 0

    with session_scope() as session:
        if enabled_company_count(session) == 0:
            companies_changed += bootstrap_starter_pack(session)
        if ensure_accenture(session):
            companies_changed += 1
        locations_backfilled = backfill_job_locations(session)
        if companies_changed:
            log_activity(session, "config", f"Quick start: {companies_changed} companies configured")
        if locations_backfilled:
            log_activity(
                session,
                "discovery",
                f"Quick start: backfilled location on {locations_backfilled} jobs",
            )

    if locations_backfilled:
        rescore_all_jobs()

    result = run_pipeline()

    from sqlalchemy import func

    with session_scope() as session:
        jobs_total = session.execute(select(func.count(Job.id))).scalar() or 0
        high_priority = (
            session.execute(select(func.count(Job.id)).where(Job.is_high_priority.is_(True))).scalar() or 0
        )
        enabled = enabled_company_count(session)

    return {
        "status": "ok",
        "companies_enabled": enabled,
        "companies_changed": companies_changed,
        "locations_backfilled": locations_backfilled,
        "jobs_total": jobs_total,
        "high_priority": high_priority,
        "new_jobs": result.new_jobs,
        "fetched": result.fetched,
        "errors": result.errors,
    }


# ---------------------------------------------------------------- jobs


@router.get("/jobs")
def api_jobs(
    db: Session = Depends(get_db),
    q: str | None = None,
    location: str | None = None,
    min_score: float = 0,
    high_priority: bool = False,
    limit: int = 100,
    offset: int = 0,
):
    stmt = select(Job).where(Job.is_active.is_(True), Job.match_score >= min_score)
    if q:
        stmt = stmt.where(Job.title.ilike(f"%{q}%"))
    if location:
        stmt = stmt.where(Job.location.ilike(f"%{location}%"))
    if high_priority:
        stmt = stmt.where(Job.is_high_priority.is_(True))
    stmt = stmt.order_by(Job.match_score.desc(), Job.discovered_at.desc()).limit(limit).offset(offset)
    return [_job_dict(j) for j in db.execute(stmt).scalars()]


@router.get("/jobs/{job_id}")
def api_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
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
def api_applications(db: Session = Depends(get_db)):
    apps = db.execute(select(Application).order_by(Application.updated_at.desc())).scalars().all()
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
def api_create_application(payload: ApplicationIn):
    if payload.status not in APPLICATION_STATUSES:
        raise HTTPException(400, f"Status must be one of {APPLICATION_STATUSES}")
    with session_scope() as session:
        data = payload.model_dump()
        if payload.job_id:
            job = session.get(Job, payload.job_id)
            if job is None:
                raise HTTPException(404, "Job not found")
            data.setdefault("company_name", None)
            data["company_name"] = data["company_name"] or (job.company.name if job.company else None)
            data["role"] = data["role"] or job.title
        application = Application(**data)
        session.add(application)
        session.flush()
        log_activity(session, "app", f"Application created: {application.company_name} / {application.role}")
        return {"id": application.id}


@router.put("/applications/{app_id}")
def api_update_application(app_id: int, payload: ApplicationIn):
    if payload.status not in APPLICATION_STATUSES:
        raise HTTPException(400, f"Status must be one of {APPLICATION_STATUSES}")
    with session_scope() as session:
        application = session.get(Application, app_id)
        if application is None:
            raise HTTPException(404, "Application not found")
        for key, value in payload.model_dump().items():
            setattr(application, key, value)
        log_activity(session, "app", f"Application updated: {application.company_name} / {application.role}")
        return {"id": application.id}


@router.delete("/applications/{app_id}")
def api_delete_application(app_id: int):
    with session_scope() as session:
        application = session.get(Application, app_id)
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
def api_create_company(payload: CompanyIn):
    with session_scope() as session:
        company = session.execute(
            select(Company).where(Company.name == payload.name.strip())
        ).scalar_one_or_none()
        if company is None:
            company = Company(name=payload.name.strip())
            session.add(company)
        _apply_company_payload(company, payload)
        session.flush()
        log_activity(session, "config", f"Company added/updated via API: {company.name}")
        return {"id": company.id}


@router.put("/companies/{company_id}")
def api_update_company(company_id: int, payload: CompanyIn):
    with session_scope() as session:
        company = session.get(Company, company_id)
        if company is None:
            raise HTTPException(404, "Company not found")
        _apply_company_payload(company, payload)
        log_activity(session, "config", f"Company updated via API: {company.name}")
        return {"id": company.id}


@router.delete("/companies/{company_id}")
def api_delete_company(company_id: int):
    with session_scope() as session:
        company = session.get(Company, company_id)
        if company is None:
            raise HTTPException(404, "Company not found")
        if company.jobs or company.recruiters:
            company.ats_type = None
            company.enabled = False
            return {"unmonitored": company_id}
        session.delete(company)
        return {"deleted": company_id}


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
async def api_upload_cv(file: UploadFile):
    suffix = Path(file.filename or "cv").suffix.lower()
    if suffix not in (".pdf", ".docx", ".doc", ".txt"):
        raise HTTPException(400, "Upload a PDF, DOCX or TXT resume")
    cv_dir = data_dir() / "cv"
    cv_dir.mkdir(parents=True, exist_ok=True)
    target = cv_dir / f"master{suffix}"
    target.write_bytes(await file.read())

    parsed = cv_parser.parse_cv(target)

    profile = get_profile()
    for field in ("full_name", "email", "phone"):
        if parsed.get(field):
            profile[field] = parsed[field]
    if parsed.get("experience_years"):
        profile["experience_years"] = parsed["experience_years"]
    if parsed.get("skills"):
        merged = list(dict.fromkeys((profile.get("skills") or []) + parsed["skills"]))
        profile["skills"] = merged
    save_profile(profile)

    with session_scope() as session:
        row = session.execute(select(UserProfile)).scalars().first()
        if row is None:
            row = UserProfile()
            session.add(row)
        row.full_name = parsed.get("full_name") or row.full_name
        row.email = parsed.get("email") or row.email
        row.phone = parsed.get("phone") or row.phone
        row.cv_path = str(target)
        parsed_no_text = {k: v for k, v in parsed.items() if k != "raw_text"}
        row.profile_json = json.dumps(parsed_no_text)
        log_activity(session, "profile", f"CV uploaded and parsed: {file.filename}")

    # Existing discoveries must re-rank for the new profile immediately.
    rescored = rescore_all_jobs()
    return {
        "parsed": {k: v for k, v in parsed.items() if k != "raw_text"},
        "cv_path": str(target),
        "rescored_jobs": rescored,
    }


@router.post("/jobs/rescore")
def api_rescore_jobs():
    """Re-score all stored jobs against the current profile and weights."""
    return {"rescored": rescore_all_jobs()}


# ---------------------------------------------------------------- resumes


@router.post("/resumes/tailor/{job_id}")
def api_tailor_resume(job_id: int):
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        profile_row = session.execute(select(UserProfile)).scalars().first()
        master = profile_row.cv_path if profile_row and profile_row.cv_path else None
        if not master or not Path(master).exists():
            raise HTTPException(400, "Upload your master resume (DOCX) first, on the Profile page")
        if not master.endswith(".docx"):
            raise HTTPException(400, "Tailoring needs a DOCX master resume; you uploaded a different format")

        result = resume_engine.tailor_resume(
            master,
            job_title=job.title,
            company=job.company.name if job.company else "company",
            job_description=job.description or "",
        )
        resume = Resume(
            job_id=job.id,
            kind="tailored",
            file_path=result["docx"],
            pdf_path=result["pdf"],
            matched_keywords=result["matched_keywords_json"],
        )
        session.add(resume)
        session.flush()
        log_activity(session, "resume", f"Tailored resume generated for job #{job.id}: {job.title}")
        return {
            "id": resume.id,
            "docx": result["docx"],
            "pdf": result["pdf"],
            "matched_keywords": result["matched_keywords"],
        }


@router.post("/jobs/{job_id}/cover-letter")
def api_cover_letter(job_id: int):
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        profile_row = session.execute(select(UserProfile)).scalars().first()
        cv_json = json.loads(profile_row.profile_json) if profile_row and profile_row.profile_json else None
        if not get_profile().get("full_name") and not (cv_json and cv_json.get("full_name")):
            raise HTTPException(400, "Upload your CV on the Profile page first")
        result = generate_cover_letter(
            job_title=job.title,
            company=job.company.name if job.company else "the company",
            job_description=job.description or "",
            cv_json=cv_json,
        )
        resume = Resume(
            job_id=job.id,
            kind="cover_letter",
            file_path=result["docx"],
            matched_keywords=result["matched_keywords_json"],
        )
        session.add(resume)
        session.flush()
        log_activity(session, "resume", f"Cover letter generated for job #{job.id}: {job.title}")
        return {
            "id": resume.id,
            "docx": result["docx"],
            "matched_keywords": result["matched_keywords"],
            "preview": result["text"][:2000],
        }


@router.post("/jobs/{job_id}/interview-prep")
def api_interview_prep(job_id: int):
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        breakdown = json.loads(job.score_breakdown) if job.score_breakdown else []
        prep = build_interview_prep(
            job_title=job.title,
            company=job.company.name if job.company else "",
            job_description=job.description or "",
            location=job.location or "",
            score_breakdown=breakdown,
        )
        row = InterviewPrep(job_id=job.id, content_json=prep["content_json"])
        session.add(row)
        session.flush()
        log_activity(session, "app", f"Interview prep generated for job #{job.id}")
        prep["id"] = row.id
        return prep


@router.get("/jobs/{job_id}/interview-prep")
def api_get_interview_prep(job_id: int, db: Session = Depends(get_db)):
    row = (
        db.execute(
            select(InterviewPrep).where(InterviewPrep.job_id == job_id).order_by(InterviewPrep.created_at.desc())
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
def api_resumes(db: Session = Depends(get_db)):
    rows = db.execute(select(Resume).order_by(Resume.created_at.desc())).scalars().all()
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
def api_download_resume(resume_id: int, fmt: str = "docx", db: Session = Depends(get_db)):
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise HTTPException(404, "Resume not found")
    path = resume.pdf_path if fmt == "pdf" else resume.file_path
    if not path or not Path(path).exists():
        raise HTTPException(404, f"No {fmt} file available for this resume")
    return FileResponse(path, filename=Path(path).name)


# ---------------------------------------------------------------- exports & summaries


@router.post("/exports/excel")
def api_export_excel():
    path = export_excel()
    return {"path": str(path)}


@router.get("/exports/excel/download")
def api_download_excel():
    path = export_excel()
    return FileResponse(str(path), filename=Path(str(path)).name)


@router.post("/exports/google-sheets")
def api_export_google_sheets():
    spreadsheet_id = export_google_sheets()
    if not spreadsheet_id:
        return {"status": "disabled"}
    return {"status": "synced", "spreadsheet_id": spreadsheet_id}


@router.get("/summary/daily")
def api_daily_summary():
    return {"summary": build_daily_summary()}


@router.post("/summary/daily/send")
def api_send_daily_summary():
    send_daily_summary()
    return {"status": "sent"}


@router.post("/notifications/test")
def api_test_notifications():
    """Send a test message on all configured channels and report the outcome."""
    return send_test_notification()


# ---------------------------------------------------------------- analytics


@router.get("/analytics")
def api_analytics(db: Session = Depends(get_db)):
    return compute_analytics(db)

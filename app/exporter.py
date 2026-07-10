"""Exports (spec section 14): per-user Excel workbook with Jobs / Companies /
Recruiters / Applications sheets, plus optional Google Sheets sync (admin)."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from sqlalchemy import and_, select

from app.config import PROJECT_ROOT, get_settings
from app.db import session_scope
from app.models import Application, Company, Job, Recruiter, UserCompanyMonitor, UserJobScore
from app.user_access import active_user_ids
from app.user_prefs import user_exports_dir

logger = logging.getLogger(__name__)


def _jobs_frame(session, user_id: int) -> pd.DataFrame:
    score_join = and_(UserJobScore.job_id == Job.id, UserJobScore.user_id == user_id)
    rows = []
    for job, score in session.execute(
        select(Job, UserJobScore)
        .join(UserJobScore, score_join)
        .order_by(UserJobScore.match_score.desc())
    ).all():
        rows.append(
            {
                "ID": job.id,
                "Company": job.company.name if job.company else "",
                "Title": job.title,
                "Location": job.location,
                "Score": score.match_score,
                "JD Fit": score.jd_fit_score,
                "High Priority": "Yes" if score.is_high_priority else "",
                "Source": job.source,
                "Posted": job.posted_at,
                "Discovered": job.discovered_at,
                "URL": job.url,
            }
        )
    return pd.DataFrame(rows)


def _companies_frame(session, user_id: int) -> pd.DataFrame:
    rows = []
    monitors = (
        session.execute(
            select(UserCompanyMonitor).where(UserCompanyMonitor.user_id == user_id)
        )
        .scalars()
        .all()
    )
    for monitor in monitors:
        company = session.get(Company, monitor.company_id)
        if company is None:
            continue
        active = sum(
            1
            for s, j in session.execute(
                select(UserJobScore, Job)
                .join(Job, Job.id == UserJobScore.job_id)
                .where(
                    UserJobScore.user_id == user_id,
                    Job.company_id == company.id,
                    Job.is_active.is_(True),
                )
            ).all()
        )
        rows.append(
            {
                "ID": company.id,
                "Name": company.name,
                "Monitoring": "Yes" if monitor.enabled else "No",
                "Your Active Jobs": active,
                "Keywords": monitor.keywords or "",
                "Priority": monitor.priority,
                "ATS": company.ats_type or "",
                "Notes": company.notes,
            }
        )
    return pd.DataFrame(rows)


def _recruiters_frame(session, user_id: int) -> pd.DataFrame:
    company_ids = list(
        session.execute(
            select(UserCompanyMonitor.company_id).where(UserCompanyMonitor.user_id == user_id)
        ).scalars()
    )
    if not company_ids:
        return pd.DataFrame()
    rows = []
    for rec in session.execute(
        select(Recruiter)
        .where(Recruiter.company_id.in_(company_ids))
        .order_by(Recruiter.name)
    ).scalars():
        rows.append(
            {
                "ID": rec.id,
                "Name": rec.name,
                "Company": rec.company.name if rec.company else "",
                "Department": rec.department,
                "LinkedIn": rec.linkedin_url,
                "Public Email": rec.public_email,
                "Requisitions": rec.related_requisitions,
                "Notes": rec.notes,
            }
        )
    return pd.DataFrame(rows)


def _applications_frame(session, user_id: int) -> pd.DataFrame:
    rows = []
    for app_row in session.execute(
        select(Application)
        .where(Application.user_id == user_id)
        .order_by(Application.updated_at.desc())
    ).scalars():
        rows.append(
            {
                "ID": app_row.id,
                "Company": app_row.company_name
                or (app_row.job.company.name if app_row.job and app_row.job.company else ""),
                "Role": app_row.role or (app_row.job.title if app_row.job else ""),
                "Date Applied": app_row.date_applied,
                "Status": app_row.status,
                "Follow-up": app_row.follow_up_date,
                "Interview Stages": app_row.interview_stages,
                "Outcome": app_row.outcome,
                "Notes": app_row.notes,
            }
        )
    return pd.DataFrame(rows)


def export_excel(user_id: int, path: str | None = None) -> Path:
    """Write a private Excel export for one user."""
    target = Path(path) if path else user_exports_dir(user_id) / "careercopilot.xlsx"
    if not target.is_absolute():
        target = PROJECT_ROOT / target
    target.parent.mkdir(parents=True, exist_ok=True)

    with session_scope() as session:
        frames = {
            "Jobs": _jobs_frame(session, user_id),
            "Companies": _companies_frame(session, user_id),
            "Recruiters": _recruiters_frame(session, user_id),
            "Applications": _applications_frame(session, user_id),
        }

    with pd.ExcelWriter(target, engine="openpyxl") as writer:
        for sheet, frame in frames.items():
            if frame.empty:
                frame = pd.DataFrame({"info": [f"No {sheet.lower()} yet"]})
            for col in frame.columns:
                if frame[col].dtype == object:
                    frame[col] = frame[col].map(
                        lambda v: v[:32000] if isinstance(v, str) else v
                    )
            frame.to_excel(writer, sheet_name=sheet, index=False)
    logger.info("Excel export for user %d written to %s", user_id, target)
    return target


def export_excel_all_users() -> list[Path]:
    """Refresh exports for every active user (called after discovery)."""
    paths: list[Path] = []
    with session_scope() as session:
        user_ids = active_user_ids(session)
    for user_id in user_ids:
        try:
            paths.append(export_excel(user_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Excel export failed for user %d: %s", user_id, exc)
    return paths


def _prepare_frame(frame: pd.DataFrame) -> list[list]:
    if frame.empty:
        return []
    out = frame.copy()
    for col in out.columns:
        if out[col].dtype == object:
            out[col] = out[col].map(lambda v: v[:32000] if isinstance(v, str) else v)
    out = out.astype(object).where(pd.notnull(out), "")
    for col in out.columns:
        out[col] = out[col].map(lambda v: v.isoformat() if hasattr(v, "isoformat") else v)
    return [list(out.columns), *out.values.tolist()]


def export_google_sheets(user_id: int) -> str | None:
    """Optional per-user Google Sheets sync (disabled by default)."""
    settings = get_settings()
    cfg = (settings.get("exports", {}) or {}).get("google_sheets", {}) or {}
    if not cfg.get("enabled"):
        return None

    creds_file = (cfg.get("credentials_file") or "").strip()
    spreadsheet_id = (cfg.get("spreadsheet_id") or "").strip()
    if not creds_file or not spreadsheet_id:
        raise ValueError("google_sheets.credentials_file and spreadsheet_id are required when enabled")

    creds_path = Path(creds_file)
    if not creds_path.is_absolute():
        creds_path = PROJECT_ROOT / creds_path
    if not creds_path.exists():
        raise FileNotFoundError(f"Google Sheets credentials not found: {creds_path}")

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError as exc:
        raise ImportError("Install gspread and google-auth to use Google Sheets export") from exc

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(str(creds_path), scopes=scopes)
    gc = gspread.authorize(creds)
    spreadsheet = gc.open_by_key(spreadsheet_id)

    with session_scope() as session:
        frames = {
            "Jobs": _jobs_frame(session, user_id),
            "Companies": _companies_frame(session, user_id),
            "Recruiters": _recruiters_frame(session, user_id),
            "Applications": _applications_frame(session, user_id),
        }

    suffix = f"_{user_id}"
    for sheet_name, frame in frames.items():
        tab = f"{sheet_name}{suffix}"
        try:
            worksheet = spreadsheet.worksheet(tab)
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=tab, rows=1000, cols=20)
        rows = _prepare_frame(frame)
        if not rows:
            rows = [["info"], [f"No {sheet_name.lower()} yet"]]
        worksheet.clear()
        worksheet.update(rows, value_input_option="USER_ENTERED")

    logger.info("Google Sheets export for user %d synced to %s", user_id, spreadsheet_id)
    return spreadsheet_id

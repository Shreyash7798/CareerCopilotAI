"""Exports (spec section 14): Excel workbook with Jobs / Companies /
Recruiters / Applications sheets, plus optional Google Sheets sync."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from sqlalchemy import select

from app.config import PROJECT_ROOT, get_settings
from app.db import session_scope
from app.models import Application, Company, Job, Recruiter

logger = logging.getLogger(__name__)


def _jobs_frame(session) -> pd.DataFrame:
    rows = []
    for job in session.execute(select(Job).order_by(Job.match_score.desc())).scalars():
        rows.append(
            {
                "ID": job.id,
                "Company": job.company.name if job.company else "",
                "Title": job.title,
                "Location": job.location,
                "Score": job.match_score,
                "High Priority": "Yes" if job.is_high_priority else "",
                "Source": job.source,
                "Posted": job.posted_at,
                "Discovered": job.discovered_at,
                "URL": job.url,
            }
        )
    return pd.DataFrame(rows)


def _companies_frame(session) -> pd.DataFrame:
    rows = []
    for company in session.execute(select(Company).order_by(Company.name)).scalars():
        active = sum(1 for j in company.jobs if j.is_active)
        rows.append(
            {
                "ID": company.id,
                "Name": company.name,
                "Preferred": "Yes" if company.is_preferred else "",
                "Active Jobs": active,
                "Industry": company.industry,
                "Website": company.website,
                "Notes": company.notes,
            }
        )
    return pd.DataFrame(rows)


def _recruiters_frame(session) -> pd.DataFrame:
    rows = []
    for rec in session.execute(select(Recruiter).order_by(Recruiter.name)).scalars():
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


def _applications_frame(session) -> pd.DataFrame:
    rows = []
    for app_row in session.execute(select(Application).order_by(Application.updated_at.desc())).scalars():
        rows.append(
            {
                "ID": app_row.id,
                "Company": app_row.company_name or (app_row.job.company.name if app_row.job and app_row.job.company else ""),
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


def export_excel(path: str | None = None) -> Path:
    settings = get_settings()
    excel_cfg = (settings.get("exports", {}) or {}).get("excel", {}) or {}
    target = Path(path or excel_cfg.get("path", "data/careercopilot.xlsx"))
    if not target.is_absolute():
        target = PROJECT_ROOT / target
    target.parent.mkdir(parents=True, exist_ok=True)

    with session_scope() as session:
        frames = {
            "Jobs": _jobs_frame(session),
            "Companies": _companies_frame(session),
            "Recruiters": _recruiters_frame(session),
            "Applications": _applications_frame(session),
        }

    with pd.ExcelWriter(target, engine="openpyxl") as writer:
        for sheet, frame in frames.items():
            if frame.empty:
                frame = pd.DataFrame({"info": [f"No {sheet.lower()} yet"]})
            # Excel cells cap at 32k chars; also strip timezone awareness.
            for col in frame.columns:
                if frame[col].dtype == object:
                    frame[col] = frame[col].map(
                        lambda v: v[:32000] if isinstance(v, str) else v
                    )
            frame.to_excel(writer, sheet_name=sheet, index=False)
    logger.info("Excel export written to %s", target)
    return target


def _prepare_frame(frame: pd.DataFrame) -> list[list]:
    """Turn a DataFrame into sheet rows with Excel-safe cell values."""
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


def export_google_sheets() -> str | None:
    """Optional sync to Google Sheets (disabled by default). Returns spreadsheet ID."""
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
            "Jobs": _jobs_frame(session),
            "Companies": _companies_frame(session),
            "Recruiters": _recruiters_frame(session),
            "Applications": _applications_frame(session),
        }

    for sheet_name, frame in frames.items():
        try:
            worksheet = spreadsheet.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=20)
        rows = _prepare_frame(frame)
        if not rows:
            rows = [["info"], [f"No {sheet_name.lower()} yet"]]
        worksheet.clear()
        worksheet.update(rows, value_input_option="USER_ENTERED")

    logger.info("Google Sheets export synced to %s", spreadsheet_id)
    return spreadsheet_id

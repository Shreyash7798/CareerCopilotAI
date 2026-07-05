"""Notifications (spec section 13): Telegram + email.

Both channels are optional and disabled by default. Failures are logged, never
fatal — the pipeline keeps working without any notification configured.
"""

from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

import httpx
from sqlalchemy import func, select

from app.config import get_settings
from app.db import session_scope
from app.models import Application, Company, Job, log_activity, utcnow

logger = logging.getLogger(__name__)


def send_telegram(text: str) -> bool:
    # Refresh so edits to settings.yaml take effect without a restart.
    cfg = (get_settings(refresh=True).get("notifications", {}) or {}).get("telegram", {}) or {}
    if not cfg.get("enabled") or not cfg.get("bot_token") or not cfg.get("chat_id"):
        return False
    url = f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage"
    try:
        resp = httpx.post(
            url,
            json={"chat_id": cfg["chat_id"], "text": text, "disable_web_page_preview": True},
            timeout=20,
        )
        return resp.status_code == 200
    except httpx.HTTPError as exc:
        logger.warning("Telegram send failed: %s", exc)
        return False


def send_email(subject: str, body: str) -> bool:
    cfg = (get_settings(refresh=True).get("notifications", {}) or {}).get("email", {}) or {}
    if not cfg.get("enabled") or not cfg.get("smtp_host") or not cfg.get("to_address"):
        return False
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = cfg.get("from_address") or cfg.get("username", "")
    msg["To"] = cfg["to_address"]
    try:
        with smtplib.SMTP(cfg["smtp_host"], int(cfg.get("smtp_port", 587)), timeout=30) as smtp:
            smtp.starttls()
            if cfg.get("username"):
                smtp.login(cfg["username"], cfg.get("password", ""))
            smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        logger.warning("Email send failed: %s", exc)
        return False


def _job_line(job: Job, base_url: str) -> str:
    company = job.company.name if job.company else "?"
    return (
        f"• [{job.match_score:.0f}] {job.title} — {company}"
        f" ({job.location or 'location n/a'})\n  {base_url}/jobs/{job.id}"
    )


def notify_high_priority(new_job_ids: list[int]) -> None:
    """Instant alert for newly discovered high-priority jobs."""
    settings = get_settings()
    base_url = (settings.get("app", {}) or {}).get("base_url", "http://localhost:8000")
    with session_scope() as session:
        jobs = (
            session.execute(
                select(Job)
                .where(Job.id.in_(new_job_ids), Job.is_high_priority.is_(True), Job.notified_at.is_(None))
                .order_by(Job.match_score.desc())
            )
            .scalars()
            .all()
        )
        if not jobs:
            return
        lines = [_job_line(j, base_url) for j in jobs[:15]]
        text = f"CareerCopilot: {len(jobs)} high-priority job(s) found\n\n" + "\n".join(lines)
        sent_tg = send_telegram(text)
        sent_mail = send_email("CareerCopilot: high-priority jobs", text)
        now = utcnow()
        for j in jobs:
            j.notified_at = now
        log_activity(
            session,
            "notify",
            f"High-priority alert for {len(jobs)} jobs (telegram={sent_tg}, email={sent_mail})",
        )


def build_daily_summary() -> str:
    """New jobs, high-priority jobs, companies hiring, application reminders."""
    settings = get_settings()
    base_url = (settings.get("app", {}) or {}).get("base_url", "http://localhost:8000")
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    with session_scope() as session:
        new_jobs = (
            session.execute(
                select(Job).where(Job.discovered_at >= since).order_by(Job.match_score.desc())
            )
            .scalars()
            .all()
        )
        high = [j for j in new_jobs if j.is_high_priority]
        companies_hiring = session.execute(
            select(Company.name, func.count(Job.id))
            .join(Job, Job.company_id == Company.id)
            .where(Job.discovered_at >= since)
            .group_by(Company.name)
            .order_by(func.count(Job.id).desc())
        ).all()
        due = (
            session.execute(
                select(Application).where(
                    Application.follow_up_date.isnot(None),
                    Application.follow_up_date <= datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
                    Application.status.notin_(["Rejected", "Withdrawn", "Offer"]),
                )
            )
            .scalars()
            .all()
        )

        parts = [f"CareerCopilot daily summary — {datetime.now():%d %b %Y}"]
        parts.append(f"\nNew jobs in the last 24h: {len(new_jobs)} ({len(high)} high priority)")
        if high:
            parts.append("\nTop matches:")
            parts.extend(_job_line(j, base_url) for j in high[:10])
        if companies_hiring:
            parts.append("\nCompanies hiring:")
            parts.extend(f"• {name}: {count} new role(s)" for name, count in companies_hiring[:10])
        if due:
            parts.append("\nFollow-ups due:")
            parts.extend(
                f"• {a.company_name or ''} — {a.role or ''} (status: {a.status})" for a in due[:10]
            )
        if len(parts) == 2 and not new_jobs:
            parts.append("\nNo new activity. The pipeline is still watching your sources.")
        return "\n".join(parts)


def send_daily_summary() -> None:
    text = build_daily_summary()
    sent_tg = send_telegram(text)
    sent_mail = send_email("CareerCopilot daily summary", text)
    with session_scope() as session:
        log_activity(session, "notify", f"Daily summary sent (telegram={sent_tg}, email={sent_mail})")


def send_run_summary(result) -> None:
    """Short Telegram/email message after every scheduled discovery run, so the
    user gets a heartbeat even when nothing new was found."""
    cfg = get_settings().get("notifications", {}) or {}
    if not cfg.get("run_summary", True):
        return
    base_url = (get_settings().get("app", {}) or {}).get("base_url", "http://localhost:8000")
    if result.new_jobs:
        text = (
            f"CareerCopilot check-in: {result.new_jobs} new job(s), "
            f"{result.high_priority} high priority, "
            f"{result.duplicates} duplicates skipped "
            f"({result.sources_run} sources, {result.sources_failed} failed).\n{base_url}/jobs"
        )
    else:
        text = (
            f"CareerCopilot check-in: no new jobs this cycle "
            f"({result.sources_run} sources checked, {result.duplicates} duplicates skipped)."
        )
    if result.errors:
        text += f"\nIssues: {'; '.join(result.errors[:3])}"
    sent_tg = send_telegram(text)
    sent_mail = send_email("CareerCopilot check-in", text) if cfg.get("run_summary_email") else False
    with session_scope() as session:
        log_activity(session, "notify", f"Run summary sent (telegram={sent_tg}, email={sent_mail})")


def send_test_notification() -> dict:
    """Send a test message on all configured channels and report the outcome,
    so channel misconfiguration is diagnosable from the dashboard."""
    text = "CareerCopilot test notification — your channel is configured correctly."
    cfg = get_settings(refresh=True).get("notifications", {}) or {}
    tg_cfg = cfg.get("telegram", {}) or {}
    email_cfg = cfg.get("email", {}) or {}
    outcome = {
        "telegram_enabled": bool(tg_cfg.get("enabled")),
        "telegram_sent": False,
        "email_enabled": bool(email_cfg.get("enabled")),
        "email_sent": False,
    }
    if outcome["telegram_enabled"]:
        outcome["telegram_sent"] = send_telegram(text)
    if outcome["email_enabled"]:
        outcome["email_sent"] = send_email("CareerCopilot test", text)
    with session_scope() as session:
        log_activity(session, "notify", f"Test notification: {outcome}")
    return outcome

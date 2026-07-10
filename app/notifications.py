"""Per-user notifications via a shared Telegram bot and/or email.

Global settings.yaml holds the bot token and SMTP credentials (server-side).
Each user configures their Telegram chat ID and channel toggles in the dashboard.
Messages go only to that user — never broadcast to other accounts.
"""

from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

import httpx
from sqlalchemy import and_, func, select

from app.config import get_settings
from app.db import session_scope
from app.models import Application, Company, Job, User, UserCompanyMonitor, UserJobScore, log_activity, utcnow
from app.user_access import active_user_ids, hydrate_job_from_score
from app.user_prefs import notification_config_for_user, user_notification_email

logger = logging.getLogger(__name__)


def _global_telegram() -> dict:
    return (get_settings(refresh=True).get("notifications", {}) or {}).get("telegram", {}) or {}


def _global_email() -> dict:
    return (get_settings(refresh=True).get("notifications", {}) or {}).get("email", {}) or {}


def _base_url() -> str:
    return (get_settings().get("app", {}) or {}).get("base_url", "http://localhost:8000")


def _user_wants(user: User, channel: str) -> bool:
    cfg = notification_config_for_user(user)
    return bool(cfg.get(channel, True))


def _telegram_configured() -> bool:
    tg = _global_telegram()
    return bool(tg.get("enabled") and tg.get("bot_token"))


def _email_configured() -> bool:
    em = _global_email()
    return bool(em.get("enabled") and em.get("smtp_host"))


def send_telegram_to_chat(chat_id: str, text: str) -> bool:
    tg = _global_telegram()
    if not tg.get("enabled") or not tg.get("bot_token") or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{tg['bot_token']}/sendMessage"
    try:
        resp = httpx.post(
            url,
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=20,
        )
        return resp.status_code == 200
    except httpx.HTTPError as exc:
        logger.warning("Telegram send failed: %s", exc)
        return False


def send_email_to_address(to_address: str, subject: str, body: str) -> bool:
    cfg = _global_email()
    if not cfg.get("enabled") or not cfg.get("smtp_host") or not to_address:
        return False
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = cfg.get("from_address") or cfg.get("username", "")
    msg["To"] = to_address
    try:
        with smtplib.SMTP(cfg["smtp_host"], int(cfg.get("smtp_port", 587)), timeout=30) as smtp:
            smtp.starttls()
            if cfg.get("username"):
                smtp.login(cfg["username"], cfg.get("password", ""))
            smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        logger.warning("Email send failed to %s: %s", to_address, exc)
        return False


def deliver_to_user(user: User, subject: str, text: str, *, email_only: bool = False) -> dict[str, bool]:
    """Send to the user's Telegram chat ID and/or registered email."""
    cfg = notification_config_for_user(user)
    outcome = {"telegram": False, "email": False}

    if not email_only and cfg.get("telegram_enabled", True) and _telegram_configured():
        chat_id = str(cfg.get("telegram_chat_id") or "").strip()
        if chat_id:
            outcome["telegram"] = send_telegram_to_chat(chat_id, text)

    if cfg.get("email_enabled", True) and _email_configured():
        address = user_notification_email(user)
        if address:
            outcome["email"] = send_email_to_address(address, subject, text)

    return outcome


def _job_line(job: Job, base_url: str) -> str:
    company = job.company.name if job.company else "?"
    return (
        f"• [{job.match_score:.0f}] {job.title} — {company}"
        f" ({job.location or 'location n/a'})\n  {base_url}/jobs/{job.id}"
    )


def notify_high_priority(new_job_ids: list[int]) -> None:
    """Instant alert per user for their own newly discovered high-priority jobs."""
    if not new_job_ids:
        return
    base_url = _base_url()
    with session_scope() as session:
        for user_id in active_user_ids(session):
            user = session.get(User, user_id)
            if user is None or not user.is_active or not _user_wants(user, "high_priority"):
                continue
            rows = session.execute(
                select(UserJobScore, Job)
                .join(Job, Job.id == UserJobScore.job_id)
                .where(
                    UserJobScore.user_id == user_id,
                    UserJobScore.job_id.in_(new_job_ids),
                    UserJobScore.is_high_priority.is_(True),
                    UserJobScore.notified_at.is_(None),
                )
                .order_by(UserJobScore.match_score.desc())
            ).all()
            if not rows:
                continue
            lines = []
            for score, job in rows[:15]:
                lines.append(_job_line(hydrate_job_from_score(job, score), base_url))
            text = f"CareerCopilot: {len(rows)} high-priority job(s) for you\n\n" + "\n".join(lines)
            outcome = deliver_to_user(user, "CareerCopilot: high-priority jobs", text)
            now = utcnow()
            for score, _job in rows:
                score.notified_at = now
            log_activity(
                session,
                "notify",
                f"High-priority alert: {len(rows)} jobs (telegram={outcome['telegram']}, email={outcome['email']})",
                user_id=user_id,
            )


def build_daily_summary(user_id: int) -> str:
    """Daily digest for one user: their scores, applications, follow-ups."""
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    base_url = _base_url()
    with session_scope() as session:
        user = session.get(User, user_id)
        if user is None:
            return ""
        score_join = and_(UserJobScore.job_id == Job.id, UserJobScore.user_id == user_id)
        new_rows = session.execute(
            select(UserJobScore, Job)
            .join(Job, score_join)
            .where(Job.discovered_at >= since)
            .order_by(UserJobScore.match_score.desc())
        ).all()
        high = [(s, j) for s, j in new_rows if s.is_high_priority]
        monitored = (
            session.execute(
                select(func.count(UserCompanyMonitor.id)).where(
                    UserCompanyMonitor.user_id == user_id,
                    UserCompanyMonitor.enabled.is_(True),
                )
            ).scalar()
            or 0
        )
        companies_hiring = session.execute(
            select(Company.name, func.count(Job.id))
            .join(Job, Job.company_id == Company.id)
            .join(UserJobScore, and_(UserJobScore.job_id == Job.id, UserJobScore.user_id == user_id))
            .where(Job.discovered_at >= since)
            .group_by(Company.name)
            .order_by(func.count(Job.id).desc())
        ).all()
        due = (
            session.execute(
                select(Application).where(
                    Application.user_id == user_id,
                    Application.follow_up_date.isnot(None),
                    Application.follow_up_date
                    <= datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
                    Application.status.notin_(["Rejected", "Withdrawn", "Offer"]),
                )
            )
            .scalars()
            .all()
        )

        name = user.display_name or user.email.split("@")[0]
        parts = [f"CareerCopilot daily summary — {datetime.now():%d %b %Y}", f"Hi {name},"]
        parts.append(
            f"\nNew jobs matched for you in the last 24h: {len(new_rows)} ({len(high)} high priority)"
        )
        parts.append(f"Companies you monitor: {monitored}")
        if high:
            parts.append("\nTop matches:")
            parts.extend(
                _job_line(hydrate_job_from_score(j, s), base_url) for s, j in high[:10]
            )
        if companies_hiring:
            parts.append("\nCompanies hiring:")
            parts.extend(f"• {name}: {count} new role(s)" for name, count in companies_hiring[:10])
        if due:
            parts.append("\nFollow-ups due:")
            parts.extend(
                f"• {a.company_name or ''} — {a.role or ''} (status: {a.status})" for a in due[:10]
            )
        if len(parts) <= 3 and not new_rows:
            parts.append("\nNo new matches today. Your monitored companies are still being checked.")
        return "\n".join(parts)


def send_daily_summary() -> None:
    """Send the daily digest to every active user who opted in."""
    with session_scope() as session:
        for user_id in active_user_ids(session):
            user = session.get(User, user_id)
            if user is None or not user.is_active or not _user_wants(user, "daily_summary"):
                continue
            text = build_daily_summary(user_id)
            if not text:
                continue
            outcome = deliver_to_user(user, "CareerCopilot daily summary", text)
            log_activity(
                session,
                "notify",
                f"Daily summary sent (telegram={outcome['telegram']}, email={outcome['email']})",
                user_id=user_id,
            )


def send_run_summary(result) -> None:
    """Per-user heartbeat after discovery — only their new matches, never global totals."""
    with session_scope() as session:
        for user_id in active_user_ids(session):
            user = session.get(User, user_id)
            if user is None or not user.is_active or not _user_wants(user, "run_summary"):
                continue
            cfg = notification_config_for_user(user)
            new_for_user = 0
            high_for_user = 0
            if result.new_job_ids:
                user_scores = session.execute(
                    select(UserJobScore).where(
                        UserJobScore.user_id == user_id,
                        UserJobScore.job_id.in_(result.new_job_ids),
                    )
                ).scalars().all()
                new_for_user = len(user_scores)
                high_for_user = sum(1 for s in user_scores if s.is_high_priority)

            base_url = _base_url()
            if new_for_user:
                text = (
                    f"CareerCopilot: {new_for_user} new job(s) matched for you"
                    f" ({high_for_user} high priority).\n{base_url}/jobs"
                )
            else:
                text = "CareerCopilot: no new matches for you this cycle. Your companies are still being monitored."
            if result.errors:
                text += f"\nNote: some sources had issues this cycle."

            email_only = bool(cfg.get("run_summary_email")) and not cfg.get("telegram_enabled", True)
            outcome = deliver_to_user(
                user,
                "CareerCopilot check-in",
                text,
                email_only=email_only and not str(cfg.get("telegram_chat_id") or "").strip(),
            )
            log_activity(
                session,
                "notify",
                f"Run summary sent (telegram={outcome['telegram']}, email={outcome['email']})",
                user_id=user_id,
            )


def send_followup_reminders() -> None:
    """Per-user application follow-up reminders."""
    today = datetime.now(timezone.utc).replace(tzinfo=None).date()
    base_url = _base_url()
    with session_scope() as session:
        for user_id in active_user_ids(session):
            user = session.get(User, user_id)
            if user is None or not user.is_active or not _user_wants(user, "followup_reminders"):
                continue
            due = (
                session.execute(
                    select(Application).where(
                        Application.user_id == user_id,
                        Application.follow_up_date.isnot(None),
                        Application.status.notin_(["Rejected", "Withdrawn", "Offer"]),
                    )
                )
                .scalars()
                .all()
            )
            items = [a for a in due if a.follow_up_date and a.follow_up_date.date() <= today]
            if not items:
                continue
            lines = [
                f"• {a.company_name or '—'} — {a.role or ''} (status: {a.status}, due {a.follow_up_date:%d %b})"
                for a in items[:15]
            ]
            text = (
                f"CareerCopilot: {len(items)} follow-up(s) due\n\n"
                + "\n".join(lines)
                + f"\n\nTracker: {base_url}/applications"
            )
            outcome = deliver_to_user(user, "CareerCopilot: follow-ups due", text)
            log_activity(
                session,
                "notify",
                f"Follow-up reminders sent (telegram={outcome['telegram']}, email={outcome['email']})",
                user_id=user_id,
            )


def send_test_notification(user: User) -> dict:
    """Send a test message to the authenticated user only."""
    text = "CareerCopilot test notification — your alerts are configured correctly."
    cfg = notification_config_for_user(user)
    tg_ready = bool(
        cfg.get("telegram_enabled", True)
        and _telegram_configured()
        and str(cfg.get("telegram_chat_id") or "").strip()
    )
    em_ready = bool(cfg.get("email_enabled", True) and _email_configured() and user_notification_email(user))
    outcome = {
        "telegram_configured": tg_ready,
        "telegram_sent": False,
        "email_configured": em_ready,
        "email_sent": False,
        "telegram_chat_id_set": bool(str(cfg.get("telegram_chat_id") or "").strip()),
        "email_address": user_notification_email(user) or None,
    }
    if tg_ready:
        outcome["telegram_sent"] = send_telegram_to_chat(str(cfg["telegram_chat_id"]), text)
    if em_ready:
        outcome["email_sent"] = send_email_to_address(
            user_notification_email(user), "CareerCopilot test", text
        )
    with session_scope() as session:
        log_activity(
            session,
            "notify",
            f"Test notification: {outcome}",
            user_id=user.id,
        )
    return outcome

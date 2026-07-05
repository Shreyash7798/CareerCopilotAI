"""Job list visibility, posting age labels, and staleness rules.

Untracked jobs that are removed from company boards or exceed max age are
hidden from the portal. Jobs linked in the application tracker stay visible.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.sql import ColumnElement

from app.config import get_settings
from app.models import Application, Job


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def visibility_settings() -> dict:
    cfg = get_settings().get("job_visibility", {}) or {}
    return {
        "hide_stale_untracked": cfg.get("hide_stale_untracked", True),
        "max_posted_age_days": cfg.get("max_posted_age_days", 60),
        "max_discovered_age_days": cfg.get("max_discovered_age_days", 90),
    }


def tracked_job_ids_subquery():
    return select(Application.job_id).where(Application.job_id.isnot(None)).distinct()


def job_reference_date(job: Job) -> datetime | None:
    return job.posted_at or job.discovered_at


def job_age_days(job: Job, *, now: datetime | None = None) -> int | None:
    ref = _as_naive(job_reference_date(job))
    if not ref:
        return None
    now = _as_naive(now) or utcnow_naive()
    return max(0, (now - ref).days)


def job_age_basis(job: Job) -> str:
    return "posted" if job.posted_at else "discovered"


def job_age_label(job: Job, *, now: datetime | None = None) -> str | None:
    ref = job_reference_date(job)
    if not ref:
        return None
    prefix = "Posted" if job.posted_at else "Found"
    days = job_age_days(job, now=now)
    if days is None:
        return None
    if days == 0:
        return f"{prefix} today"
    if days == 1:
        return f"{prefix} yesterday"
    if days < 14:
        return f"{prefix} {days}d ago"
    if days < 60:
        return f"{prefix} {days // 7}w ago"
    return f"{prefix} {ref.strftime('%d %b %Y')}"


def is_aged_out(job: Job, cfg: dict | None = None, *, now: datetime | None = None) -> bool:
    cfg = cfg if cfg is not None else visibility_settings()
    if not cfg.get("hide_stale_untracked", True):
        return False
    now = _as_naive(now) or utcnow_naive()
    max_posted = cfg.get("max_posted_age_days")
    max_discovered = cfg.get("max_discovered_age_days")
    posted_at = _as_naive(job.posted_at)
    discovered_at = _as_naive(job.discovered_at)
    if posted_at and max_posted is not None:
        return (now - posted_at).days > int(max_posted)
    if not posted_at and max_discovered is not None and discovered_at:
        return (now - discovered_at).days > int(max_discovered)
    return False


def _age_ok_clause(cfg: dict, now: datetime) -> ColumnElement[bool]:
    max_posted = cfg.get("max_posted_age_days")
    max_discovered = cfg.get("max_discovered_age_days")
    if max_posted is None and max_discovered is None:
        return Job.id.isnot(None)  # always true for Job rows

    posted_cutoff = now - timedelta(days=int(max_posted)) if max_posted is not None else None
    discovered_cutoff = (
        now - timedelta(days=int(max_discovered)) if max_discovered is not None else None
    )

    parts = []
    if posted_cutoff is not None:
        parts.append(and_(Job.posted_at.isnot(None), Job.posted_at >= posted_cutoff))
    if discovered_cutoff is not None:
        parts.append(and_(Job.posted_at.is_(None), Job.discovered_at >= discovered_cutoff))
    if not parts:
        return Job.id.isnot(None)
    return or_(*parts)


def visible_jobs_filter(*, now: datetime | None = None) -> ColumnElement[bool]:
    """SQLAlchemy expression: jobs that belong on the portal list."""
    cfg = visibility_settings()
    tracked = tracked_job_ids_subquery()
    is_tracked = Job.id.in_(tracked)

    untracked = and_(Job.is_active.is_(True))
    if cfg.get("hide_stale_untracked", True):
        untracked = and_(untracked, _age_ok_clause(cfg, now or utcnow_naive()))

    return or_(is_tracked, untracked)


def job_status_badge(job: Job, *, is_tracked: bool = False) -> str | None:
    if is_tracked and (not job.is_active or is_aged_out(job)):
        if not job.is_active:
            return "Posting closed"
        if is_aged_out(job):
            return "Posting aged out"
    if not job.is_active:
        return "No longer listed"
    if is_aged_out(job):
        return "Likely stale"
    return None


def job_age_dict(job: Job, *, now: datetime | None = None) -> dict:
    return {
        "age_days": job_age_days(job, now=now),
        "age_label": job_age_label(job, now=now),
        "age_basis": job_age_basis(job),
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
        "discovered_at": job.discovered_at.isoformat() if job.discovered_at else None,
        "is_active": job.is_active,
        "status_badge": job_status_badge(job),
    }

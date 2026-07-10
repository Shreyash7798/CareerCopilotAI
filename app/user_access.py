"""Per-user data isolation: query helpers and score hydration."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models import (
    Application,
    ActivityLog,
    Company,
    InterviewPrep,
    Job,
    Resume,
    User,
    UserCompanyMonitor,
    UserJobScore,
)
from app.user_prefs import get_user_profile_dict, scoring_config_for_user
from app.users import get_user_by_id


def get_user_or_404(user_id: int) -> User:
    user = get_user_by_id(user_id)
    if user is None:
        raise ValueError("User not found")
    return user


def monitor_for_user(session: Session, user_id: int, company_id: int) -> UserCompanyMonitor | None:
    return session.execute(
        select(UserCompanyMonitor).where(
            UserCompanyMonitor.user_id == user_id,
            UserCompanyMonitor.company_id == company_id,
        )
    ).scalar_one_or_none()


def ensure_monitor(
    session: Session,
    user_id: int,
    company: Company,
    *,
    enabled: bool = True,
    keywords: str | None = None,
    refresh_interval_minutes: int | None = None,
    priority: str = "normal",
    recruiter_search_enabled: bool = False,
) -> UserCompanyMonitor:
    monitor = monitor_for_user(session, user_id, company.id)
    if monitor is None:
        monitor = UserCompanyMonitor(
            user_id=user_id,
            company_id=company.id,
            enabled=enabled,
            keywords=keywords or company.keywords,
            refresh_interval_minutes=refresh_interval_minutes or company.refresh_interval_minutes,
            priority=priority or company.priority,
            recruiter_search_enabled=recruiter_search_enabled or company.recruiter_search_enabled,
        )
        session.add(monitor)
    else:
        monitor.enabled = enabled
        if keywords is not None:
            monitor.keywords = keywords or None
        if refresh_interval_minutes is not None:
            monitor.refresh_interval_minutes = refresh_interval_minutes
        monitor.priority = priority or monitor.priority
        monitor.recruiter_search_enabled = recruiter_search_enabled
    return monitor


def sync_monitor_from_company(session: Session, user_id: int, company: Company) -> UserCompanyMonitor:
    """Create or update a monitor when the user edits company settings."""
    return ensure_monitor(
        session,
        user_id,
        company,
        enabled=company.enabled,
        keywords=company.keywords,
        refresh_interval_minutes=company.refresh_interval_minutes,
        priority=company.priority,
        recruiter_search_enabled=company.recruiter_search_enabled,
    )


def enabled_monitor_count(session: Session, user_id: int | None = None) -> int:
    """Count enabled monitors. With user_id, returns that user's count only."""
    if user_id is not None:
        return (
            session.execute(
                select(func.count(UserCompanyMonitor.id)).where(
                    UserCompanyMonitor.user_id == user_id,
                    UserCompanyMonitor.enabled.is_(True),
                    UserCompanyMonitor.company_id.in_(
                        select(Company.id).where(Company.ats_type.isnot(None))
                    ),
                )
            ).scalar()
            or 0
        )
    return (
        session.execute(
            select(func.count(func.distinct(UserCompanyMonitor.company_id))).where(
                UserCompanyMonitor.enabled.is_(True),
                UserCompanyMonitor.company_id.in_(
                    select(Company.id).where(Company.ats_type.isnot(None))
                ),
            )
        ).scalar()
        or 0
    )


def user_ids_monitoring_company(session: Session, company_id: int) -> list[int]:
    return list(
        session.execute(
            select(UserCompanyMonitor.user_id).where(
                UserCompanyMonitor.company_id == company_id,
                UserCompanyMonitor.enabled.is_(True),
            )
        ).scalars()
    )


def active_user_ids(session: Session) -> list[int]:
    return list(session.execute(select(User.id).where(User.is_active.is_(True))).scalars())


def keywords_for_monitor(monitor: UserCompanyMonitor | None) -> list[str]:
    if monitor is None or not monitor.keywords:
        return []
    return [k.strip().lower() for k in monitor.keywords.split(",") if k.strip()]


def title_matches_keywords(title: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    lowered = title.lower()
    return any(k in lowered for k in keywords)


def user_job_score(
    session: Session, user_id: int, job_id: int
) -> UserJobScore | None:
    return session.execute(
        select(UserJobScore).where(
            UserJobScore.user_id == user_id,
            UserJobScore.job_id == job_id,
        )
    ).scalar_one_or_none()


def upsert_user_job_score(
    session: Session,
    *,
    user_id: int,
    job_id: int,
    match_score: float,
    score_breakdown: str,
    jd_fit_score: float,
    jd_fit_breakdown: str,
    is_high_priority: bool,
) -> UserJobScore:
    row = user_job_score(session, user_id, job_id)
    if row is None:
        row = UserJobScore(user_id=user_id, job_id=job_id)
        session.add(row)
    row.match_score = match_score
    row.score_breakdown = score_breakdown
    row.jd_fit_score = jd_fit_score
    row.jd_fit_breakdown = jd_fit_breakdown
    row.is_high_priority = is_high_priority
    return row


def hydrate_job_from_score(job: Job, score: UserJobScore) -> Job:
    job.match_score = score.match_score
    job.score_breakdown = score.score_breakdown
    job.jd_fit_score = score.jd_fit_score
    job.jd_fit_breakdown = score.jd_fit_breakdown
    job.is_high_priority = score.is_high_priority
    return job


def attach_user_scores(session: Session, jobs: list[Job], user_id: int) -> list[Job]:
    if not jobs:
        return jobs
    job_ids = [j.id for j in jobs]
    scores = {
        s.job_id: s
        for s in session.execute(
            select(UserJobScore).where(
                UserJobScore.user_id == user_id,
                UserJobScore.job_id.in_(job_ids),
            )
        ).scalars()
    }
    hydrated: list[Job] = []
    for job in jobs:
        score = scores.get(job.id)
        if score is not None:
            hydrated.append(hydrate_job_from_score(job, score))
    return hydrated


def jobs_with_scores_stmt(user_id: int):
    """Base select joining jobs to this user's scores."""
    return (
        select(Job, UserJobScore)
        .join(
            UserJobScore,
            and_(UserJobScore.job_id == Job.id, UserJobScore.user_id == user_id),
        )
    )


def application_owned(session: Session, app_id: int, user_id: int) -> Application | None:
    app_row = session.get(Application, app_id)
    if app_row is None or app_row.user_id != user_id:
        return None
    return app_row


def resume_owned(session: Session, resume_id: int, user_id: int) -> Resume | None:
    row = session.get(Resume, resume_id)
    if row is None or row.user_id != user_id:
        return None
    return row


def interview_prep_owned(session: Session, prep_id: int, user_id: int) -> InterviewPrep | None:
    row = session.get(InterviewPrep, prep_id)
    if row is None or row.user_id != user_id:
        return None
    return row


def company_monitor_map(session: Session, user_id: int) -> dict[int, UserCompanyMonitor]:
    rows = session.execute(
        select(UserCompanyMonitor).where(UserCompanyMonitor.user_id == user_id)
    ).scalars()
    return {m.company_id: m for m in rows}


def apply_monitor_to_company(company: Company, monitor: UserCompanyMonitor | None) -> Company:
    """Overlay per-user monitor settings onto a company for the UI."""
    if monitor is not None:
        company.enabled = monitor.enabled
        company.keywords = monitor.keywords
        company.refresh_interval_minutes = monitor.refresh_interval_minutes
        company.priority = monitor.priority
        company.recruiter_search_enabled = monitor.recruiter_search_enabled
        if monitor.last_run_at:
            company.last_run_at = monitor.last_run_at
        if monitor.last_run_status:
            company.last_run_status = monitor.last_run_status
    else:
        company.enabled = False
    return company


def scoring_context_for_user(user: User) -> tuple[dict[str, Any], dict[str, Any]]:
    profile = get_user_profile_dict(user)
    scoring_cfg = scoring_config_for_user(user)
    return profile, scoring_cfg

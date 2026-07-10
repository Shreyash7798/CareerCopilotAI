"""Analytics (spec section 19)."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, func, select

from app.job_visibility import visible_jobs_filter
from app.models import Application, Company, Job, UserCompanyMonitor, UserJobScore
from app.resume_engine import extract_keywords

INTERVIEW_STATUSES = ["Interviewing", "Offer"]


def _safe_score(value) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def compute_analytics(session, user_id: int | None = None) -> dict:
    """Compute dashboard analytics. Uses SQL aggregates to stay light on 1 GB VMs."""
    visible = visible_jobs_filter()
    score_col = func.coalesce(UserJobScore.match_score, 0.0)

    if user_id is not None:
        score_join = and_(UserJobScore.job_id == Job.id, UserJobScore.user_id == user_id)
        active_jobs = (
            session.execute(
                select(func.count(Job.id))
                .select_from(Job)
                .join(UserJobScore, score_join)
                .where(visible)
            ).scalar()
            or 0
        )
        high_priority = (
            session.execute(
                select(func.count(Job.id))
                .select_from(Job)
                .join(UserJobScore, score_join)
                .where(visible, UserJobScore.is_high_priority.is_(True))
            ).scalar()
            or 0
        )
        avg_score = (
            session.execute(
                select(func.avg(score_col))
                .select_from(Job)
                .join(UserJobScore, score_join)
                .where(visible)
            ).scalar()
            or 0.0
        )
        total_jobs = active_jobs
        apps = (
            session.execute(select(Application).where(Application.user_id == user_id))
            .scalars()
            .all()
        )
        monitored_companies = (
            session.execute(
                select(func.count(UserCompanyMonitor.id)).where(
                    UserCompanyMonitor.user_id == user_id,
                    UserCompanyMonitor.enabled.is_(True),
                )
            ).scalar()
            or 0
        )
        enabled_companies = monitored_companies
        top_companies = session.execute(
            select(Company.name, func.count(Job.id))
            .join(Job, Job.company_id == Company.id)
            .join(UserJobScore, score_join)
            .where(visible)
            .group_by(Company.name)
            .order_by(func.count(Job.id).desc())
            .limit(10)
        ).all()
        descriptions = (
            session.execute(
                select(Job.description)
                .join(UserJobScore, score_join)
                .where(Job.description.isnot(None), visible)
                .order_by(Job.discovered_at.desc())
                .limit(100)
            )
            .scalars()
            .all()
        )
        jobs_by_source = session.execute(
            select(Job.source, func.count(Job.id))
            .join(UserJobScore, score_join)
            .where(visible)
            .group_by(Job.source)
            .order_by(func.count(Job.id).desc())
        ).all()
        jobs_by_sector = session.execute(
            select(Company.sector, func.count(Job.id))
            .join(Job, Job.company_id == Company.id)
            .join(UserJobScore, score_join)
            .where(visible, Company.sector.isnot(None))
            .group_by(Company.sector)
            .order_by(func.count(Job.id).desc())
        ).all()
        bucket_row = session.execute(
            select(
                func.sum(case((score_col >= 70, 1), else_=0)),
                func.sum(case((and_(score_col >= 45, score_col < 70), 1), else_=0)),
                func.sum(case((score_col < 45, 1), else_=0)),
            )
            .select_from(Job)
            .join(UserJobScore, score_join)
            .where(visible)
        ).one()
        score_buckets = {
            "high": int(bucket_row[0] or 0),
            "medium": int(bucket_row[1] or 0),
            "low": int(bucket_row[2] or 0),
        }
    else:
        total_jobs = session.execute(select(func.count(Job.id))).scalar() or 0
        active_jobs = session.execute(select(func.count(Job.id)).where(visible)).scalar() or 0
        high_priority = (
            session.execute(
                select(func.count(Job.id)).where(visible, Job.is_high_priority.is_(True))
            ).scalar()
            or 0
        )
        avg_score = (
            session.execute(
                select(func.avg(func.coalesce(Job.match_score, 0.0))).where(visible)
            ).scalar()
            or 0.0
        )
        apps = session.execute(select(Application)).scalars().all()
        top_companies = session.execute(
            select(Company.name, func.count(Job.id))
            .join(Job, Job.company_id == Company.id)
            .where(visible)
            .group_by(Company.name)
            .order_by(func.count(Job.id).desc())
            .limit(10)
        ).all()
        descriptions = (
            session.execute(
                select(Job.description)
                .where(Job.description.isnot(None), visible)
                .order_by(Job.discovered_at.desc())
                .limit(100)
            )
            .scalars()
            .all()
        )
        jobs_by_source = session.execute(
            select(Job.source, func.count(Job.id))
            .where(visible)
            .group_by(Job.source)
            .order_by(func.count(Job.id).desc())
        ).all()
        jobs_by_sector = session.execute(
            select(Company.sector, func.count(Job.id))
            .join(Job, Job.company_id == Company.id)
            .where(visible, Company.sector.isnot(None))
            .group_by(Company.sector)
            .order_by(func.count(Job.id).desc())
        ).all()
        job_score = func.coalesce(Job.match_score, 0.0)
        bucket_row = session.execute(
            select(
                func.sum(case((job_score >= 70, 1), else_=0)),
                func.sum(case((and_(job_score >= 45, job_score < 70), 1), else_=0)),
                func.sum(case((job_score < 45, 1), else_=0)),
            ).where(visible)
        ).one()
        score_buckets = {
            "high": int(bucket_row[0] or 0),
            "medium": int(bucket_row[1] or 0),
            "low": int(bucket_row[2] or 0),
        }
        monitored_companies = (
            session.execute(select(func.count(Company.id)).where(Company.ats_type.isnot(None))).scalar()
            or 0
        )
        enabled_companies = (
            session.execute(
                select(func.count(Company.id)).where(
                    Company.ats_type.isnot(None), Company.enabled.is_(True)
                )
            ).scalar()
            or 0
        )

    counter: Counter[str] = Counter()
    for desc in descriptions:
        counter.update(set(extract_keywords(desc or "", top_n=25)))
    common_skills = counter.most_common(15)

    submitted = [a for a in apps if a.status not in ("Planned",)]
    interviews = [a for a in apps if a.status in INTERVIEW_STATUSES or (a.interview_stages or "").strip()]
    offers = [a for a in apps if a.status == "Offer"]
    status_counts: Counter[str] = Counter(a.status for a in apps)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    if user_id is not None:
        score_join = and_(UserJobScore.job_id == Job.id, UserJobScore.user_id == user_id)
        jobs_last_7_days = (
            session.execute(
                select(func.count(Job.id))
                .select_from(Job)
                .join(UserJobScore, score_join)
                .where(Job.discovered_at >= week_ago, visible)
            ).scalar()
            or 0
        )
        jobs_last_30_days = (
            session.execute(
                select(func.count(Job.id))
                .select_from(Job)
                .join(UserJobScore, score_join)
                .where(Job.discovered_at >= month_ago, visible)
            ).scalar()
            or 0
        )
    else:
        jobs_last_7_days = (
            session.execute(
                select(func.count(Job.id)).where(Job.discovered_at >= week_ago)
            ).scalar()
            or 0
        )
        jobs_last_30_days = (
            session.execute(
                select(func.count(Job.id)).where(Job.discovered_at >= month_ago)
            ).scalar()
            or 0
        )

    return {
        "total_jobs": total_jobs,
        "active_jobs": active_jobs,
        "high_priority_jobs": high_priority,
        "avg_match_score": round(_safe_score(avg_score), 1),
        "applications_total": len(apps),
        "applications_submitted": len(submitted),
        "interviews_received": len(interviews),
        "offers": len(offers),
        "interview_conversion_pct": round(100 * len(interviews) / len(submitted), 1) if submitted else 0.0,
        "offer_conversion_pct": round(100 * len(offers) / len(submitted), 1) if submitted else 0.0,
        "top_companies": [{"name": n, "jobs": c} for n, c in top_companies],
        "common_skills": [{"keyword": k, "count": c} for k, c in common_skills],
        "jobs_by_source": [{"source": s or "unknown", "jobs": c} for s, c in jobs_by_source],
        "jobs_by_sector": [{"sector": s or "unknown", "jobs": c} for s, c in jobs_by_sector],
        "score_buckets": score_buckets,
        "applications_by_status": [{"status": s, "count": c} for s, c in status_counts.most_common()],
        "monitored_companies": monitored_companies,
        "enabled_companies": enabled_companies,
        "jobs_last_7_days": jobs_last_7_days,
        "jobs_last_30_days": jobs_last_30_days,
    }

"""Analytics (spec section 19)."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import and_, func, select

from app.job_visibility import visible_jobs_filter
from app.models import Application, Company, Job, UserCompanyMonitor, UserJobScore
from app.resume_engine import extract_keywords

INTERVIEW_STATUSES = ["Interviewing", "Offer"]


def compute_analytics(session, user_id: int | None = None) -> dict:
    visible = visible_jobs_filter()
    if user_id is not None:
        job_base = (
            select(Job.id, UserJobScore.match_score, UserJobScore.is_high_priority)
            .join(
                UserJobScore,
                and_(UserJobScore.job_id == Job.id, UserJobScore.user_id == user_id),
            )
            .where(visible)
        )
        scored_rows = session.execute(job_base).all()
        active_jobs = len(scored_rows)
        high_priority = sum(1 for _id, _s, hp in scored_rows if hp)
        avg_score = (
            sum(s for _id, s, _hp in scored_rows) / active_jobs if active_jobs else 0.0
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
            .join(
                UserJobScore,
                and_(UserJobScore.job_id == Job.id, UserJobScore.user_id == user_id),
            )
            .where(visible)
            .group_by(Company.name)
            .order_by(func.count(Job.id).desc())
            .limit(10)
        ).all()
        descriptions = (
            session.execute(
                select(Job.description)
                .join(
                    UserJobScore,
                    and_(UserJobScore.job_id == Job.id, UserJobScore.user_id == user_id),
                )
                .where(Job.description.isnot(None), visible)
                .order_by(Job.discovered_at.desc())
                .limit(200)
            )
            .scalars()
            .all()
        )
        jobs_by_source = session.execute(
            select(Job.source, func.count(Job.id))
            .join(
                UserJobScore,
                and_(UserJobScore.job_id == Job.id, UserJobScore.user_id == user_id),
            )
            .where(visible)
            .group_by(Job.source)
            .order_by(func.count(Job.id).desc())
        ).all()
        jobs_by_sector = session.execute(
            select(Company.sector, func.count(Job.id))
            .join(Job, Job.company_id == Company.id)
            .join(
                UserJobScore,
                and_(UserJobScore.job_id == Job.id, UserJobScore.user_id == user_id),
            )
            .where(visible, Company.sector.isnot(None))
            .group_by(Company.sector)
            .order_by(func.count(Job.id).desc())
        ).all()
        score_buckets = {
            "high": sum(1 for _id, s, _hp in scored_rows if s >= 70),
            "medium": sum(1 for _id, s, _hp in scored_rows if 45 <= s < 70),
            "low": sum(1 for _id, s, _hp in scored_rows if s < 45),
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
            session.execute(select(func.avg(Job.match_score)).where(visible)).scalar() or 0.0
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
                .limit(200)
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
        score_buckets = {
            "high": session.execute(
                select(func.count(Job.id)).where(visible, Job.match_score >= 70)
            ).scalar()
            or 0,
            "medium": session.execute(
                select(func.count(Job.id)).where(
                    visible, Job.match_score >= 45, Job.match_score < 70
                )
            ).scalar()
            or 0,
            "low": session.execute(
                select(func.count(Job.id)).where(visible, Job.match_score < 45)
            ).scalar()
            or 0,
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

    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    if user_id is not None:
        jobs_last_7_days = (
            session.execute(
                select(func.count(Job.id))
                .join(
                    UserJobScore,
                    and_(UserJobScore.job_id == Job.id, UserJobScore.user_id == user_id),
                )
                .where(Job.discovered_at >= week_ago, visible)
            ).scalar()
            or 0
        )
        jobs_last_30_days = (
            session.execute(
                select(func.count(Job.id))
                .join(
                    UserJobScore,
                    and_(UserJobScore.job_id == Job.id, UserJobScore.user_id == user_id),
                )
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
        "avg_match_score": round(float(avg_score), 1),
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

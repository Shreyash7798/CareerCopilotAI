"""Analytics (spec section 19)."""

from __future__ import annotations

from collections import Counter

from sqlalchemy import func, select

from app.models import Application, Company, Job
from app.resume_engine import extract_keywords

INTERVIEW_STATUSES = ["Interviewing", "Offer"]


def compute_analytics(session) -> dict:
    total_jobs = session.execute(select(func.count(Job.id))).scalar() or 0
    high_priority = (
        session.execute(select(func.count(Job.id)).where(Job.is_high_priority.is_(True))).scalar() or 0
    )
    avg_score = session.execute(select(func.avg(Job.match_score))).scalar() or 0.0

    apps = session.execute(select(Application)).scalars().all()
    submitted = [a for a in apps if a.status not in ("Planned",)]
    interviews = [a for a in apps if a.status in INTERVIEW_STATUSES or (a.interview_stages or "").strip()]
    offers = [a for a in apps if a.status == "Offer"]

    top_companies = session.execute(
        select(Company.name, func.count(Job.id))
        .join(Job, Job.company_id == Company.id)
        .group_by(Company.name)
        .order_by(func.count(Job.id).desc())
        .limit(10)
    ).all()

    # Most common required skills: keyword frequency across recent JDs.
    descriptions = (
        session.execute(
            select(Job.description).where(Job.description.isnot(None)).order_by(Job.discovered_at.desc()).limit(200)
        )
        .scalars()
        .all()
    )
    counter: Counter[str] = Counter()
    for desc in descriptions:
        counter.update(set(extract_keywords(desc or "", top_n=25)))
    common_skills = counter.most_common(15)

    return {
        "total_jobs": total_jobs,
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
    }

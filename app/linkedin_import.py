"""Import a single public LinkedIn job URL into the jobs table."""

from __future__ import annotations

import json

from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope
from app.dedup import dedup_key, is_fuzzy_duplicate
from app.models import Company, Job, log_activity
from app.normalize import normalize
from app.pipeline import _score_job_for_users
from app.scoring import load_scoring_profile, score_jd_fit, score_job
from app.sources.linkedin import parse_job_url


def import_linkedin_job(url: str, user_id: int | None = None) -> dict:
    """Fetch, score, and store one LinkedIn job. Returns summary dict."""
    raw = parse_job_url(url.strip())
    raw = normalize(raw)
    if not raw.title:
        raise ValueError("Could not parse job title from LinkedIn URL")

    settings = get_settings(refresh=True)
    profile = load_scoring_profile(user_id)
    scoring_cfg = settings.get("scoring", {}) or {}
    key = dedup_key(raw.company, raw.title, raw.location)

    with session_scope() as session:
        existing = session.execute(
            select(Job).where(Job.dedup_key == key)
        ).scalar_one_or_none()
        if existing is not None:
            if raw.posted_at and (
                existing.posted_at is None or raw.posted_at > existing.posted_at
            ):
                existing.posted_at = raw.posted_at
            if raw.description and len(raw.description) > len(existing.description or ""):
                existing.description = raw.description
            if raw.url:
                existing.url = raw.url
            existing.is_active = True
            existing.source = existing.source or "linkedin"
            log_activity(session, "discovery", f"LinkedIn job refreshed: {existing.title}")
            return {
                "status": "existing",
                "job_id": existing.id,
                "title": existing.title,
                "company": existing.company.name if existing.company else raw.company,
            }

        company = session.execute(
            select(Company).where(Company.name == raw.company)
        ).scalar_one_or_none()
        if company is None:
            preferred = any(
                p.lower() in raw.company.lower()
                for p in (profile.get("preferred_companies") or [])
            )
            company = Company(name=raw.company, is_preferred=preferred)
            session.add(company)
            session.flush()

        titles = [
            t
            for t in session.execute(
                select(Job.title).where(Job.company_id == company.id)
            ).scalars()
        ]
        if is_fuzzy_duplicate(raw.title, titles):
            raise ValueError("A similar job already exists for this company")

        job = Job(
            company_id=company.id,
            title=raw.title,
            location=raw.location,
            description=raw.description,
            url=raw.url,
            source="linkedin",
            external_id=raw.external_id,
            dedup_key=key,
            posted_at=raw.posted_at,
        )
        session.add(job)
        session.flush()
        _score_job_for_users(session, job, company.id)
        score_row = job.match_score
        log_activity(session, "discovery", f"LinkedIn job imported: {job.title} @ {raw.company}")
        return {
            "status": "created",
            "job_id": job.id,
            "title": job.title,
            "company": raw.company,
            "match_score": round(score_row, 1),
            "jd_fit_score": round(job.jd_fit_score, 1),
            "is_high_priority": job.is_high_priority,
        }

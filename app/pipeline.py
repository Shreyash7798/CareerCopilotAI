"""Job discovery pipeline (spec section 9).

Scheduler -> Discovery -> Parser -> Normalizer -> Duplicate Removal ->
Match Scoring -> SQLite -> Excel -> Notifications.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from sqlalchemy import select

from app import notifications
from app.config import get_settings, get_sources_config
from app.db import session_scope
from app.dedup import dedup_key, is_fuzzy_duplicate
from app.exporter import export_excel
from app.models import Company, Job, log_activity
from app.normalize import normalize, passes_filters
from app.scoring import score_job
from app.sources import REGISTRY
from app.sources.base import RawJob

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    sources_run: int = 0
    sources_failed: int = 0
    fetched: int = 0
    filtered_out: int = 0
    duplicates: int = 0
    new_jobs: int = 0
    high_priority: int = 0
    errors: list[str] = field(default_factory=list)
    new_job_ids: list[int] = field(default_factory=list)


def _get_or_create_company(session, name: str, profile: dict) -> Company:
    company = session.execute(select(Company).where(Company.name == name)).scalar_one_or_none()
    if company is None:
        preferred = any(
            p.lower() in name.lower() for p in (profile.get("preferred_companies") or [])
        )
        company = Company(name=name, is_preferred=preferred)
        session.add(company)
        session.flush()
    return company


def _discover_raw_jobs(result: PipelineResult) -> list[RawJob]:
    cfg = get_sources_config(refresh=True)
    raw_jobs: list[RawJob] = []
    for entry in cfg.get("sources") or []:
        if not entry.get("enabled", True):
            continue
        source_type = entry.get("type", "")
        fetcher = REGISTRY.get(source_type)
        if fetcher is None:
            result.errors.append(f"Unknown source type '{source_type}'")
            continue
        result.sources_run += 1
        try:
            fetched = fetcher(entry)
            raw_jobs.extend(fetched)
            logger.info("Source %s/%s: %d jobs", source_type, entry.get("company"), len(fetched))
        except Exception as exc:  # noqa: BLE001 - sources must be isolated
            result.sources_failed += 1
            result.errors.append(f"{source_type}/{entry.get('company')}: {exc}")
            logger.warning("Source failed: %s", exc)
    result.fetched = len(raw_jobs)
    return raw_jobs


def run_pipeline(notify: bool = True, export: bool = True) -> PipelineResult:
    """Run one full discovery cycle. Safe to call from the scheduler or API."""
    settings = get_settings(refresh=True)
    profile = settings.get("profile", {}) or {}
    scoring_cfg = settings.get("scoring", {}) or {}
    threshold = float(scoring_cfg.get("high_priority_threshold", 70))
    filters = (get_sources_config().get("filters") or {})

    result = PipelineResult()
    raw_jobs = _discover_raw_jobs(result)

    with session_scope() as session:
        existing_keys = set(session.execute(select(Job.dedup_key)).scalars().all())
        titles_by_company: dict[str, list[str]] = {}
        for company_name, title in session.execute(
            select(Company.name, Job.title).join(Company, Job.company_id == Company.id)
        ).all():
            titles_by_company.setdefault(company_name.lower(), []).append(title)

        for raw in raw_jobs:
            raw = normalize(raw)
            if not raw.title:
                continue
            if not passes_filters(raw, filters):
                result.filtered_out += 1
                continue

            key = dedup_key(raw.company, raw.title, raw.location)
            if key in existing_keys:
                result.duplicates += 1
                continue
            company_titles = titles_by_company.setdefault(raw.company.lower(), [])
            if is_fuzzy_duplicate(raw.title, company_titles):
                result.duplicates += 1
                continue

            score, components = score_job(
                title=raw.title,
                description=raw.description,
                location=raw.location,
                company=raw.company,
                profile=profile,
                scoring_cfg=scoring_cfg,
            )
            company = _get_or_create_company(session, raw.company, profile)
            job = Job(
                company_id=company.id,
                title=raw.title,
                location=raw.location,
                description=raw.description,
                url=raw.url,
                source=raw.source,
                external_id=raw.external_id,
                dedup_key=key,
                posted_at=raw.posted_at,
                match_score=score,
                score_breakdown=json.dumps(
                    [
                        {
                            "name": c.name,
                            "score": round(c.score, 3),
                            "weight": round(c.weight, 3),
                            "reason": c.reason,
                        }
                        for c in components
                    ]
                ),
                is_high_priority=score >= threshold,
            )
            session.add(job)
            session.flush()
            existing_keys.add(key)
            company_titles.append(raw.title)
            result.new_jobs += 1
            result.new_job_ids.append(job.id)
            if job.is_high_priority:
                result.high_priority += 1

        log_activity(
            session,
            "discovery",
            f"Pipeline run: {result.new_jobs} new jobs "
            f"({result.high_priority} high priority) from {result.sources_run} sources",
            detail=json.dumps(result.errors) if result.errors else None,
        )

    if export and result.new_jobs:
        try:
            export_excel()
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"excel export: {exc}")
            logger.warning("Excel export failed: %s", exc)

    if notify and result.high_priority:
        try:
            notifications.notify_high_priority(result.new_job_ids)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"notifications: {exc}")
            logger.warning("Notification failed: %s", exc)

    return result

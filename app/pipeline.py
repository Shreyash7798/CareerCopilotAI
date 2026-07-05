"""Job discovery pipeline (spec section 9).

Scheduler -> Discovery -> Parser -> Normalizer -> Duplicate Removal ->
Match Scoring -> SQLite -> Excel -> Notifications.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from sqlalchemy import select

from app import company_sources, notifications
from app.config import get_settings, get_sources_config, sources_yaml_exists
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
    sources_skipped: int = 0
    fetched: int = 0
    filtered_out: int = 0
    duplicates: int = 0
    new_jobs: int = 0
    high_priority: int = 0
    deactivated: int = 0
    reactivated: int = 0
    errors: list[str] = field(default_factory=list)
    new_job_ids: list[int] = field(default_factory=list)


# Connectors that return the complete list of a company's open roles in one
# run. Only for these is "job no longer returned" reliable evidence that the
# posting closed; paginated/search-limited connectors (workday, careers_page,
# smartrecruiters) could exceed fetch caps and cause false deactivations.
FULL_LIST_CONNECTORS = {"greenhouse", "lever"}


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


def _run_entry(entry: dict, result: PipelineResult) -> list[RawJob]:
    """Fetch one source entry, isolating failures. Returns [] on error."""
    source_type = entry.get("type", "")
    fetcher = REGISTRY.get(source_type)
    if fetcher is None:
        result.errors.append(f"Unknown source type '{source_type}'")
        return []
    result.sources_run += 1
    try:
        fetched = fetcher(entry)
        logger.info("Source %s/%s: %d jobs", source_type, entry.get("company"), len(fetched))
        return fetched
    except Exception as exc:  # noqa: BLE001 - sources must be isolated
        result.sources_failed += 1
        result.errors.append(f"{source_type}/{entry.get('company')}: {exc}")
        logger.warning("Source failed: %s", exc)
        return []


def _discover_raw_jobs(
    result: PipelineResult,
) -> tuple[list[RawJob], dict[str, list[str]], dict[str, dict]]:
    """Discover jobs from database-configured companies.

    Companies (with ats_type set) are the primary configuration:
        Dashboard -> Company table -> Discovery pipeline
    A legacy sources.yaml, if present, is imported into the table once on the
    first run after upgrading, so existing installs migrate automatically.

    Returns (raw jobs, per-company keyword filters, per-company run info).
    """
    raw_jobs: list[RawJob] = []
    keyword_map: dict[str, list[str]] = {}
    run_info: dict[str, dict] = {}

    with session_scope() as session:
        companies = company_sources.source_companies(session)
        if not companies and sources_yaml_exists():
            imported = company_sources.seed_from_yaml_config(
                session, get_sources_config(refresh=True)
            )
            if imported:
                log_activity(
                    session, "discovery", f"Imported {imported} companies from sources.yaml"
                )
                session.flush()
                companies = company_sources.source_companies(session)

        now = utcnow_naive()
        for company in companies:
            if not company.enabled:
                continue
            if not company_sources.is_due(company, now):
                result.sources_skipped += 1
                continue
            entry = company_sources.entry_from_company(company)
            if entry is None:
                continue
            kws = company_sources.keywords_list(company)
            if kws:
                keyword_map[company.name.lower()] = kws
            failures_before = result.sources_failed
            fetched = _run_entry(entry, result)
            raw_jobs.extend(fetched)
            ok = result.sources_failed == failures_before
            run_info[company.name.lower()] = {
                "company_id": company.id,
                "connector": entry["type"],
                "ok": ok,
                "fetched": len(fetched),
            }
            company.last_run_at = now
            company.last_run_status = (
                f"{len(fetched)} jobs fetched"
                if ok
                else (result.errors[-1][:250] if result.errors else "failed")
            )

    result.fetched = len(raw_jobs)
    return raw_jobs, keyword_map, run_info


def utcnow_naive():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(tzinfo=None)


def _passes_company_keywords(raw: RawJob, keyword_map: dict[str, list[str]]) -> bool:
    kws = keyword_map.get(raw.company.lower())
    if not kws:
        return True
    title = raw.title.lower()
    return any(k in title for k in kws)


def _score_breakdown_json(components) -> str:
    return json.dumps(
        [
            {
                "name": c.name,
                "score": round(c.score, 3),
                "weight": round(c.weight, 3),
                "reason": c.reason,
            }
            for c in components
        ]
    )


def rescore_all_jobs() -> int:
    """Re-score every stored job against the current profile and weights.

    Called after a CV upload or profile change so existing discoveries
    re-rank for the new user/preferences instead of keeping stale scores.
    Does not re-trigger notifications. Returns the number of jobs rescored.
    """
    settings = get_settings(refresh=True)
    profile = settings.get("profile", {}) or {}
    scoring_cfg = settings.get("scoring", {}) or {}
    threshold = float(scoring_cfg.get("high_priority_threshold", 70))

    count = 0
    with session_scope() as session:
        for job in session.execute(select(Job)).scalars():
            score, components = score_job(
                title=job.title,
                description=job.description or "",
                location=job.location or "",
                company=job.company.name if job.company else "",
                profile=profile,
                scoring_cfg=scoring_cfg,
            )
            job.match_score = score
            job.score_breakdown = _score_breakdown_json(components)
            job.is_high_priority = score >= threshold
            count += 1
        log_activity(session, "scoring", f"Rescored {count} jobs against the current profile")
    return count


def run_pipeline(notify: bool = True, export: bool = True) -> PipelineResult:
    """Run one full discovery cycle. Safe to call from the scheduler or API."""
    settings = get_settings(refresh=True)
    profile = settings.get("profile", {}) or {}
    scoring_cfg = settings.get("scoring", {}) or {}
    threshold = float(scoring_cfg.get("high_priority_threshold", 70))
    # Global filters come from a user-created sources.yaml only; the example
    # file's consulting-specific filters must not constrain other users.
    filters = (get_sources_config().get("filters") or {}) if sources_yaml_exists() else {}

    result = PipelineResult()
    raw_jobs, keyword_map, run_info = _discover_raw_jobs(result)

    with session_scope() as session:
        # dedup_key -> (job id, is_active) so returning jobs can be reactivated
        existing_jobs = {
            key: (job_id, active)
            for key, job_id, active in session.execute(
                select(Job.dedup_key, Job.id, Job.is_active)
            ).all()
        }
        titles_by_company: dict[str, list[str]] = {}
        for company_name, title in session.execute(
            select(Company.name, Job.title).join(Company, Job.company_id == Company.id)
        ).all():
            titles_by_company.setdefault(company_name.lower(), []).append(title)

        # Every key seen this run, per company (pre-filter, so filtered jobs
        # still count as "still listed" and don't cause false deactivation).
        seen_keys_by_company: dict[str, set[str]] = {}

        for raw in raw_jobs:
            raw = normalize(raw)
            if not raw.title:
                continue
            key = dedup_key(raw.company, raw.title, raw.location)
            seen_keys_by_company.setdefault(raw.company.lower(), set()).add(key)

            if not passes_filters(raw, filters):
                result.filtered_out += 1
                continue
            if not _passes_company_keywords(raw, keyword_map):
                result.filtered_out += 1
                continue

            if key in existing_jobs:
                result.duplicates += 1
                job_id, active = existing_jobs[key]
                if not active:
                    stored = session.get(Job, job_id)
                    if stored is not None:
                        stored.is_active = True
                        result.reactivated += 1
                        existing_jobs[key] = (job_id, True)
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
                score_breakdown=_score_breakdown_json(components),
                is_high_priority=score >= threshold,
            )
            session.add(job)
            session.flush()
            existing_jobs[key] = (job.id, True)
            company_titles.append(raw.title)
            result.new_jobs += 1
            result.new_job_ids.append(job.id)
            if job.is_high_priority:
                result.high_priority += 1

        # Deactivate postings that disappeared from full-list sources.
        for name_lower, info in run_info.items():
            if (
                not info["ok"]
                or info["connector"] not in FULL_LIST_CONNECTORS
                or info["fetched"] == 0  # transient empty response guard
            ):
                continue
            seen = seen_keys_by_company.get(name_lower, set())
            stale_jobs = (
                session.execute(
                    select(Job).where(
                        Job.company_id == info["company_id"],
                        Job.is_active.is_(True),
                        Job.dedup_key.notin_(seen),
                    )
                )
                .scalars()
                .all()
            )
            for job in stale_jobs:
                job.is_active = False
                result.deactivated += 1

        if result.deactivated or result.reactivated:
            log_activity(
                session,
                "discovery",
                f"Job lifecycle: {result.deactivated} closed, {result.reactivated} relisted",
            )

        log_activity(
            session,
            "discovery",
            f"Pipeline run: {result.new_jobs} new jobs "
            f"({result.high_priority} high priority) from {result.sources_run} sources",
            detail=json.dumps(result.errors) if result.errors else None,
        )

    # Always export so the workbook also reflects application/recruiter edits
    # made since the last run, not just new jobs.
    if export:
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

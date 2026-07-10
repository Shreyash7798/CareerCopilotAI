"""Job discovery pipeline (spec section 9).

Scheduler -> Discovery -> Parser -> Normalizer -> Duplicate Removal ->
Match Scoring -> SQLite -> Excel -> Notifications.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select

from app import company_sources, notifications
from app.config import get_settings, get_sources_config, sources_yaml_exists
from app.db import session_scope
from app.dedup import dedup_key, is_fuzzy_duplicate
from app.exporter import export_excel_all_users, export_google_sheets
from app.models import Application, Company, Job, User, UserCompanyMonitor, log_activity
from app.normalize import normalize, passes_filters
from app.job_visibility import is_aged_out, visibility_settings
from app.recruiter_discovery import upsert_recruiters
from app.scoring import score_jd_fit, score_job
from app.sources import REGISTRY
from app.sources.base import RawJob
from app.user_access import (
    active_user_ids,
    keywords_for_monitor,
    scoring_context_for_user,
    title_matches_keywords,
    upsert_user_job_score,
    user_ids_monitoring_company,
)

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
FULL_LIST_CONNECTORS = {"greenhouse", "lever", "accenture", "smartrecruiters"}


def _pipeline_settings() -> dict:
    return get_settings().get("pipeline", {}) or {}


def _source_timeout_seconds() -> float:
    return float(_pipeline_settings().get("source_timeout_seconds") or 120)


def _max_sources_per_run() -> int:
    return int(_pipeline_settings().get("max_sources_per_run") or 25)


def _fetch_source(entry: dict) -> list[RawJob]:
    """Fetch one source entry. Raises on failure."""
    source_type = entry.get("type", "")
    fetcher = REGISTRY.get(source_type)
    if fetcher is None:
        raise ValueError(f"Unknown source type '{source_type}'")
    fetched = fetcher(entry)
    logger.info("Source %s/%s: %d jobs", source_type, entry.get("company"), len(fetched))
    return fetched


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
    company = entry.get("company", "")
    if source_type not in REGISTRY:
        result.errors.append(f"Unknown source type '{source_type}'")
        return []

    result.sources_run += 1
    timeout = _source_timeout_seconds()
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_fetch_source, entry)
            return future.result(timeout=timeout)
    except FuturesTimeoutError:
        result.sources_failed += 1
        msg = f"{source_type}/{company}: timed out after {int(timeout)}s"
        result.errors.append(msg)
        logger.warning("Source timed out: %s", msg)
        return []
    except Exception as exc:  # noqa: BLE001 - sources must be isolated
        result.sources_failed += 1
        result.errors.append(f"{source_type}/{company}: {exc}")
        logger.warning("Source failed: %s", exc)
        return []


def _discover_raw_jobs(
    result: PipelineResult,
) -> tuple[list[RawJob], dict[str, dict]]:
    """Discover jobs from companies monitored by any user."""
    raw_jobs: list[RawJob] = []
    run_info: dict[str, dict] = {}

    with session_scope() as session:
        companies = list(company_sources.source_companies(session))
        if not companies and sources_yaml_exists():
            imported = company_sources.seed_from_yaml_config(
                session, get_sources_config(refresh=True)
            )
            if imported:
                log_activity(
                    session, "discovery", f"Imported {imported} companies from sources.yaml"
                )
                session.flush()
                companies = list(company_sources.source_companies(session))

    now = utcnow_naive()
    max_sources = _max_sources_per_run()
    polled = 0

    with session_scope() as session:
        companies = list(company_sources.source_companies(session))

    for company in companies:
        with session_scope() as session:
            db_company = session.get(Company, company.id)
            if db_company is None:
                continue
            if not company_sources.is_due(db_company, now, session=session):
                result.sources_skipped += 1
                continue
        if polled >= max_sources:
            result.sources_skipped += 1
            continue
        entry = company_sources.entry_from_company(company)
        if entry is None:
            continue
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
        polled += 1
        status = (
            f"{len(fetched)} jobs fetched"
            if ok
            else (result.errors[-1][:250] if result.errors else "failed")
        )
        with session_scope() as session:
            db_company = session.get(Company, company.id)
            if db_company is not None:
                db_company.last_run_at = now
                db_company.last_run_status = status

    result.fetched = len(raw_jobs)
    return raw_jobs, run_info


def utcnow_naive():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(tzinfo=None)


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


@dataclass
class _ScoringCache:
    """Preloaded users, monitors, and scoring context to avoid per-job DB round-trips."""

    users: dict[int, User]
    contexts: dict[int, tuple[dict, dict]]
    monitors_by_company: dict[int, dict[int, UserCompanyMonitor]]
    monitors_by_user: dict[int, set[int]]

    @classmethod
    def build(cls, session, *, user_id: int | None = None) -> _ScoringCache:
        user_ids = [user_id] if user_id is not None else active_user_ids(session)
        users: dict[int, User] = {}
        contexts: dict[int, tuple[dict, dict]] = {}
        for uid in user_ids:
            user = session.get(User, uid)
            if user is None or not user.is_active:
                continue
            users[uid] = user
            contexts[uid] = scoring_context_for_user(user)

        monitors_by_company: dict[int, dict[int, UserCompanyMonitor]] = defaultdict(dict)
        monitors_by_user: dict[int, set[int]] = defaultdict(set)
        if user_ids:
            for monitor in session.execute(
                select(UserCompanyMonitor).where(
                    UserCompanyMonitor.enabled.is_(True),
                    UserCompanyMonitor.user_id.in_(user_ids),
                )
            ).scalars():
                monitors_by_company[monitor.company_id][monitor.user_id] = monitor
                monitors_by_user[monitor.user_id].add(monitor.company_id)

        return cls(
            users=users,
            contexts=contexts,
            monitors_by_company=dict(monitors_by_company),
            monitors_by_user=dict(monitors_by_user),
        )

    def user_ids_for_company(self, company_id: int | None) -> list[int]:
        if company_id and company_id in self.monitors_by_company:
            return [uid for uid in self.monitors_by_company[company_id] if uid in self.users]
        return list(self.users.keys())


def _score_job_for_users(
    session,
    job: Job,
    company_id: int | None,
    *,
    cache: _ScoringCache | None = None,
) -> int:
    """Score one job for every user monitoring its company. Returns high-priority count."""
    if cache is not None:
        user_ids = cache.user_ids_for_company(company_id)
        monitors = cache.monitors_by_company.get(company_id, {}) if company_id else {}
    else:
        user_ids = user_ids_monitoring_company(session, company_id) if company_id else active_user_ids(session)
        if not user_ids:
            user_ids = active_user_ids(session)
        monitors = {
            m.user_id: m
            for m in session.execute(
                select(UserCompanyMonitor).where(
                    UserCompanyMonitor.company_id == company_id,
                    UserCompanyMonitor.user_id.in_(user_ids),
                )
            ).scalars()
        } if company_id else {}

    high = 0
    for uid in user_ids:
        if cache is not None:
            user = cache.users.get(uid)
            if user is None:
                continue
            profile, scoring_cfg = cache.contexts[uid]
        else:
            user = session.get(User, uid)
            if user is None or not user.is_active:
                continue
            profile, scoring_cfg = scoring_context_for_user(user)

        monitor = monitors.get(uid)
        if monitor is not None and not title_matches_keywords(job.title, keywords_for_monitor(monitor)):
            continue
        threshold = float(scoring_cfg.get("high_priority_threshold", 70))
        score, components = score_job(
            title=job.title,
            description=job.description or "",
            location=job.location or "",
            company=job.company.name if job.company else "",
            profile=profile,
            scoring_cfg=scoring_cfg,
        )
        jd_score, jd_components = score_jd_fit(
            title=job.title,
            description=job.description or "",
            company=job.company.name if job.company else "",
            profile=profile,
            scoring_cfg=scoring_cfg,
        )
        is_hp = score >= threshold
        upsert_user_job_score(
            session,
            user_id=uid,
            job_id=job.id,
            match_score=score,
            score_breakdown=_score_breakdown_json(components),
            jd_fit_score=jd_score,
            jd_fit_breakdown=_score_breakdown_json(jd_components),
            is_high_priority=is_hp,
        )
        if is_hp:
            high += 1
        if uid == user_ids[0]:
            job.match_score = score
            job.score_breakdown = _score_breakdown_json(components)
            job.jd_fit_score = jd_score
            job.jd_fit_breakdown = _score_breakdown_json(jd_components)
            job.is_high_priority = is_hp
    return high


def rescore_all_jobs(user_id: int | None = None) -> int:
    """Re-score jobs for one user or all active users."""
    count = 0
    with session_scope() as session:
        cache = _ScoringCache.build(session, user_id=user_id)
        if not cache.users:
            return 0

        if user_id is not None:
            company_ids = list(cache.monitors_by_user.get(user_id, set()))
            if not company_ids:
                return 0
            jobs = session.execute(select(Job).where(Job.company_id.in_(company_ids))).scalars()
        else:
            jobs = session.execute(select(Job)).scalars()

        for job in jobs:
            _score_job_for_users(session, job, job.company_id, cache=cache)
            count += 1
        log_activity(
            session,
            "scoring",
            f"Rescored {count} jobs for {len(cache.users)} user(s)",
            user_id=user_id,
        )
    return count


def run_pipeline(notify: bool = True, export: bool = True) -> PipelineResult:
    """Run one full discovery cycle. Safe to call from the scheduler or API."""
    settings = get_settings(refresh=True)
    default_profile = (settings.get("profile") or {})
    # Global filters come from a user-created sources.yaml only; the example
    # file's consulting-specific filters must not constrain other users.
    filters = (get_sources_config().get("filters") or {}) if sources_yaml_exists() else {}

    result = PipelineResult()

    with session_scope() as session:
        due_count = sum(
            1
            for company in company_sources.source_companies(session)
            if company_sources.is_due(company, session=session)
        )
        log_activity(
            session,
            "discovery",
            f"Discovery started: {due_count} companies due",
        )

    raw_jobs, run_info = _discover_raw_jobs(result)

    with session_scope() as session:
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

            if key in existing_jobs:
                result.duplicates += 1
                job_id, active = existing_jobs[key]
                stored = session.get(Job, job_id)
                if stored is not None:
                    if raw.posted_at and (
                        stored.posted_at is None or raw.posted_at > stored.posted_at
                    ):
                        stored.posted_at = raw.posted_at
                    if not active:
                        stored.is_active = True
                        result.reactivated += 1
                        existing_jobs[key] = (job_id, True)
                continue
            company_titles = titles_by_company.setdefault(raw.company.lower(), [])
            if is_fuzzy_duplicate(raw.title, company_titles):
                result.duplicates += 1
                continue

            company = _get_or_create_company(session, raw.company, default_profile)
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
            )
            session.add(job)
            session.flush()
            hp = _score_job_for_users(session, job, company.id)
            existing_jobs[key] = (job.id, True)
            company_titles.append(raw.title)
            result.new_jobs += 1
            result.new_job_ids.append(job.id)
            if hp > 0:
                result.high_priority += 1
            if company.recruiter_search_enabled and job.description:
                upsert_recruiters(
                    session,
                    company_id=company.id,
                    company_name=company.name,
                    job_title=job.title,
                    description=job.description,
                )

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

        # Deactivate untracked jobs that exceeded max posting age.
        tracked_ids = {
            row[0]
            for row in session.execute(
                select(Application.job_id).where(Application.job_id.isnot(None))
            ).all()
        }
        vis_cfg = visibility_settings()
        now = utcnow_naive()
        if vis_cfg.get("hide_stale_untracked", True):
            for job in session.execute(select(Job).where(Job.is_active.is_(True))).scalars():
                if job.id in tracked_ids:
                    continue
                if is_aged_out(job, vis_cfg, now=now):
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
            export_excel_all_users()
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"excel export: {exc}")
            logger.warning("Excel export failed: %s", exc)
        try:
            from app.user_access import active_user_ids

            with session_scope() as session:
                for uid in active_user_ids(session):
                    export_google_sheets(uid)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"google sheets export: {exc}")
            logger.warning("Google Sheets export failed: %s", exc)

    if notify and result.high_priority:
        try:
            notifications.notify_high_priority(result.new_job_ids)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"notifications: {exc}")
            logger.warning("Notification failed: %s", exc)

    return result

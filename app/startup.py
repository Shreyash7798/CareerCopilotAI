"""First-run bootstrap and startup maintenance.

On a fresh deploy this loads a cloud-verified starter pack, runs discovery
once, and backfills missing locations so Mumbai/Pune roles score correctly.
"""

from __future__ import annotations

import json
import logging
import threading

from sqlalchemy import func, select

from app.company_sources import enabled_source_count, seed_from_yaml_config
from app.config import (
    PROJECT_ROOT,
    get_company_catalog,
    get_settings,
    get_sources_config,
    sources_yaml_exists,
)
from app.db import session_scope
from app.location_infer import infer_location
from app.models import Company, Job, User, log_activity
from app.user_access import ensure_monitor
from app.scheduler import schedule_deferred_discovery

logger = logging.getLogger(__name__)

STARTER_PACK_FILE = PROJECT_ROOT / "config" / "starter_pack.yaml"
ACCENTURE_CATALOG_NAME = "Accenture India"


def _load_starter_pack() -> list[dict]:
    if not STARTER_PACK_FILE.exists():
        return []
    import yaml

    data = yaml.safe_load(STARTER_PACK_FILE.read_text(encoding="utf-8")) or {}
    return list(data.get("companies") or [])


def _catalog_entry(sector: str, name: str) -> dict | None:
    catalog = get_company_catalog()
    for sec in catalog.get("sectors", []):
        if sec.get("name") != sector:
            continue
        for item in sec.get("companies", []):
            if item.get("name") == name:
                return {**item, "sector": sector}
    return None


def _apply_catalog_entry(session, entry: dict) -> bool:
    """Add or upgrade a company from a catalog entry. Returns True if changed."""
    name = entry.get("name")
    if not name:
        return False
    company = session.execute(select(Company).where(Company.name == name)).scalar_one_or_none()
    changed = False
    if company is None:
        company = Company(name=name)
        session.add(company)
        changed = True
    new_type = entry.get("ats_type")
    if new_type and company.ats_type != new_type:
        company.ats_type = new_type
        changed = True
    if entry.get("sector") and company.sector != entry.get("sector"):
        company.sector = entry["sector"]
        changed = True
    if entry.get("country") and company.country != entry.get("country"):
        company.country = entry["country"]
        changed = True
    if entry.get("career_url") and company.career_url != entry.get("career_url"):
        company.career_url = entry["career_url"]
        changed = True
    new_cfg = json.dumps(entry["ats_config"]) if entry.get("ats_config") else None
    if new_cfg and company.ats_config != new_cfg:
        company.ats_config = new_cfg
        changed = True
    if not entry.get("caveat") and not company.enabled:
        company.enabled = True
        changed = True
    if changed and company.ats_type:
        _ensure_monitors_for_active_users(session, company)
    return changed


def enabled_company_count(session) -> int:
    return enabled_source_count(session)


def _ensure_monitors_for_active_users(session, company: Company) -> None:
    if not company.ats_type:
        return
    for user_id in session.execute(select(User.id).where(User.is_active.is_(True))).scalars():
        ensure_monitor(session, user_id, company, enabled=company.enabled)


def bootstrap_starter_pack(session) -> int:
    added = 0
    for item in _load_starter_pack():
        entry = _catalog_entry(item.get("sector", ""), item.get("name", ""))
        if entry is None:
            continue
        if _apply_catalog_entry(session, entry):
            added += 1
    return added


def bootstrap_starter_pack_for_user(session, user_id: int) -> int:
    """Load starter companies and enable monitors for one user only."""
    from app.user_access import ensure_monitor

    bootstrap_starter_pack(session)
    enabled = 0
    for item in _load_starter_pack():
        entry = _catalog_entry(item.get("sector", ""), item.get("name", ""))
        if entry is None:
            continue
        company = session.execute(
            select(Company).where(Company.name == entry["name"])
        ).scalar_one_or_none()
        if company is None or not company.ats_type:
            continue
        ensure_monitor(session, user_id, company, enabled=True)
        enabled += 1
    return enabled


def ensure_accenture(session) -> bool:
    """Upgrade legacy disabled Accenture careers_page rows to the API connector."""
    entry = None
    for sec in get_company_catalog().get("sectors", []):
        for item in sec.get("companies", []):
            if item.get("name") == ACCENTURE_CATALOG_NAME:
                entry = {**item, "sector": sec.get("name")}
                break
    if entry is None:
        return False
    return _apply_catalog_entry(session, entry)


def backfill_job_locations(session) -> int:
    updated = 0
    for job in session.execute(select(Job)).scalars():
        inferred = infer_location(
            title=job.title or "",
            url=job.url or "",
            existing=job.location or "",
        )
        if inferred and inferred != (job.location or ""):
            job.location = inferred
            updated += 1
    return updated


def run_startup_tasks() -> None:
    settings = get_settings()
    cfg = settings.get("bootstrap", {}) or {}
    if cfg.get("enabled", True) is False:
        return

    bootstrapped = 0
    accenture_added = False
    locations_updated = 0
    should_discover = False

    with session_scope() as session:
        if cfg.get("starter_pack_on_first_run", True) and enabled_company_count(session) == 0:
            if sources_yaml_exists():
                imported = seed_from_yaml_config(session, get_sources_config(refresh=True))
                if imported:
                    log_activity(session, "config", f"Imported {imported} companies from sources.yaml")
            if enabled_company_count(session) == 0:
                bootstrapped = bootstrap_starter_pack(session)
                if bootstrapped:
                    log_activity(
                        session,
                        "config",
                        f"Starter pack loaded: {bootstrapped} companies ready to monitor",
                    )
                    should_discover = True

        if cfg.get("ensure_accenture", True):
            accenture_added = ensure_accenture(session)
            if accenture_added:
                log_activity(session, "config", "Accenture India connector enabled")

        if cfg.get("backfill_locations_on_startup", True):
            locations_updated = backfill_job_locations(session)
            if locations_updated:
                log_activity(
                    session,
                    "discovery",
                    f"Backfilled location on {locations_updated} jobs from title/URL",
                )

    if locations_updated:
        from app.pipeline import rescore_all_jobs

        rescored = rescore_all_jobs()
        logger.info("Rescored %d jobs after location backfill", rescored)

    job_count = 0
    with session_scope() as session:
        job_count = session.execute(select(func.count(Job.id))).scalar() or 0

    if cfg.get("run_discovery_on_startup", True) and (
        should_discover or bootstrapped or accenture_added or job_count == 0
    ):
        delay = int(cfg.get("discovery_startup_delay_seconds") or 180)
        schedule_deferred_discovery(delay_seconds=delay)
        logger.info(
            "Startup discovery deferred by %d seconds (first-run bootstrap, jobs=%d)",
            delay,
            job_count,
        )


def schedule_startup_tasks() -> None:
    """Run maintenance in a daemon thread so the web server binds immediately."""
    thread = threading.Thread(target=run_startup_tasks, name="careercopilot-startup", daemon=True)
    thread.start()

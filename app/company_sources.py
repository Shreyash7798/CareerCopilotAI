"""Database-backed discovery configuration (Company Management MVP).

Turns Company rows (where ats_type is set) into the source-entry dicts the
connectors in app/sources/ expect, replacing routine sources.yaml editing:

    Dashboard -> Company table -> Discovery pipeline

'sap', 'oracle' and 'taleo' have no dedicated JSON API connector; they are
served by the generic careers-page connector with platform-appropriate
defaults (SAP SuccessFactors career sites share the `a.jobTitle-link` markup;
Oracle/Taleo sites are JavaScript-rendered).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.models import Company, UserCompanyMonitor

# ats_type -> (connector type, default ats_config)
ATS_DEFAULTS: dict[str, tuple[str, dict]] = {
    "accenture": ("accenture", {"country_site": "in-en", "job_language": "en", "sort_by": "0"}),
    "greenhouse": ("greenhouse", {}),
    "lever": ("lever", {}),
    "workday": ("workday", {}),
    "smartrecruiters": ("smartrecruiters", {}),
    "sap": ("careers_page", {"link_selector": "a.jobTitle-link", "render": False}),
    "oracle": ("careers_page", {"link_selector": "a[href*='/job/']", "render": True}),
    "taleo": ("careers_page", {"link_selector": "a[href*='job']", "render": True}),
    "careers_page": ("careers_page", {}),
    "crawl4ai": ("crawl4ai", {"detail_limit": 15}),
    "linkedin": ("linkedin", {"f_TPR": "r604800", "max_pages": 5, "detail_limit": 20}),
}


def parse_ats_config(company: Company) -> dict:
    if not company.ats_config:
        return {}
    try:
        data = json.loads(company.ats_config)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def entry_from_company(company: Company) -> dict | None:
    """Build the source-entry dict for one company, or None if incomplete."""
    if not company.ats_type:
        return None
    connector, defaults = ATS_DEFAULTS.get(company.ats_type, ("careers_page", {}))
    entry: dict = {**defaults, **parse_ats_config(company)}
    entry["type"] = connector
    entry["company"] = company.name
    entry["enabled"] = bool(company.enabled)
    if company.career_url and "url" not in entry:
        entry["url"] = company.career_url
    if company.country:
        entry["country"] = company.country
    return entry


def keywords_list(company: Company) -> list[str]:
    if not company.keywords:
        return []
    return [k.strip().lower() for k in company.keywords.split(",") if k.strip()]


def is_due(company: Company, now: datetime | None = None, session=None) -> bool:
    """Per-company refresh interval from the shortest active monitor interval."""
    interval = company.refresh_interval_minutes
    if session is not None:
        monitors = session.execute(
            select(UserCompanyMonitor).where(
                UserCompanyMonitor.company_id == company.id,
                UserCompanyMonitor.enabled.is_(True),
            )
        ).scalars()
        intervals = [
            m.refresh_interval_minutes or company.refresh_interval_minutes
            for m in monitors
            if (m.refresh_interval_minutes or company.refresh_interval_minutes)
        ]
        if intervals:
            interval = min(intervals)
    if not interval or not company.last_run_at:
        return True
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    elapsed_min = (now - company.last_run_at).total_seconds() / 60
    return elapsed_min >= interval


def source_companies(session) -> list[Company]:
    """Companies with at least one enabled user monitor, oldest-checked first."""
    any_monitors = (
        session.execute(select(func.count(UserCompanyMonitor.id))).scalar() or 0
    ) > 0
    if any_monitors:
        monitored_ids = (
            session.execute(
                select(UserCompanyMonitor.company_id)
                .where(UserCompanyMonitor.enabled.is_(True))
                .group_by(UserCompanyMonitor.company_id)
            )
            .scalars()
            .all()
        )
        if not monitored_ids:
            return []
        return (
            session.execute(
                select(Company)
                .where(Company.ats_type.isnot(None), Company.id.in_(monitored_ids))
                .order_by(Company.last_run_at.asc().nulls_first(), Company.name)
            )
            .scalars()
            .all()
        )
    return (
        session.execute(
            select(Company)
            .where(Company.ats_type.isnot(None), Company.enabled.is_(True))
            .order_by(Company.last_run_at.asc().nulls_first(), Company.name)
        )
        .scalars()
        .all()
    )


def enabled_source_count(session) -> int:
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


# --------------------------------------------------------------- YAML import

# Keys that belong in ats_config when importing a sources.yaml entry.
_ATS_CONFIG_KEYS = (
    "board",
    "host",
    "tenant",
    "site",
    "search_text",
    "link_selector",
    "render",
    "detail_limit",
    "default_location",
    "country_site",
    "job_language",
    "job_country",
    "job_keyword",
    "sort_by",
    "max_result_size",
    "detail_selector",
)


def seed_from_yaml_config(session, sources_cfg: dict) -> int:
    """One-time import of sources.yaml entries into the companies table.

    Existing installs (configured over SSH) migrate automatically on the
    first run after upgrading; afterwards the dashboard is the only place
    configuration is edited. Companies already configured in the DB are
    left untouched. Returns the number of companies imported.
    """
    imported = 0
    for entry in sources_cfg.get("sources") or []:
        name = (entry.get("company") or "").strip()
        source_type = entry.get("type", "")
        if not name or source_type not in ATS_DEFAULTS:
            continue
        company = session.execute(select(Company).where(Company.name == name)).scalar_one_or_none()
        if company is not None and company.ats_type:
            continue  # already configured in the DB
        if company is None:
            company = Company(name=name)
            session.add(company)
        company.ats_type = source_type
        company.enabled = bool(entry.get("enabled", True))
        company.career_url = entry.get("url") or company.career_url
        ats_config = {k: entry[k] for k in _ATS_CONFIG_KEYS if k in entry}
        company.ats_config = json.dumps(ats_config) if ats_config else None
        imported += 1
    return imported

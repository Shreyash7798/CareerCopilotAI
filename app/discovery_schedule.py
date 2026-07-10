"""Discovery cadence: poll every enabled company 2–3×/day on a small VM.

When many companies are monitored, runs become more frequent (down to hourly).
When few companies fit in one batch, we still schedule enough runs per day so
each company is checked multiple times, not just once.
"""

from __future__ import annotations

import logging
import math

from sqlalchemy import func, select

from app.company_sources import enabled_source_count
from app.config import get_settings
from app.db import session_scope

logger = logging.getLogger(__name__)

MINUTES_PER_DAY = 1440


def _scheduler_settings() -> dict:
    return get_settings().get("scheduler", {}) or {}


def _pipeline_settings() -> dict:
    return get_settings().get("pipeline", {}) or {}


def enabled_company_count(session=None) -> int:
    if session is not None:
        return enabled_source_count(session)
    with session_scope() as scoped:
        return enabled_source_count(scoped)


def compute_discovery_interval_minutes(
    enabled_count: int,
    *,
    max_per_run: int | None = None,
    target_polls_per_day: int | None = None,
    min_interval: int | None = None,
    max_interval: int | None = None,
) -> int:
    """Return minutes between discovery runs for *enabled_count* companies."""
    sched = _scheduler_settings()
    pipe = _pipeline_settings()
    max_per_run = max_per_run if max_per_run is not None else int(pipe.get("max_sources_per_run") or 25)
    target_polls_per_day = target_polls_per_day if target_polls_per_day is not None else int(
        sched.get("target_polls_per_company_per_day") or 3
    )
    min_interval = min_interval if min_interval is not None else int(
        sched.get("min_discovery_interval_minutes") or 60
    )
    max_interval = max_interval if max_interval is not None else int(
        sched.get("max_discovery_interval_minutes") or 360
    )
    auto = sched.get("discovery_auto_interval", True)

    if not auto:
        return int(sched.get("discovery_interval_minutes") or 180)

    if enabled_count <= 0:
        return max_interval

    max_per_run = max(1, max_per_run)
    target_polls_per_day = max(1, target_polls_per_day)

    polls_needed = enabled_count * target_polls_per_day
    runs_from_volume = math.ceil(polls_needed / max_per_run)
    # When all companies fit in one batch, still run multiple times per day.
    runs_from_frequency = target_polls_per_day if enabled_count <= max_per_run else 0
    runs_per_day = max(runs_from_volume, runs_from_frequency, 1)
    interval = MINUTES_PER_DAY // runs_per_day
    return max(min_interval, min(max_interval, interval))


def effective_discovery_interval_minutes() -> int:
    return compute_discovery_interval_minutes(enabled_company_count())


def format_interval_label(minutes: int) -> str:
    """Human-readable cadence, e.g. 'every 2h 40m'."""
    if minutes >= 60:
        hrs = minutes // 60
        mins_rem = minutes % 60
        if mins_rem == 0:
            return "every hour" if hrs == 1 else f"every {hrs} hours"
        return f"every {hrs}h {mins_rem}m"
    return f"every {minutes} min"


def discovery_summary_for_user(session, user_id: int) -> dict:
    """Per-user discovery cadence for dashboard and settings."""
    from app.user_access import enabled_monitor_count

    user_monitored = enabled_monitor_count(session, user_id)
    global_monitored = enabled_company_count(session)
    sched = _scheduler_settings()
    target = int(sched.get("target_polls_per_company_per_day") or 3)
    user_interval = compute_discovery_interval_minutes(user_monitored)
    global_interval = compute_discovery_interval_minutes(global_monitored)
    return {
        "user_monitored_companies": user_monitored,
        "global_monitored_companies": global_monitored,
        "enabled_companies": user_monitored,
        "target_polls_per_company_per_day": target,
        "user_discovery_interval_minutes": user_interval,
        "discovery_interval_minutes": user_interval,
        "global_discovery_interval_minutes": global_interval,
        "user_interval_label": format_interval_label(user_interval),
        "global_interval_label": format_interval_label(global_interval),
        "auto_interval": bool(sched.get("discovery_auto_interval", True)),
        "shared_server": global_monitored > user_monitored,
    }


def discovery_schedule_summary(user_id: int | None = None, session=None) -> dict:
    """Human-readable snapshot for settings / dashboard."""
    if user_id is not None and session is not None:
        return discovery_summary_for_user(session, user_id)

    enabled = enabled_company_count()
    sched = _scheduler_settings()
    pipe = _pipeline_settings()
    max_per_run = int(pipe.get("max_sources_per_run") or 25)
    target = int(sched.get("target_polls_per_company_per_day") or 3)
    interval = effective_discovery_interval_minutes()
    polls_needed = enabled * target
    runs_per_day = max(1, MINUTES_PER_DAY // interval) if interval else 0
    return {
        "enabled_companies": enabled,
        "max_sources_per_run": max_per_run,
        "target_polls_per_company_per_day": target,
        "discovery_interval_minutes": interval,
        "runs_per_day": runs_per_day,
        "auto_interval": bool(sched.get("discovery_auto_interval", True)),
        "companies_per_full_cycle": min(enabled, max_per_run) * runs_per_day if enabled else 0,
        "polls_needed_per_day": polls_needed,
    }


def refresh_discovery_interval(scheduler) -> int:
    """Reschedule the discovery job when company count changes. Returns new interval."""
    interval = effective_discovery_interval_minutes()
    job = scheduler.get_job("discovery")
    if job is None:
        return interval

    from apscheduler.triggers.interval import IntervalTrigger

    current = getattr(getattr(job.trigger, "interval", None), "total_seconds", lambda: None)()
    if current is not None and int(current // 60) == interval:
        return interval

    scheduler.reschedule_job("discovery", trigger=IntervalTrigger(minutes=interval))
    logger.info("Discovery interval updated to every %d min (%d enabled companies)", interval, enabled_company_count())
    return interval

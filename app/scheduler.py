"""Background scheduler: periodic discovery runs + daily summary.

Runs inside the FastAPI process via APScheduler, so a single `python run.py`
gives you the web app and the automation together. When deployed on an
always-on host (Raspberry Pi, free-tier VM, home server), discovery keeps
running even when your laptop/phone is off.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import get_settings
from app.notifications import build_daily_summary, send_daily_summary, send_followup_reminders, send_run_summary
from app.pipeline import run_pipeline

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _discovery_job() -> None:
    try:
        result = run_pipeline()
        logger.info(
            "Scheduled discovery: %d new (%d high priority), %d duplicates skipped",
            result.new_jobs,
            result.high_priority,
            result.duplicates,
        )
        try:
            send_run_summary(result)
        except Exception:  # noqa: BLE001
            logger.exception("Run summary notification failed")
    except Exception:  # noqa: BLE001
        logger.exception("Scheduled discovery run failed")


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    settings = get_settings()
    cfg = settings.get("scheduler", {}) or {}
    if not cfg.get("enabled", True):
        logger.info("Scheduler disabled in settings")
        return None

    interval = int(cfg.get("discovery_interval_minutes", 180))
    summary_time = str(cfg.get("daily_summary_time", "08:30"))
    hour, _, minute = summary_time.partition(":")
    # e.g. "Asia/Kolkata"; None keeps the server's local timezone (usually UTC
    # on cloud hosts, which surprises users expecting local-time summaries).
    timezone = cfg.get("timezone") or None

    _scheduler = BackgroundScheduler(**({"timezone": timezone} if timezone else {}))
    _scheduler.add_job(
        _discovery_job,
        IntervalTrigger(minutes=interval),
        id="discovery",
        max_instances=1,
        coalesce=True,
    )
    cron_kwargs = {"timezone": timezone} if timezone else {}
    _scheduler.add_job(
        send_daily_summary,
        CronTrigger(hour=int(hour or 8), minute=int(minute or 30), **cron_kwargs),
        id="daily_summary",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        send_followup_reminders,
        CronTrigger(hour=9, minute=0, **cron_kwargs),
        id="followup_reminders",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info(
        "Scheduler started: discovery every %d min, daily summary at %s", interval, summary_time
    )
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None

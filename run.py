"""Start CareerCopilot AI (web dashboard + background automation).

Usage:
    python run.py                # start server on host/port from settings
    python run.py --once         # run one discovery cycle and exit (for cron)
    python run.py --summary      # send the daily summary now and exit
"""

from __future__ import annotations

import argparse

import uvicorn

from app.config import get_settings
from app.db import init_db


def main() -> None:
    parser = argparse.ArgumentParser(description="CareerCopilot AI")
    parser.add_argument("--once", action="store_true", help="run one discovery cycle and exit")
    parser.add_argument("--summary", action="store_true", help="send the daily summary and exit")
    args = parser.parse_args()

    init_db()

    if args.once:
        from app.discovery_runner import acquire_discovery_lock, clear_discovery_lock
        from app.notifications import send_run_summary
        from app.pipeline import run_pipeline

        if not acquire_discovery_lock():
            print("Discovery already running — skipped.")
            return

        try:
            result = run_pipeline()
            print(
                f"Discovery finished: {result.new_jobs} new jobs "
                f"({result.high_priority} high priority), {result.duplicates} duplicates skipped."
            )
            for error in result.errors:
                print(f"  warning: {error}")
            try:
                send_run_summary(result)
            except Exception:  # noqa: BLE001
                import logging

                logging.getLogger(__name__).exception("Run summary notification failed")
        finally:
            clear_discovery_lock()
        return

    if args.summary:
        from app.notifications import send_daily_summary

        send_daily_summary()
        print("Daily summary sent (to configured channels).")
        return

    app_cfg = get_settings().get("app", {}) or {}
    uvicorn.run(
        "app.main:app",
        host=app_cfg.get("host", "0.0.0.0"),
        port=int(app_cfg.get("port", 8000)),
        log_level="info",
    )


if __name__ == "__main__":
    main()

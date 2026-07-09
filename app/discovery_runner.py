"""Run discovery outside the web server process.

Playwright and long SQLite writes must not share memory with the dashboard.
A small VM can become unresponsive (nginx 504) when discovery runs in-process.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from app.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

LOCK_FILE = PROJECT_ROOT / "data" / ".discovery.lock"


def _ensure_data_dir() -> None:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)


def _read_lock_pid() -> int | None:
    if not LOCK_FILE.exists():
        return None
    try:
        return int(LOCK_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def is_discovery_running() -> bool:
    pid = _read_lock_pid()
    if pid is None:
        return False
    if _pid_alive(pid):
        return True
    clear_discovery_lock()
    return False


def acquire_discovery_lock() -> bool:
    """Mark this process as the active discovery worker."""
    _ensure_data_dir()
    pid = _read_lock_pid()
    if pid is not None and _pid_alive(pid) and pid != os.getpid():
        return False
    LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    return True


def clear_discovery_lock() -> None:
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def run_discovery_subprocess() -> dict:
    """Start `python run.py --once` detached. Returns {started, reason?, pid?}."""
    if is_discovery_running():
        return {"started": False, "reason": "already_running"}

    _ensure_data_dir()
    proc = subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "run.py"), "--once"],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    logger.info("Discovery subprocess started (pid %s)", proc.pid)
    return {"started": True, "pid": proc.pid}

"""Deployed build identity — used by /api/version to verify OCI is current.

IMPORTANT: `revision` comes from the REVISION file on disk (what was
*deployed*), while `runtime_revision` is captured at import time (what is
actually *running*). If they differ, an old process survived the deploy and
must be restarted — the health watchdog does this automatically.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from app.config import PROJECT_ROOT

REVISION_FILE = PROJECT_ROOT / "REVISION"

BOOT_UNIX = int(time.time())


def git_revision() -> str:
    if REVISION_FILE.exists():
        text = REVISION_FILE.read_text(encoding="utf-8").strip()
        if text:
            return text
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=PROJECT_ROOT,
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _capture_runtime_revision() -> str:
    """Git revision of the code this process imported (frozen at boot)."""
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=PROJECT_ROOT,
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        if REVISION_FILE.exists():
            return REVISION_FILE.read_text(encoding="utf-8").strip() or "unknown"
        return "unknown"


RUNTIME_REVISION = _capture_runtime_revision()


def deploy_info() -> dict:
    return {
        "revision": git_revision(),
        "runtime_revision": RUNTIME_REVISION,
        "boot_unix": BOOT_UNIX,
        "uptime_seconds": int(time.time()) - BOOT_UNIX,
        "project": "CareerCopilotAI",
    }

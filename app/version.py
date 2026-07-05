"""Deployed build identity — used by /api/version to verify OCI is current."""

from __future__ import annotations

import subprocess

from app.config import PROJECT_ROOT


def git_revision() -> str:
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


def deploy_info() -> dict:
    return {
        "revision": git_revision(),
        "project": "CareerCopilotAI",
    }

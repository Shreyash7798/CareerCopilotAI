"""Run scripts/deploy.sh from the deploy webhook (GitHub Actions or manual curl)."""

from __future__ import annotations

import logging
import subprocess

from app.config import PROJECT_ROOT
from app.version import git_revision

logger = logging.getLogger(__name__)

DEPLOY_SCRIPT = PROJECT_ROOT / "scripts" / "deploy.sh"


def run_deploy() -> dict:
    if not DEPLOY_SCRIPT.exists():
        raise FileNotFoundError(f"Deploy script missing: {DEPLOY_SCRIPT}")

    before = git_revision()
    proc = subprocess.run(
        ["bash", str(DEPLOY_SCRIPT)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        logger.error("Deploy script failed (%s): %s", proc.returncode, output[-2000:])
        raise RuntimeError(output[-2000:] or f"deploy.sh exited {proc.returncode}")

    after = git_revision()
    return {
        "status": "ok",
        "revision_before": before,
        "revision_after": after,
        "log_tail": "\n".join(output.strip().splitlines()[-15:]),
    }

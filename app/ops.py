"""Operations helpers: status, backups, admin alerts."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from app.config import PROJECT_ROOT, data_dir, get_settings
from app.db import session_scope
from app.discovery_schedule import discovery_schedule_summary, effective_discovery_interval_minutes
from app.models import ActivityLog, Job, User
from app.users import max_users

logger = logging.getLogger(__name__)

REVISION_FILE = PROJECT_ROOT / "REVISION"


def git_revision() -> str:
    if REVISION_FILE.exists():
        return REVISION_FILE.read_text(encoding="utf-8").strip()
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def environment_label() -> str:
    return os.environ.get("CAREERCOPILOT_ENV", "production")


def memory_stats() -> dict:
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            lines = {k.rstrip(":"): v.split()[0] for k, v in (ln.split(maxsplit=1) for ln in f if ":" in ln)}
        total = int(lines.get("MemTotal", 0)) // 1024
        avail = int(lines.get("MemAvailable", 0)) // 1024
        return {"total_mb": total, "available_mb": avail, "used_mb": max(0, total - avail)}
    except OSError:
        return {"total_mb": 0, "available_mb": 0, "used_mb": 0}


def disk_stats() -> dict:
    try:
        usage = shutil.disk_usage(data_dir())
        return {
            "total_gb": round(usage.total / (1024**3), 1),
            "free_gb": round(usage.free / (1024**3), 1),
        }
    except OSError:
        return {"total_gb": 0, "free_gb": 0}


def last_discovery_log() -> ActivityLog | None:
    with session_scope() as session:
        return session.execute(
            select(ActivityLog)
            .where(ActivityLog.category == "discovery", ActivityLog.message.like("Pipeline run:%"))
            .order_by(ActivityLog.timestamp.desc())
            .limit(1)
        ).scalar_one_or_none()


def tail_file(path: Path, lines: int = 15) -> list[str]:
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return content[-lines:]
    except OSError:
        return []


def get_system_status(session=None) -> dict:
    """Snapshot for admin status page and /api/admin/status."""
    from app.company_sources import enabled_source_count

    with session_scope() as scoped:
        db = session or scoped
        users = db.execute(select(func.count(User.id))).scalar() or 0
        active_users = db.execute(select(func.count(User.id)).where(User.is_active.is_(True))).scalar() or 0
        jobs = db.execute(select(func.count(Job.id)).where(Job.is_active.is_(True))).scalar() or 0
        last_disc = last_discovery_log()
        enabled = enabled_source_count(db)

    mem = memory_stats()
    disk = disk_stats()
    data = data_dir()
    return {
        "revision": git_revision(),
        "environment": environment_label(),
        "port": int((get_settings().get("app", {}) or {}).get("port", 8000)),
        "base_url": str((get_settings().get("app", {}) or {}).get("base_url", "")),
        "users": users,
        "active_users": active_users,
        "max_users": max_users(),
        "active_jobs": jobs,
        "enabled_companies": enabled,
        "discovery_interval_minutes": effective_discovery_interval_minutes(),
        "last_discovery": last_disc.timestamp.isoformat() if last_disc and last_disc.timestamp else None,
        "last_discovery_message": last_disc.message if last_disc else None,
        "memory": mem,
        "disk": disk,
        "watchdog_log": tail_file(data / "watchdog.log"),
        "deploy_log": tail_file(data / "deploy.log"),
        "backup_dir": str(data / "backups"),
    }


def backup_database() -> Path:
    """Copy SQLite DB to data/backups/ with timestamp."""
    settings = get_settings()
    db_url = str(settings.get("app", {}).get("database_url", "sqlite:///data/careercopilot.db"))
    if not db_url.startswith("sqlite:///"):
        raise ValueError("Only SQLite backups are supported in v1")
    rel = db_url.replace("sqlite:///", "")
    src = Path(rel) if Path(rel).is_absolute() else PROJECT_ROOT / rel
    if not src.exists():
        raise FileNotFoundError(f"Database not found: {src}")
    dest_dir = data_dir() / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"careercopilot-{stamp}.db"
    shutil.copy2(src, dest)
    # Keep last 14 backups
    backups = sorted(dest_dir.glob("careercopilot-*.db"), key=lambda p: p.stat().st_mtime)
    for old in backups[:-14]:
        old.unlink(missing_ok=True)
    logger.info("Database backup: %s", dest)
    return dest


def notify_admin_error(message: str) -> None:
    """Best-effort Telegram alert to admin users on production errors."""
    try:
        from app.notifications import deliver_to_user, _telegram_configured
        from app.users import ROLE_ADMIN

        if not _telegram_configured():
            return
        with session_scope() as session:
            admins = session.execute(select(User).where(User.role == ROLE_ADMIN, User.is_active.is_(True))).scalars()
            for admin in admins:
                deliver_to_user(admin, "CareerCopilot alert", message[:3500], email_only=False)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to notify admin of error")

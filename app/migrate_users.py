"""One-time migration from single-user install to multi-user accounts."""

from __future__ import annotations

import json
import logging

from sqlalchemy import select

from app.config import get_profile, get_settings
from app.db import session_scope
from app.models import (
    ActivityLog,
    Application,
    Company,
    InterviewPrep,
    Job,
    Resume,
    User,
    UserCompanyMonitor,
    UserJobScore,
    UserProfile,
)
from app.user_prefs import migrate_legacy_cv_to_user
from app.users import ROLE_ADMIN, create_user, hash_password, max_users, user_count

logger = logging.getLogger(__name__)


def bootstrap_users_if_needed() -> None:
    """Create admin from legacy data when no users exist yet."""
    if user_count() > 0:
        return

    settings = get_settings()
    app_cfg = settings.get("app", {}) or {}
    admin_email = str(app_cfg.get("admin_email") or "admin@careercopilot.local").lower()
    legacy_password = str(app_cfg.get("auth_password") or "")
    if not legacy_password:
        legacy_password = "changeme123"

    with session_scope() as session:
        legacy_profile = session.execute(select(UserProfile)).scalars().first()
        profile_data = get_profile()
        if legacy_profile and legacy_profile.profile_json:
            try:
                parsed = json.loads(legacy_profile.profile_json)
                if isinstance(parsed, dict):
                    profile_data = {**profile_data, **parsed}
            except json.JSONDecodeError:
                pass

        preferences = {
            "profile": profile_data,
            "scoring": settings.get("scoring", {}) or {},
            "notifications": settings.get("notifications", {}) or {},
        }

        admin = User(
            email=admin_email,
            display_name=profile_data.get("full_name") or "Admin",
            password_hash=hash_password(legacy_password),
            role=ROLE_ADMIN,
            cv_path=legacy_profile.cv_path if legacy_profile else None,
            profile_json=legacy_profile.profile_json if legacy_profile else None,
            preferences_json=json.dumps(preferences),
        )
        session.add(admin)
        session.flush()
        admin_id = admin.id

        migrate_legacy_cv_to_user(admin_id, admin.cv_path)

        for company in session.execute(select(Company).where(Company.ats_type.isnot(None))).scalars():
            if company.enabled:
                session.add(
                    UserCompanyMonitor(
                        user_id=admin_id,
                        company_id=company.id,
                        enabled=True,
                        keywords=company.keywords,
                        refresh_interval_minutes=company.refresh_interval_minutes,
                        priority=company.priority,
                        recruiter_search_enabled=company.recruiter_search_enabled,
                        last_run_at=company.last_run_at,
                        last_run_status=company.last_run_status,
                    )
                )

        for job in session.execute(select(Job)).scalars():
            session.add(
                UserJobScore(
                    user_id=admin_id,
                    job_id=job.id,
                    match_score=job.match_score,
                    score_breakdown=job.score_breakdown,
                    jd_fit_score=getattr(job, "jd_fit_score", 0.0) or 0.0,
                    jd_fit_breakdown=getattr(job, "jd_fit_breakdown", None),
                    is_high_priority=job.is_high_priority,
                    notified_at=job.notified_at,
                )
            )

        for app_row in session.execute(select(Application)).scalars():
            app_row.user_id = admin_id
        for resume in session.execute(select(Resume)).scalars():
            resume.user_id = admin_id
        for prep in session.execute(select(InterviewPrep)).scalars():
            prep.user_id = admin_id
        for log in session.execute(select(ActivityLog)).scalars():
            if log.user_id is None:
                log.user_id = admin_id

    logger.info(
        "Bootstrapped admin user %s (max %d users). Change password after first login.",
        admin_email,
        max_users(),
    )

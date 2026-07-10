"""Per-user preferences, CV paths, and private data directories."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT, data_dir, get_profile, get_settings
from app.db import session_scope
from app.models import User


def user_data_dir(user_id: int) -> Path:
    path = data_dir() / "users" / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_cv_dir(user_id: int) -> Path:
    path = user_data_dir(user_id) / "cv"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_resumes_dir(user_id: int) -> Path:
    path = user_data_dir(user_id) / "resumes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _default_preferences() -> dict[str, Any]:
    settings = get_settings()
    profile = dict(get_profile())
    scoring = dict(settings.get("scoring", {}) or {})
    return {
        "profile": profile,
        "scoring": scoring,
        "notifications": dict(settings.get("notifications", {}) or {}),
    }


def get_user_preferences(user: User) -> dict[str, Any]:
    if user.preferences_json:
        try:
            data = json.loads(user.preferences_json)
            if isinstance(data, dict):
                base = _default_preferences()
                base["profile"] = {**base.get("profile", {}), **(data.get("profile") or {})}
                if data.get("scoring"):
                    base["scoring"] = data["scoring"]
                if data.get("notifications"):
                    base["notifications"] = data["notifications"]
                return base
        except json.JSONDecodeError:
            pass
    return _default_preferences()


def save_user_preferences(user_id: int, preferences: dict[str, Any]) -> None:
    with session_scope() as session:
        user = session.get(User, user_id)
        if user is None:
            raise ValueError("User not found")
        user.preferences_json = json.dumps(preferences)


def get_user_profile_dict(user: User) -> dict[str, Any]:
    prefs = get_user_preferences(user)
    profile = dict(prefs.get("profile") or {})
    if user.profile_json:
        try:
            parsed = json.loads(user.profile_json)
            if isinstance(parsed, dict):
                if parsed.get("skills"):
                    profile["skills"] = list(
                        dict.fromkeys((profile.get("skills") or []) + parsed["skills"])
                    )
                if parsed.get("employers"):
                    profile["employers"] = list(
                        dict.fromkeys((profile.get("employers") or []) + parsed["employers"])
                    )
                for key in ("full_name", "email", "phone", "experience_years", "raw_text"):
                    if parsed.get(key) and not profile.get(key):
                        profile[key] = parsed[key]
        except json.JSONDecodeError:
            pass
    if user.display_name and not profile.get("full_name"):
        profile["full_name"] = user.display_name
    if user.email and not profile.get("email"):
        profile["email"] = user.email
    return profile


def scoring_profile_for_user(user: User) -> dict[str, Any]:
    return get_user_profile_dict(user)


def scoring_config_for_user(user: User) -> dict[str, Any]:
    return get_user_preferences(user).get("scoring") or {}


def migrate_legacy_cv_to_user(user_id: int, legacy_path: str | None) -> None:
    if not legacy_path:
        return
    src = Path(legacy_path)
    if not src.is_absolute():
        src = PROJECT_ROOT / legacy_path
    if not src.exists():
        return
    dest = user_cv_dir(user_id) / src.name
    if not dest.exists():
        shutil.copy2(src, dest)

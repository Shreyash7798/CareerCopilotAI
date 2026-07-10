"""Multi-user accounts (max 10): passwords, sessions, admin user management."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.config import get_settings
from app.db import session_scope
from app.models import User, log_activity

MAX_USERS = 10
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
COOKIE_NAME = "careercopilot_session"


def max_users() -> int:
    return int((get_settings().get("app", {}) or {}).get("max_users") or MAX_USERS)


def session_secret() -> str:
    secret = str((get_settings(refresh=True).get("app", {}) or {}).get("session_secret") or "")
    if not secret:
        secret = str((get_settings().get("app", {}) or {}).get("auth_password") or "") or "careercopilot-dev"
    return secret


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, digest = stored_hash.split("$", 1)
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
    return hmac.compare_digest(check.hex(), digest)


def create_session_token(user_id: int) -> str:
    expires = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())
    payload = f"{user_id}:{expires}"
    sig = hmac.new(session_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def parse_session_token(token: str) -> int | None:
    if not token or token.count(":") != 2:
        return None
    user_part, expires_part, sig = token.split(":", 2)
    payload = f"{user_part}:{expires_part}"
    expected = hmac.new(session_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        user_id = int(user_part)
        expires = int(expires_part)
    except ValueError:
        return None
    if datetime.now(timezone.utc).timestamp() > expires:
        return None
    return user_id


def get_user_by_id(user_id: int) -> User | None:
    with session_scope() as session:
        user = session.get(User, user_id)
        if user is None or not user.is_active:
            return None
        session.expunge(user)
        return user


def get_user_by_email(email: str) -> User | None:
    with session_scope() as session:
        user = session.execute(select(User).where(User.email == email.lower().strip())).scalar_one_or_none()
        if user is None:
            return None
        session.expunge(user)
        return user


def authenticate(email: str, password: str) -> User | None:
    user = get_user_by_email(email)
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    with session_scope() as session:
        db_user = session.get(User, user.id)
        if db_user is not None:
            db_user.last_login_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return user


def user_count() -> int:
    with session_scope() as session:
        return session.execute(select(func.count(User.id))).scalar() or 0


def list_users() -> list[dict]:
    with session_scope() as session:
        rows = session.execute(select(User).order_by(User.id)).scalars().all()
        return [
            {
                "id": u.id,
                "email": u.email,
                "display_name": u.display_name,
                "role": u.role,
                "is_active": u.is_active,
                "has_cv": bool(u.cv_path),
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            }
            for u in rows
        ]


def create_user(
    *,
    email: str,
    password: str,
    display_name: str = "",
    role: str = ROLE_MEMBER,
    actor_user_id: int | None = None,
) -> User:
    email = email.lower().strip()
    if not email or "@" not in email:
        raise ValueError("A valid email is required")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    if role not in (ROLE_ADMIN, ROLE_MEMBER):
        raise ValueError("Invalid role")
    if user_count() >= max_users():
        raise ValueError(f"User limit reached ({max_users()} accounts maximum)")

    with session_scope() as session:
        if session.execute(select(User).where(User.email == email)).scalar_one_or_none():
            raise ValueError("Email already registered")
        user = User(
            email=email,
            display_name=display_name.strip() or email.split("@")[0],
            password_hash=hash_password(password),
            role=role,
            preferences_json=json.dumps({}),
        )
        session.add(user)
        session.flush()
        log_activity(
            session,
            "admin",
            f"User account created: {email} ({role})",
            user_id=actor_user_id,
        )
        session.expunge(user)
        return user


def set_user_password(user_id: int, new_password: str, *, actor_user_id: int | None = None) -> None:
    if len(new_password) < 8:
        raise ValueError("Password must be at least 8 characters")
    with session_scope() as session:
        user = session.get(User, user_id)
        if user is None:
            raise ValueError("User not found")
        user.password_hash = hash_password(new_password)
        log_activity(
            session,
            "admin",
            f"Password reset for {user.email}",
            user_id=actor_user_id,
        )


def set_user_active(user_id: int, active: bool, *, actor_user_id: int | None = None) -> None:
    with session_scope() as session:
        user = session.get(User, user_id)
        if user is None:
            raise ValueError("User not found")
        if user.role == ROLE_ADMIN and not active:
            admins = session.execute(
                select(func.count(User.id)).where(User.role == ROLE_ADMIN, User.is_active.is_(True))
            ).scalar()
            if admins <= 1:
                raise ValueError("Cannot deactivate the only admin account")
        user.is_active = active
        log_activity(
            session,
            "admin",
            f"User {'enabled' if active else 'disabled'}: {user.email}",
            user_id=actor_user_id,
        )

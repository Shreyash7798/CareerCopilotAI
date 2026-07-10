"""FastAPI dependencies for authenticated requests."""

from __future__ import annotations

from fastapi import HTTPException, Request

from app.models import User
from app.users import ROLE_ADMIN


def get_current_user(request: Request) -> User:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(request: Request) -> User:
    user = get_current_user(request)
    if user.role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

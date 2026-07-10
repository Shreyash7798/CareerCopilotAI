"""Multi-user session authentication.

Each account has its own email/password (PBKDF2). Sessions are signed HMAC
tokens in an HttpOnly cookie. Admin accounts can manage users but cannot read
other users' private CV, applications, or scores through the dashboard.

When no users exist yet, init_db bootstraps an admin from legacy settings.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.users import COOKIE_NAME, get_user_by_id, parse_session_token, user_count

# Paths reachable without a session (login flow + static assets + health probes).
PUBLIC_PREFIXES = (
    "/login",
    "/static/",
    "/api/deploy/hook",
    "/api/version",
    "/api/health",
    "/api/crawl4ai/health",
)


def auth_required() -> bool:
    """Login is required once at least one user account exists."""
    return user_count() > 0


def is_valid_session(request: Request) -> int | None:
    cookie = request.cookies.get(COOKIE_NAME, "")
    return parse_session_token(cookie)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path == p or path.startswith(p) for p in PUBLIC_PREFIXES):
            return await call_next(request)

        if not auth_required():
            return await call_next(request)

        user_id = is_valid_session(request)
        if user_id is not None:
            user = get_user_by_id(user_id)
            if user is not None:
                request.state.user = user
                request.state.user_id = user.id
                return await call_next(request)

        if path.startswith("/api/"):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return RedirectResponse(f"/login?next={path}", status_code=303)

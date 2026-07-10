"""Multi-user session authentication.

Each account has its own email/password (PBKDF2). Sessions are signed HMAC
tokens in an HttpOnly cookie. Admin accounts can manage users but cannot read
other users' private CV, applications, or scores through the dashboard.

When no users exist yet, init_db bootstraps an admin from legacy settings.
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.exc import OperationalError
from starlette.middleware.base import BaseHTTPMiddleware

from app.users import COOKIE_NAME, get_user_by_id, parse_session_token, user_count

logger = logging.getLogger(__name__)

_BUSY_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>One moment · CareerCopilot</title>
<link rel="stylesheet" href="/static/style.css"></head>
<body><main class="content" style="max-width:480px;margin-top:14vh;text-align:center">
<div class="card" style="padding:28px 20px">
<h1 style="margin:0 0 10px">One moment…</h1>
<p class="muted">The server is busy finishing a discovery run.<br>
This page retries automatically in a few seconds.</p>
</div></main></body></html>"""

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


def _busy_response(path: str):
    if path.startswith("/api/"):
        return JSONResponse(
            {"detail": "Server busy (discovery running) — retry in a few seconds"},
            status_code=503,
            headers={"Retry-After": "5"},
        )
    return HTMLResponse(_BUSY_HTML, status_code=503, headers={"Retry-After": "5"})


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path == p or path.startswith(p) for p in PUBLIC_PREFIXES):
            try:
                return await call_next(request)
            except OperationalError:
                logger.warning("SQLite busy on public path %s", path)
                return _busy_response(path)

        try:
            if not auth_required():
                return await call_next(request)

            user_id = is_valid_session(request)
            if user_id is not None:
                user = get_user_by_id(user_id)
                if user is not None:
                    request.state.user = user
                    request.state.user_id = user.id
                    return await call_next(request)
        except OperationalError:
            # SQLite locked by the discovery subprocess — show a retry page
            # instead of a plain Internal Server Error.
            logger.warning("SQLite busy during request %s", path)
            return _busy_response(path)

        if path.startswith("/api/"):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return RedirectResponse(f"/login?next={path}", status_code=303)

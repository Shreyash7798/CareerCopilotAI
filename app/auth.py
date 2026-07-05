"""Optional single-password login.

The dashboard is designed to be private (local network / VPN), but the
reference deployment exposes it on a public IP — so a lightweight gate is
needed. Set `app.auth_password` in config/settings.yaml to require login;
leave it empty to keep the local-first, no-login behaviour.

Sessions are a signed HMAC token in an HttpOnly cookie. The token is derived
from the password, so changing the password invalidates existing sessions.
"""

from __future__ import annotations

import hashlib
import hmac

from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings

COOKIE_NAME = "careercopilot_session"
# Paths reachable without a session (login flow + static assets + PWA files).
PUBLIC_PREFIXES = ("/login", "/static/",)


def configured_password() -> str:
    return str((get_settings(refresh=True).get("app", {}) or {}).get("auth_password", "") or "")


def session_token(password: str) -> str:
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return hmac.new(digest, b"careercopilot-session-v1", hashlib.sha256).hexdigest()


def is_valid_session(request: Request, password: str) -> bool:
    cookie = request.cookies.get(COOKIE_NAME, "")
    return bool(cookie) and hmac.compare_digest(cookie, session_token(password))


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        password = configured_password()
        if not password:  # auth disabled: local-first default
            return await call_next(request)
        path = request.url.path
        if any(path == p or path.startswith(p) for p in PUBLIC_PREFIXES):
            return await call_next(request)
        if is_valid_session(request, password):
            return await call_next(request)
        if path.startswith("/api/"):
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return RedirectResponse(f"/login?next={path}", status_code=303)

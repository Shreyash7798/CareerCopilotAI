"""Outermost catch-all middleware.

Anything that escapes routes, exception handlers, or inner middleware
(including template render errors and auth middleware crashes) lands here.
We log the full traceback to data/errors.log, remember a short summary for
/api/health, and return a friendly page instead of Starlette's plain
"Internal Server Error".
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone

from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

_last_error: dict | None = None

_FRIENDLY_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Something went wrong · CareerCopilot</title>
<link rel="stylesheet" href="/static/style.css"></head>
<body><main class="content" style="max-width:520px;margin-top:12vh;text-align:center">
<div class="card" style="padding:28px 20px">
<p class="dash-eyebrow">Error 500</p>
<h1 style="margin:8px 0 12px">Something went wrong</h1>
<p class="muted">The server hit an unexpected error. It has been logged for the admin.</p>
<div style="margin-top:20px;display:flex;gap:10px;justify-content:center;flex-wrap:wrap">
<a class="btn btn-accent" href="/">Dashboard</a>
<a class="btn" href="/jobs">Jobs</a>
<a class="btn" href="/login">Sign in</a>
</div></div></main></body></html>"""


def record_error(exc: Exception, path: str) -> None:
    global _last_error
    _last_error = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "path": path,
        "type": type(exc).__name__,
        "message": str(exc)[:300],
    }
    try:
        from app.config import data_dir

        log_path = data_dir() / "errors.log"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n=== {_last_error['time']} {path} ===\n")
            f.write(traceback.format_exc())
    except Exception:  # noqa: BLE001 — logging must never crash the shield
        pass


def get_last_error() -> dict | None:
    return _last_error


class ErrorShieldMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:  # noqa: BLE001 — last line of defence
            path = request.url.path
            logger.error("ErrorShield caught %s on %s", type(exc).__name__, path)
            record_error(exc, path)
            if path.startswith("/api/"):
                return JSONResponse(
                    {"detail": "Internal server error", "type": type(exc).__name__},
                    status_code=500,
                )
            return HTMLResponse(_FRIENDLY_HTML, status_code=500)

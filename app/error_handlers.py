"""Friendly HTML error pages and admin alerts."""

from __future__ import annotations

import logging
import traceback

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

logger = logging.getLogger(__name__)
_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


async def not_found_handler(request: Request, exc):
    status = getattr(exc, "status_code", 404)
    detail = getattr(exc, "detail", "Not found")
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": detail}, status_code=status)
    if status == 401:
        from fastapi.responses import RedirectResponse

        return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
    if status == 404:
        return _templates.TemplateResponse(
            request,
            "error.html",
            {
                "code": 404,
                "title": "Page not found",
                "message": "That page does not exist. Try the dashboard or Help.",
            },
            status_code=404,
        )
    message = str(detail) if detail else "Something went wrong."
    return _templates.TemplateResponse(
        request,
        "error.html",
        {
            "code": status,
            "title": f"Error {status}",
            "message": message,
        },
        status_code=status,
    )


async def server_error_handler(request: Request, exc: Exception):
    tb = traceback.format_exc()
    logger.error("Unhandled error on %s %s\n%s", request.method, request.url.path, tb)
    try:
        from app.ops import notify_admin_error

        notify_admin_error(f"500 on {request.method} {request.url.path}\n{type(exc).__name__}: {exc}")
    except Exception:  # noqa: BLE001
        pass
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Internal server error"}, status_code=500)
    return _templates.TemplateResponse(
        request,
        "error.html",
        {
            "code": 500,
            "title": "Something went wrong",
            "message": "The server hit an unexpected error. Please try again in a moment, or contact your admin if this persists.",
        },
        status_code=500,
    )

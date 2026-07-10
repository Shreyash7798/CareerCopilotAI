"""CareerCopilot AI — FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth import AuthMiddleware
from app.db import init_db
from app.error_handlers import not_found_handler, server_error_handler
from app.routers import api, pages
from app.scheduler import start_scheduler, stop_scheduler
from app.startup import schedule_startup_tasks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    start_scheduler()
    schedule_startup_tasks()
    yield
    stop_scheduler()


def create_app() -> FastAPI:
    app = FastAPI(
        title="CareerCopilot AI",
        version="1.0",
        description="Personal career intelligence and automation platform.",
        lifespan=lifespan,
    )
    app.add_middleware(AuthMiddleware)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.add_exception_handler(StarletteHTTPException, not_found_handler)
    app.add_exception_handler(Exception, server_error_handler)
    app.include_router(api.router)
    app.include_router(pages.router)
    return app


app = create_app()

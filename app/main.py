"""CareerCopilot AI — FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.auth import AuthMiddleware
from app.db import init_db
from app.routers import api, pages
from app.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    start_scheduler()
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
    app.include_router(api.router)
    app.include_router(pages.router)
    return app


app = create_app()

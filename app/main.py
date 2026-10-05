"""OpsIntel — Smart Operations Data Intelligence Platform.

FastAPI application factory: wires config, logging, error handling and routers.
Run locally:  uvicorn app.main:app --reload
"""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.config import get_settings
from app.db import init_db
from app.errors import register_error_handlers
from app.logging_config import request_id_var, setup_logging
from app.profile import load_profile
from app.routers import health

log = logging.getLogger("opsintel")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    load_profile(settings.dataset_profile)  # fail fast if the data contract is broken
    init_db()
    log.info("OpsIntel started (storage=%s, profile=%s)",
             settings.storage_backend, settings.dataset_profile)
    yield
    log.info("OpsIntel stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="OpsIntel API",
        description="Upload operational CSV data, validate and clean it, and query insights.",
        version="0.1.0",
        lifespan=lifespan,
    )
    register_error_handlers(app)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        token = request_id_var.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
            elapsed = (time.perf_counter() - start) * 1000
            response.headers["X-Request-ID"] = rid
            if request.url.path.startswith("/api/"):
                log.info("%s %s -> %s (%.0f ms)", request.method, request.url.path,
                         response.status_code, elapsed)
            return response
        finally:
            request_id_var.reset(token)

    app.include_router(health.router)
    return app


app = create_app()

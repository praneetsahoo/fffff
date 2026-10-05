"""Health check: proves the app can reach both the database and object storage."""
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db import ping_db
from app.storage import get_storage

router = APIRouter(tags=["health"])
log = logging.getLogger(__name__)


@router.get("/api/health")
def health() -> JSONResponse:
    checks: dict[str, str] = {}

    try:
        ping_db()
        checks["database"] = "ok"
    except (SQLAlchemyError, RuntimeError) as exc:
        log.error("Health: database check failed (%s)", exc.__class__.__name__)
        checks["database"] = "unavailable"

    storage = get_storage()
    checks["storage"] = "ok" if storage.health() else "unavailable"

    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "degraded",
            "checks": checks,
            "storage_backend": storage.name,
            "dataset_profile": get_settings().dataset_profile,
        },
    )

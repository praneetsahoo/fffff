"""One predictable error format for the whole API.

Every failure returns:
    {"error": {"code": "...", "message": "human readable", "details": {...}}}
so the frontend can always show a meaningful message instead of a blank page.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


class AppError(Exception):
    """Base error carrying an HTTP status and a stable machine-readable code."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, details: dict | None = None,
                 status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code


class InvalidFileError(AppError):
    status_code = 422
    code = "invalid_file"


class DuplicateUploadError(AppError):
    status_code = 409
    code = "duplicate_upload"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class StorageError(AppError):
    status_code = 503
    code = "storage_unavailable"


def _body(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        log.warning("AppError %s: %s", exc.code, exc.message)
        return JSONResponse(status_code=exc.status_code,
                            content=_body(exc.code, exc.message, exc.details))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=_body("validation_error", "The request is invalid.",
                          {"errors": [{"field": ".".join(map(str, e["loc"])), "msg": e["msg"]}
                                      for e in exc.errors()]}),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = "not_found" if exc.status_code == 404 else "http_error"
        return JSONResponse(status_code=exc.status_code,
                            content=_body(code, str(exc.detail)))

    @app.exception_handler(OperationalError)
    async def _db_down(_: Request, exc: OperationalError):
        log.error("Database unavailable: %s", exc.__class__.__name__)
        return JSONResponse(
            status_code=503,
            content=_body("database_unavailable",
                          "The database is temporarily unavailable. Please try again shortly."),
        )

    @app.exception_handler(SQLAlchemyError)
    async def _db_error(_: Request, exc: SQLAlchemyError):
        log.exception("Database error")
        return JSONResponse(status_code=500,
                            content=_body("database_error", "A database error occurred."))

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):
        log.exception("Unhandled error")
        return JSONResponse(status_code=500,
                            content=_body("internal_error",
                                          "Something went wrong on our side. The error has been logged."))

"""Database engine and session management (SQLAlchemy 2.0).

The same ORM models run on SQLite (local dev/tests) and MySQL on AWS RDS (production).
"""
from __future__ import annotations

import logging
from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        url = get_settings().database_url()
        kwargs: dict = {"pool_pre_ping": True, "future": True}
        if str(url).startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            # RDS closes idle connections; recycle before that happens.
            kwargs.update(pool_recycle=1800, pool_size=5, max_overflow=5)
        _engine = create_engine(url, **kwargs)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
        log.info("Database engine created (dialect=%s)", _engine.dialect.name)
    return _engine


def reset_engine() -> None:
    """Used by tests to point the app at a fresh database."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    get_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()


def new_session() -> Session:
    """Session for background jobs (outside a request)."""
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


def init_db() -> None:
    """Create tables if they do not exist (idempotent)."""
    from app import models  # noqa: F401  (registers models on Base.metadata)

    Base.metadata.create_all(get_engine())
    log.info("Database schema ready")


def ping_db() -> bool:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    return True

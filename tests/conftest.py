from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """Isolated config: fresh SQLite DB + temp local storage per test."""
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.delenv("DB_HOST", raising=False)

    from app.config import get_settings
    from app.db import reset_engine
    from app.storage import get_storage

    get_settings.cache_clear()
    get_storage.cache_clear()
    reset_engine()
    yield tmp_path
    reset_engine()
    get_settings.cache_clear()
    get_storage.cache_clear()


MYSQL_URL = os.environ.get("TEST_MYSQL_URL")  # e.g. mysql+pymysql://user:pw@127.0.0.1/opsintel_test


def _drop_all(url: str) -> None:
    from sqlalchemy import create_engine

    import app.models  # noqa: F401
    from app.db import Base

    eng = create_engine(url)
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture(params=["sqlite", "mysql"])
def db_env(request, tmp_path, monkeypatch):
    """Runs a test on SQLite and (when TEST_MYSQL_URL is set) on real MySQL/MariaDB.
    Background jobs run inline (JOB_MODE=sync) so results are deterministic."""
    if request.param == "mysql":
        if not MYSQL_URL:
            pytest.skip("TEST_MYSQL_URL not set")
        url = MYSQL_URL
        _drop_all(url)
    else:
        url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DB_URL", url)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("JOB_MODE", "sync")
    monkeypatch.delenv("DB_HOST", raising=False)

    from app.config import get_settings
    from app.db import init_db, reset_engine
    from app.storage import get_storage

    get_settings.cache_clear()
    get_storage.cache_clear()
    reset_engine()
    init_db()
    yield request.param
    reset_engine()
    if request.param == "mysql":
        _drop_all(url)
    get_settings.cache_clear()
    get_storage.cache_clear()


@pytest.fixture
def client(app_env):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c

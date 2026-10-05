from __future__ import annotations

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


@pytest.fixture
def client(app_env):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c

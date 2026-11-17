"""Each test gets its own SQLite database and in-memory job store (no Redis, no network)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("APP_IMPORT_LEGACY", "0")
    for k in ("REDIS_URL", "APP_API_TOKEN", "APP_SECRET_KEY", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    from backend.app import config, db, jobs

    config.reset_settings_cache()
    db.reset_engine()
    jobs.reset_store()
    from backend.app.main import create_app

    with TestClient(create_app()) as c:
        yield c
    db.reset_engine()
    jobs.reset_store()
    config.reset_settings_cache()


@pytest.fixture()
def configured(client):
    """AI settings pointing at a cloud model with a key (no request is ever sent in tests)."""
    r = client.put("/api/v1/settings/ai", json={"provider": "deepseek", "base_url": "https://api.deepseek.com/v1",
                                                 "model": "deepseek-flash", "api_key": "sk-test-1234567890abcd"})
    assert r.status_code == 200, r.text
    return client

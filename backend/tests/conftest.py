"""Every test runs against PostgreSQL (decision D3), the same database as production.

One throwaway database is created per test session and emptied after each test. The server
comes from TEST_DATABASE_URL, or the local dev PostgreSQL started with
``docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db``.
No Redis and no network: jobs run in-process and the engine is faked where a test needs it.
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

DEFAULT_SERVER = "postgresql+psycopg://valuation:change-me@localhost:5432/valuation"


@pytest.fixture(scope="session")
def pg_url():
    server = make_url(os.environ.get("TEST_DATABASE_URL") or DEFAULT_SERVER)
    name = f"sv_test_{uuid.uuid4().hex[:10]}"
    admin = create_engine(server.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as c:
            c.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # say how to fix it instead of failing every test with a traceback
        pytest.exit(f"後端測試需要 PostgreSQL（{server.render_as_string(hide_password=True)}）：{exc}\n"
                    "先執行 docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db，"
                    "或設定 TEST_DATABASE_URL。", returncode=2)
    yield server.set(database=name).render_as_string(hide_password=False)
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _empty_all_tables(url: str) -> None:
    eng = create_engine(url)
    with eng.begin() as c:
        tables = [r[0] for r in c.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'"))]
        if tables:
            c.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))
    eng.dispose()


@pytest.fixture()
def client(tmp_path, monkeypatch, pg_url):
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("APP_IMPORT_LEGACY", "0")
    for k in ("REDIS_URL", "APP_API_TOKEN", "APP_SECRET_KEY", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    from backend.app import config, db, jobs

    config.reset_settings_cache()
    db.reset_engine()
    jobs.reset_store()
    from backend.app.main import create_app

    try:
        with TestClient(create_app()) as c:
            yield c
    finally:
        db.reset_engine()
        jobs.reset_store()
        config.reset_settings_cache()
        _empty_all_tables(pg_url)


@pytest.fixture()
def configured(client):
    """AI settings pointing at a cloud model with a key (no request is ever sent in tests)."""
    r = client.put("/api/v1/settings/ai", json={"provider": "deepseek", "base_url": "https://api.deepseek.com/v1",
                                                 "model": "deepseek-flash", "api_key": "sk-test-1234567890abcd"})
    assert r.status_code == 200, r.text
    return client

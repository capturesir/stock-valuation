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
ADMIN = ("admin@example.com", "admin-pass-123")
MEMBER = ("member@example.com", "member-pass-123")


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


@pytest.fixture(autouse=True)
def fake_market(monkeypatch):
    """No network in tests: market data comes from a deterministic fake source, synced inline."""
    from backend.app.services import market, market_jobs
    from market_fake import FakeSource

    src = FakeSource()
    old = market.get_source()
    market.set_source(src)
    monkeypatch.setattr(market_jobs, "INLINE", True)
    yield src
    market.set_source(old)


@pytest.fixture()
def client(tmp_path, monkeypatch, pg_url):
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("APP_IMPORT_LEGACY", "0")
    monkeypatch.setenv("APP_MARKET_SCHEDULER", "0")
    for k in ("REDIS_URL", "APP_API_TOKEN", "APP_SECRET_KEY", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    from backend.app import config, db, jobs

    config.reset_settings_cache()
    db.reset_engine()
    jobs.reset_store()
    from backend.app.main import create_app

    try:
        with TestClient(create_app()) as c:
            from backend.app.auth import create_user
            from backend.app.db import session
            with session() as s:
                create_user(s, email=ADMIN[0], display_name="管理員", password=ADMIN[1], role="admin")
            r = c.post("/api/v1/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]})
            assert r.status_code == 200, r.text
            yield c
    finally:
        db.reset_engine()
        jobs.reset_store()
        config.reset_settings_cache()
        _empty_all_tables(pg_url)


@pytest.fixture()
def client_as(client):
    """Another signed-in browser: ``client_as(email, password)`` returns a fresh TestClient."""
    made = []

    def _make(email: str | None = None, password: str | None = None) -> TestClient:
        c = TestClient(client.app)
        if email:
            r = c.post("/api/v1/auth/login", json={"email": email, "password": password})
            assert r.status_code == 200, r.text
        made.append(c)
        return c
    yield _make
    for c in made:
        c.close()


@pytest.fixture()
def member(client, client_as):
    """A signed-in member (non-admin) alongside the admin ``client``."""
    r = client.post("/api/v1/admin/users", json={"email": MEMBER[0], "display_name": "用戶", "password": MEMBER[1]})
    assert r.status_code == 201, r.text
    return client_as(*MEMBER)


@pytest.fixture()
def configured(client):
    """AI settings pointing at a cloud model with a key (no request is ever sent in tests)."""
    r = client.put("/api/v1/settings/ai", json={"provider": "deepseek", "base_url": "https://api.deepseek.com/v1",
                                                 "model": "deepseek-flash", "api_key": "sk-test-1234567890abcd"})
    assert r.status_code == 200, r.text
    return client

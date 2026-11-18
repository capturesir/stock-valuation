"""Every Alembic migration must apply from scratch, roll back to empty and apply again on PostgreSQL."""
from __future__ import annotations

import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url


def test_upgrade_downgrade_upgrade(pg_url):
    server = make_url(pg_url)
    name = f"sv_mig_{uuid.uuid4().hex[:8]}"
    admin = create_engine(server.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = server.set(database=name).render_as_string(hide_password=False)
    try:
        here = Path(__file__).resolve().parents[1]
        cfg = Config(str(here / "alembic.ini"))
        cfg.set_main_option("script_location", str(here / "alembic"))
        cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(cfg, "head")
        eng = create_engine(url)
        tables = set(inspect(eng).get_table_names())
        assert {"runs", "users", "securities", "price_bars"} <= tables
        command.downgrade(cfg, "base")
        assert set(inspect(eng).get_table_names()) <= {"alembic_version"}
        command.upgrade(cfg, "head")
        assert set(inspect(eng).get_table_names()) == tables
        eng.dispose()
    finally:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()

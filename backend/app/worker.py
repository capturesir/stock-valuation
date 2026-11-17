"""arq worker: `arq backend.app.worker.WorkerSettings` (Docker service "worker").

One job at a time (max_jobs=1): the v1 engine writes to shared folders and module
tables, so valuations must not overlap. Scale by adding queues per tenant later,
not by raising max_jobs.
"""
from __future__ import annotations

import asyncio

from arq import cron
from arq.connections import RedisSettings

from .config import get_settings
from .db import init_db


async def run_valuation(ctx, job_id: str, payload: dict) -> None:
    from .services.valuation import execute
    await asyncio.to_thread(execute, job_id, payload)


async def market_update(ctx) -> None:
    from .scheduler import run_once
    await asyncio.to_thread(run_once)


async def market_update_now(ctx) -> None:
    await market_update(ctx)


async def startup(ctx) -> None:
    init_db()


def _cron_jobs() -> list:
    from .scheduler import enabled, utc_cron
    if not enabled():
        return []
    return [cron(market_update, hour={h}, minute={m}, run_at_startup=False) for h, m in utc_cron()]


class WorkerSettings:
    functions = [run_valuation, market_update_now]
    cron_jobs = _cron_jobs()
    on_startup = startup
    max_jobs = 1
    job_timeout = 15 * 60
    keep_result = 3600
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url or "redis://localhost:6379/0")

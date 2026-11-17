"""arq worker: `arq backend.app.worker.WorkerSettings` (Docker service "worker").

One job at a time (max_jobs=1): the v1 engine writes to shared folders and module
tables, so valuations must not overlap. Scale by adding queues per tenant later,
not by raising max_jobs.
"""
from __future__ import annotations

import asyncio

from arq.connections import RedisSettings

from .config import get_settings
from .db import init_db


async def run_valuation(ctx, job_id: str, payload: dict) -> None:
    from .services.valuation import execute
    await asyncio.to_thread(execute, job_id, payload)


async def startup(ctx) -> None:
    init_db()


class WorkerSettings:
    functions = [run_valuation]
    on_startup = startup
    max_jobs = 1
    job_timeout = 15 * 60
    keep_result = 3600
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url or "redis://localhost:6379/0")

"""Job state and queue.

With REDIS_URL set (Docker): state lives in Redis, jobs go to the arq worker, and
the API and worker can be separate containers. Without it (local dev, tests):
state lives in memory and jobs run in a background thread of the API process.

Valuations run one at a time in both modes: the v1 engine writes to shared
folders and module-level tables, so concurrent runs are not safe.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import threading
import uuid
from typing import Any, Optional

from .config import get_settings

TTL_SECONDS = 2 * 24 * 3600
ACTIVE = ("queued", "running")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def new_job(ticker: str, mode: str, valuation_date: str, tasks: list[dict], steps: list[dict],
            estimate: dict, budget_usd: Optional[float], note: Optional[str] = None) -> dict:
    return {
        "id": uuid.uuid4().hex, "ticker": ticker, "mode": mode, "valuation_date": valuation_date,
        "status": "queued", "created_at": now_iso(), "started_at": None, "finished_at": None,
        "progress": 0, "activity": "排隊中…", "steps": steps, "ai_tasks": tasks,
        "estimate": estimate, "budget_usd": budget_usd, "cost_usd": 0.0,
        "tokens_in": 0, "tokens_out": 0, "run_id": None, "result": None, "error": None,
        "note": note, "version": 1,
    }


class MemoryStore:
    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._cancel: set[str] = set()
        self._lock = threading.Lock()

    def put(self, job: dict) -> None:
        with self._lock:
            self._jobs[job["id"]] = copy.deepcopy(job)

    def get(self, job_id: str) -> Optional[dict]:
        with self._lock:
            j = self._jobs.get(job_id)
            return copy.deepcopy(j) if j else None

    def update(self, job_id: str, **fields: Any) -> Optional[dict]:
        with self._lock:
            j = self._jobs.get(job_id)
            if j is None:
                return None
            j.update(copy.deepcopy(fields))
            j["version"] = j.get("version", 0) + 1
            return copy.deepcopy(j)

    def cancel(self, job_id: str) -> None:
        with self._lock:
            self._cancel.add(job_id)

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancel

    def active(self) -> list[dict]:
        with self._lock:
            return [copy.deepcopy(j) for j in self._jobs.values() if j["status"] in ACTIVE]


class RedisStore:
    def __init__(self, url: str) -> None:
        import redis

        self.r = redis.Redis.from_url(url, decode_responses=True)

    def _k(self, job_id: str) -> str:
        return f"sv:job:{job_id}"

    def put(self, job: dict) -> None:
        self.r.set(self._k(job["id"]), json.dumps(job, ensure_ascii=False), ex=TTL_SECONDS)
        if job["status"] in ACTIVE:
            self.r.sadd("sv:jobs:active", job["id"])

    def get(self, job_id: str) -> Optional[dict]:
        raw = self.r.get(self._k(job_id))
        return json.loads(raw) if raw else None

    def update(self, job_id: str, **fields: Any) -> Optional[dict]:
        # One writer per job (the worker), so read-modify-write is safe here.
        j = self.get(job_id)
        if j is None:
            return None
        j.update(fields)
        j["version"] = j.get("version", 0) + 1
        self.r.set(self._k(job_id), json.dumps(j, ensure_ascii=False), ex=TTL_SECONDS)
        if j["status"] not in ACTIVE:
            self.r.srem("sv:jobs:active", job_id)
        return j

    def cancel(self, job_id: str) -> None:
        self.r.set(f"{self._k(job_id)}:cancel", "1", ex=TTL_SECONDS)

    def is_cancelled(self, job_id: str) -> bool:
        return bool(self.r.exists(f"{self._k(job_id)}:cancel"))

    def active(self) -> list[dict]:
        out = []
        for jid in self.r.smembers("sv:jobs:active"):
            j = self.get(jid)
            if j and j["status"] in ACTIVE:
                out.append(j)
            else:
                self.r.srem("sv:jobs:active", jid)
        return out


_store = None
_store_lock = threading.Lock()
_run_lock = threading.Lock()   # inline mode: one valuation at a time


def store():
    global _store
    with _store_lock:
        if _store is None:
            url = get_settings().redis_url
            _store = RedisStore(url) if url else MemoryStore()
        return _store


def reset_store() -> None:
    global _store
    with _store_lock:
        _store = None


async def enqueue(job: dict, payload: dict) -> None:
    """Hand the job to the arq worker, or run it in a thread when there is no Redis."""
    url = get_settings().redis_url
    if url:
        from arq import create_pool
        from arq.connections import RedisSettings

        pool = await create_pool(RedisSettings.from_dsn(url))
        try:
            await pool.enqueue_job("run_valuation", job["id"], payload, _job_id=job["id"])
        finally:
            await pool.close()
        return

    def _inline() -> None:
        from .services.valuation import execute
        with _run_lock:
            execute(job["id"], payload)

    threading.Thread(target=_inline, name=f"valuation-{job['id'][:8]}", daemon=True).start()

"""Shared cache with single-flight: when ten users ask for the same thing at once, one computes it and
the other nine wait for that result (P0 load protection). Backed by src.infra.kv (Redis or memory).

A stale copy is kept longer than the fresh one, so when an upstream source is down the caller can still
show the last known data, labelled with its time.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Optional

from src.infra.kv import kv

LOCK_SECONDS = 30
POLL = 0.1


def get(key: str) -> Optional[Any]:
    raw = kv().get(f"c:{key}")
    return json.loads(raw) if raw is not None else None


def get_stale(key: str) -> Optional[dict]:
    raw = kv().get(f"s:{key}")
    return json.loads(raw) if raw is not None else None


def put(key: str, value: Any, ttl: float, stale_ttl: Optional[float] = None) -> None:
    raw = json.dumps(value, ensure_ascii=False, default=str)
    kv().set(f"c:{key}", raw, ttl=ttl)
    if stale_ttl:
        kv().set(f"s:{key}", json.dumps({"value": value, "at": time.time()}, ensure_ascii=False, default=str), ttl=stale_ttl)


def single_flight(key: str, compute: Callable[[], Any], ttl: float, stale_ttl: Optional[float] = None,
                  wait: float = LOCK_SECONDS) -> Any:
    """Cached value, or compute it once for everyone asking at the same time."""
    hit = get(key)
    if hit is not None:
        return hit
    store = kv()
    deadline = time.monotonic() + wait
    while True:
        if store.set(f"lk:{key}", "1", ttl=wait, nx=True):
            try:
                hit = get(key)                     # someone finished while we were taking the lock
                if hit is not None:
                    return hit
                value = compute()
                put(key, value, ttl, stale_ttl)
                return value
            finally:
                store.delete(f"lk:{key}")
        time.sleep(POLL)
        hit = get(key)
        if hit is not None:
            return hit
        if time.monotonic() >= deadline:          # the holder died or is very slow: do it ourselves
            value = compute()
            put(key, value, ttl, stale_ttl)
            return value


def run_once(key: str, fn: Callable[[], Any], wait: float = LOCK_SECONDS) -> tuple[bool, Any]:
    """Run ``fn`` unless another process is already running it for ``key``; then wait for that one to
    finish and return (False, None). For side effects (syncing a ticker), not for values."""
    store = kv()
    if store.set(f"lk:{key}", "1", ttl=wait * 4, nx=True):
        try:
            return True, fn()
        finally:
            store.delete(f"lk:{key}")
    deadline = time.monotonic() + wait
    while store.get(f"lk:{key}") and time.monotonic() < deadline:
        time.sleep(POLL * 2)
    return False, None

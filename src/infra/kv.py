"""A tiny key-value store shared by every process: Redis when REDIS_URL is set (Docker: API and worker
see the same counters, locks and cache), an in-process dict otherwise (local dev, tests).

Only what rate limiting, single-flight and caching need: get / set with TTL / set-if-absent / delete /
increment-in-a-window. Values are strings; callers JSON-encode.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Optional


class MemoryKV:
    def __init__(self) -> None:
        self._d: dict[str, tuple[str, Optional[float]]] = {}
        self._lock = threading.Lock()

    def _alive(self, key: str) -> Optional[str]:
        v = self._d.get(key)
        if v is None:
            return None
        if v[1] is not None and v[1] <= time.monotonic():
            self._d.pop(key, None)
            return None
        return v[0]

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            return self._alive(key)

    def set(self, key: str, value: str, ttl: Optional[float] = None, nx: bool = False) -> bool:
        with self._lock:
            if nx and self._alive(key) is not None:
                return False
            self._d[key] = (value, time.monotonic() + ttl if ttl else None)
            return True

    def delete(self, key: str) -> None:
        with self._lock:
            self._d.pop(key, None)

    def incr(self, key: str, ttl: float) -> tuple[int, float]:
        """Count in a fixed window that starts at the first hit; returns (count, seconds left)."""
        with self._lock:
            now = time.monotonic()
            v = self._d.get(key)
            if v is None or (v[1] is not None and v[1] <= now):
                self._d[key] = ("1", now + ttl)
                return 1, ttl
            n = int(v[0]) + 1
            self._d[key] = (str(n), v[1])
            return n, max(0.0, (v[1] or now) - now)

    def ttl(self, key: str) -> float:
        with self._lock:
            v = self._d.get(key)
            if v is None or v[1] is None:
                return 0.0
            return max(0.0, v[1] - time.monotonic())


class RedisKV:
    def __init__(self, url: str) -> None:
        import redis
        self.r = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)

    def get(self, key: str) -> Optional[str]:
        return self.r.get(key)

    def set(self, key: str, value: str, ttl: Optional[float] = None, nx: bool = False) -> bool:
        return bool(self.r.set(key, value, px=int(ttl * 1000) if ttl else None, nx=nx))

    def delete(self, key: str) -> None:
        self.r.delete(key)

    def incr(self, key: str, ttl: float) -> tuple[int, float]:
        p = self.r.pipeline()
        p.incr(key)
        p.pexpire(key, int(ttl * 1000), nx=True)
        p.pttl(key)
        n, _, left = p.execute()
        return int(n), max(0.0, (left or 0) / 1000)

    def ttl(self, key: str) -> float:
        left = self.r.pttl(key)
        return max(0.0, left / 1000) if left and left > 0 else 0.0


_kv = None
_url: Optional[str] = None


def kv():
    """The store for this process; follows REDIS_URL (tests switch it between cases)."""
    global _kv, _url
    url = (os.environ.get("REDIS_URL") or "").strip() or None
    if _kv is None or url != _url:
        _kv, _url = (RedisKV(url) if url else MemoryKV()), url
    return _kv


def reset() -> None:
    global _kv, _url
    _kv, _url = None, None

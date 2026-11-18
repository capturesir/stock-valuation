"""Outbound protection for every external data source (roadmap: P0 load protection).

- **Pacing**: at most one call per ``interval`` seconds per provider, across all processes (Redis), so
  the system as a whole stays under each source's limits however many users click at once.
  SEC publishes 10 requests/second; Yahoo's limits are unpublished, so it is paced conservatively.
- **Circuit breaker**: after ``threshold`` consecutive failures (429, 5xx, network errors) a provider is
  paused for ``open_for`` seconds; calls fail fast with ProviderUnavailable instead of piling up, and
  callers fall back to stored data. The first call after the pause is a probe.
- A 404 is an answer ("no such ticker"), not a failure.

Tune with APP_OUTBOUND_<PROVIDER>_INTERVAL (seconds); APP_OUTBOUND_LIMITS=0 switches pacing off (tests).
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import urlparse

from ..infra.kv import kv


@dataclass(frozen=True)
class Policy:
    interval: float        # seconds between calls, all processes together
    threshold: int = 5     # consecutive failures that open the breaker
    open_for: float = 60   # seconds the breaker stays open
    max_wait: float = 15   # longest a caller queues for a slot


POLICIES = {
    "yahoo": Policy(0.5),
    "sec": Policy(0.12),                 # SEC fair-access: <= 10 requests/second
    "adb": Policy(1.0),
    "tradingeconomics": Policy(2.0),
    "damodaran": Policy(1.0),
    "hkex": Policy(0.5),
    "other": Policy(0.2),
}
HOSTS = {"finance.yahoo.com": "yahoo", "sec.gov": "sec", "asianbondsonline.adb.org": "adb",
         "tradingeconomics.com": "tradingeconomics", "stern.nyu.edu": "damodaran", "hkexnews.hk": "hkex",
         "hkex.com.hk": "hkex"}
FAILURE_STATUS = {0, 429, 500, 502, 503, 504}


class ProviderUnavailable(RuntimeError):
    def __init__(self, provider: str, retry_after: float, reason: str):
        super().__init__(f"{provider} 暫時無法使用（{reason}），約 {int(retry_after) + 1} 秒後再試")
        self.provider, self.retry_after, self.reason = provider, retry_after, reason


def provider_for(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for suffix, name in HOSTS.items():
        if host == suffix or host.endswith("." + suffix):
            return name
    return "other"


def _policy(provider: str) -> Policy:
    base = POLICIES.get(provider, POLICIES["other"])
    raw = os.environ.get(f"APP_OUTBOUND_{provider.upper()}_INTERVAL")
    if raw:
        try:
            return Policy(float(raw), base.threshold, base.open_for, base.max_wait)
        except ValueError:
            pass
    return base


def _pacing_on() -> bool:
    return os.environ.get("APP_OUTBOUND_LIMITS", "1").lower() not in ("0", "false", "off", "no")


def check_open(provider: str) -> None:
    store = kv()
    if store.get(f"cb:{provider}:open"):
        raise ProviderUnavailable(provider, store.ttl(f"cb:{provider}:open"), "連續失敗，暫停中")


def acquire(provider: str) -> None:
    """Wait for this provider's next slot (or give up after max_wait)."""
    if not _pacing_on():
        return
    p, store = _policy(provider), kv()
    deadline = time.monotonic() + p.max_wait
    while not store.set(f"rl:{provider}", "1", ttl=p.interval, nx=True):
        if time.monotonic() >= deadline:
            raise ProviderUnavailable(provider, p.interval, "請求排隊過長")
        time.sleep(min(p.interval, max(0.02, store.ttl(f"rl:{provider}"))))


def record(provider: str, ok: bool) -> None:
    store, p = kv(), _policy(provider)
    if ok:
        store.delete(f"cb:{provider}:fails")
        return
    n, _ = store.incr(f"cb:{provider}:fails", ttl=max(p.open_for * 2, 120))
    if n >= p.threshold:
        store.set(f"cb:{provider}:open", "1", ttl=p.open_for)
        store.delete(f"cb:{provider}:fails")


def record_status(provider: str, status: int) -> None:
    record(provider, status not in FAILURE_STATUS)


@contextmanager
def call(provider: str):
    """Around a call that raises on failure (yfinance): pace, fail fast when open, record the outcome."""
    check_open(provider)
    acquire(provider)
    try:
        yield
    except Exception:
        record(provider, False)
        raise
    record(provider, True)


def status() -> dict:
    """Breaker state per provider, for the admin page."""
    store = kv()
    out = {}
    for name in POLICIES:
        out[name] = {"open": bool(store.get(f"cb:{name}:open")), "retry_after": round(store.ttl(f"cb:{name}:open"), 1),
                     "recent_failures": int(store.get(f"cb:{name}:fails") or 0),
                     "interval": _policy(name).interval}
    return out

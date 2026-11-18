"""Per-user request limits and the valuation queue policy (P0 load protection).

Limits count per signed-in user (per client IP for anonymous calls), in fixed windows kept in the shared
store, so every API process applies the same budget. Exceeding one answers 429 with Retry-After and a
``retry_after`` the web app turns into a cool-down countdown. Administrators change the numbers in
Administration → Limits; defaults below.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

from src.infra.kv import kv

from .models import Setting

KEY = "limits"
DEFAULTS = {
    "lookup_per_minute": 30,            # company profile lookups
    "prices_per_minute": 60,            # price series (may trigger a data sync)
    "valuations_per_hour": 10,          # valuation starts per user
    "concurrent_valuations_per_user": 1,
    "queue_max": 20,                    # valuations queued or running, whole system
    "revalue_cooldown_hours": 6,        # same ticker, date and AI mode valued this recently: ask first
}
BOUNDS = {k: (1, 10_000) for k in DEFAULTS} | {"revalue_cooldown_hours": (0, 168)}


class RateLimited(Exception):
    def __init__(self, code: str, retry_after: float, **params):
        super().__init__(code)
        self.code, self.retry_after, self.params = code, max(1, int(retry_after + 0.999)), params


def get(db: Session) -> dict:
    row = db.get(Setting, KEY)
    out = dict(DEFAULTS)
    if row and isinstance(row.value, dict):
        out.update({k: v for k, v in row.value.items() if k in DEFAULTS})
    return out


def update(db: Session, changes: dict) -> dict:
    current = get(db)
    for k, v in changes.items():
        if k in DEFAULTS and v is not None:
            lo, hi = BOUNDS[k]
            current[k] = int(min(max(int(v), lo), hi))
    row = db.get(Setting, KEY)
    if row is None:
        db.add(Setting(key=KEY, value=current))
    else:
        row.value = current
    db.flush()
    return current


def who(request: Request, user_id: Optional[str]) -> str:
    if user_id:
        return f"u:{user_id}"
    fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return f"ip:{fwd or (request.client.host if request.client else 'unknown')}"


def hit(scope: str, subject: str, limit: int, window: int) -> None:
    n, left = kv().incr(f"rl:u:{scope}:{subject}", ttl=window)
    if n > limit:
        raise RateLimited("rate_limited", left, limit=limit, window=window)

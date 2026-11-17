"""Daily market-data update (roadmap v2.1 §4).

Runs at APP_MARKET_UPDATE_TIMES (default 06:30 and 17:30, in APP_TIMEZONE = Asia/Macau): after the
US close (16:00 New York) and after the Hong Kong close (16:10). With Redis the arq worker runs it
as a cron job; without Redis (local dev) a daemon thread in the API process does. Tests switch it
off with APP_MARKET_SCHEDULER=0.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import threading
from typing import Optional
from zoneinfo import ZoneInfo

from .config import get_settings

log = logging.getLogger("sv.scheduler")
STATUS_KEY = "market_status"
_thread: Optional[threading.Thread] = None
_stop = threading.Event()


def times() -> list[tuple[int, int]]:
    raw = os.environ.get("APP_MARKET_UPDATE_TIMES", "06:30,17:30")
    out = []
    for part in raw.split(","):
        try:
            h, m = part.strip().split(":")
            out.append((int(h) % 24, int(m) % 60))
        except ValueError:
            continue
    return sorted(set(out)) or [(6, 30), (17, 30)]


def enabled() -> bool:
    return os.environ.get("APP_MARKET_SCHEDULER", "1").lower() not in ("0", "false", "no", "off")


def next_runs(now: Optional[dt.datetime] = None, n: int = 2) -> list[dt.datetime]:
    tz = ZoneInfo(get_settings().timezone)
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(tz)
    out = []
    for day in range(3):
        d = now.date() + dt.timedelta(days=day)
        for h, m in times():
            at = dt.datetime(d.year, d.month, d.day, h, m, tzinfo=tz)
            if at > now:
                out.append(at)
    return sorted(out)[:n]


def utc_cron() -> list[tuple[int, int]]:
    """The run times converted to UTC hour/minute, for arq's cron (the worker container runs in UTC)."""
    tz = ZoneInfo(get_settings().timezone)
    today = dt.date.today()
    out = []
    for h, m in times():
        u = dt.datetime(today.year, today.month, today.day, h, m, tzinfo=tz).astimezone(dt.timezone.utc)
        out.append((u.hour, u.minute))
    return out


def run_once() -> dict:
    """One full update; the summary is kept in settings for the status endpoint."""
    from .db import session
    from .models import Setting
    from .services import market

    summary = market.update_all(session)
    with session() as db:
        row = db.get(Setting, STATUS_KEY)
        if row is None:
            db.add(Setting(key=STATUS_KEY, value=summary))
        else:
            row.value = summary
    log.info("market update: %s synced, %s failed", summary["synced"], len(summary["failed"]))
    return summary


def run_in_background() -> bool:
    """Start one update now without blocking the request; False when one is already running."""
    global _manual
    if _manual is not None and _manual.is_alive():
        return False
    _manual = threading.Thread(target=_safe_run, name="market-update-now", daemon=True)
    _manual.start()
    return True


_manual: Optional[threading.Thread] = None


def _safe_run() -> None:
    try:
        run_once()
    except Exception:  # never kill the thread
        log.exception("market update failed")


def _loop() -> None:
    while not _stop.is_set():
        nxt = next_runs(n=1)[0]
        wait = (nxt - dt.datetime.now(dt.timezone.utc)).total_seconds()
        if _stop.wait(max(1.0, wait)):
            return
        _safe_run()


def start_inline() -> None:
    """Local dev without Redis: a daemon thread in the API process."""
    global _thread
    if not enabled() or get_settings().redis_url or (_thread is not None and _thread.is_alive()):
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="market-scheduler", daemon=True)
    _thread.start()


def stop_inline() -> None:
    _stop.set()

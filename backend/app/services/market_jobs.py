"""Background market syncs triggered by use (adding to a watchlist, valuing a stock).

One worker thread, so syncs never run concurrently from this process. Tests set INLINE = True to
run them in the request instead (deterministic, and nothing writes after a test ends).
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

log = logging.getLogger("sv.market")
INLINE = False
_pool: Optional[ThreadPoolExecutor] = None


def _run(ticker: str) -> None:
    from ..db import session
    from . import market

    try:
        with session() as db:
            market.sync(db, ticker)
    except Exception as exc:  # recorded on the security by the next sync; never crash the pool
        log.warning("background sync of %s failed: %s", ticker, exc)


def submit_sync(ticker: str) -> None:
    global _pool
    if INLINE:
        _run(ticker)
        return
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="market-sync")
    _pool.submit(_run, ticker)

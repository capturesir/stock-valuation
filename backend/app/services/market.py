"""Market data that accumulates (roadmap v2.1 §4): daily prices, dividends, splits and FX rates.

Storage rules
- Prices are stored **unadjusted** (as traded that day). Yahoo returns split-adjusted closes and
  dividends, so they are converted back with the splits known at fetch time. Unadjusted rows never
  change when a later split happens, which is what lets history accumulate safely.
- Split- and dividend-adjusted (total-return) series are computed here from the unadjusted bars and
  corporate actions, then checked against the source's own adjusted close.
- Nothing is lost: when the source later changes a stored value, the old value goes to
  price_revisions before the row is updated.
- A bar for today is stored only after that market's close, so a mid-session price is not saved
  as the day's close.
- FX pairs are securities too (``USDHKD=X``), so they accumulate the same way.

The source is pluggable (``set_source``); tests use a fake one and never touch the network.
The pure parts (sources, unadjusting, total return and its check) live in ``libs/sv_market`` since
alpha.3 step 10; this module keeps what reads and writes the database, and re-exports the rest.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from libs.sv_market.prices import (ADJ_CHECK_TOL, CLOSE_HOUR, DIV_MATCH_TOL, FIELDS, INCREMENTAL_DAYS, REL_TOL,  # noqa: F401
                                   USD_HKD_BAND, Bar, _check_total_return, _differs, _final_cutoff, _iso, _max_deviation,
                                   _now, _source_dividend_steps, total_return, unadjust)
from libs.sv_market.sources import (MarketSource, SourceBar, SourceHistory, SymbolNotFound, YahooSource,  # noqa: F401
                                    _num, fx_symbol)

from ..models import CorporateAction, PriceBar, PriceRevision, Security

log = logging.getLogger("sv.market")



def _guarded(provider: str):
    from src.fetch import guard          # the shared rate limit and circuit breaker
    return guard.call(provider)


_source: MarketSource = YahooSource(guard=_guarded)


def set_source(src: MarketSource) -> None:
    global _source
    _source = src


def get_source() -> MarketSource:
    return _source




# ------------------------------------------------------------------ sync

def track(db: Session, ticker: str) -> Security:
    sec = db.get(Security, ticker)
    if sec is None:
        sec = Security(ticker=ticker, tracked=True, source=get_source().name, bars=0)
        db.add(sec)
        db.flush()
    elif not sec.tracked:
        sec.tracked = True
    return sec


def sync(db: Session, ticker: str, full: bool = False, now: Optional[dt.datetime] = None,
         start: Optional[dt.date] = None) -> dict:
    """Backfill the whole history the first time (or with ``full``), otherwise re-read the last
    INCREMENTAL_DAYS and append. Returns a summary; errors are recorded on the security and raised.

    One sync per ticker at a time across all processes: a second caller (another user opening the same
    stock, the scheduler) waits for the running one and then reads what it stored."""
    from src.infra.kv import kv

    store = kv()
    if not store.set(f"lk:sync:{ticker}", "1", ttl=300, nx=True):
        import time as _t
        deadline = _t.monotonic() + 60
        while store.get(f"lk:sync:{ticker}") and _t.monotonic() < deadline:
            _t.sleep(0.2)
        db.expire_all()
        sec = db.get(Security, ticker)
        return {"ticker": ticker, "mode": "joined", "bars_added": 0, "bars_revised": 0, "actions_added": 0,
                "actions_revised": 0, "first_date": _iso(sec.first_date) if sec else None,
                "last_date": _iso(sec.last_date) if sec else None, "bars": sec.bars if sec else 0}
    try:
        out = _sync(db, ticker, full, now, start)
        db.commit()                      # visible to whoever waited on the lock
        return out
    finally:
        store.delete(f"lk:sync:{ticker}")


def _sync(db: Session, ticker: str, full: bool, now: Optional[dt.datetime], start_at: Optional[dt.date] = None) -> dict:
    """``start_at`` re-reads from that date (gap repair) instead of the usual overlap window."""
    src = get_source()
    sec = track(db, ticker)
    first = full or sec.backfilled_at is None or sec.last_date is None
    start = None if first else (start_at or sec.last_date - dt.timedelta(days=INCREMENTAL_DAYS))
    try:
        hist = src.history(ticker, start=start)
    except Exception as exc:
        if _forget_unknown(db, sec, exc):
            raise
        sec.last_error = f"{type(exc).__name__}: {exc}"[:500]
        sec.updated_at = _now()
        db.flush()
        raise
    meta = hist.meta or {}
    for k in ("name", "exchange", "currency", "quote_type", "timezone"):
        if meta.get(k):
            setattr(sec, k, meta[k])
    cutoff = _final_cutoff(sec.timezone, now)
    source_bars = [b for b in hist.bars if b.date <= cutoff]
    bars, actions = unadjust(source_bars)
    added, revised = _upsert_bars(db, ticker, bars, src.name)
    a_added, a_revised = _upsert_actions(db, ticker, actions, src.name)
    if first:
        sec.checks = dict(sec.checks or {}, total_return=_check_total_return(source_bars, bars, actions, sec.currency))
        sec.backfilled_at = _now()
    _refresh_coverage(db, sec)
    sec.updated_at, sec.last_error = _now(), None
    db.flush()
    return {"ticker": ticker, "mode": "backfill" if first else "update", "bars_added": added, "bars_revised": revised,
            "actions_added": a_added, "actions_revised": a_revised, "first_date": _iso(sec.first_date),
            "last_date": _iso(sec.last_date), "bars": sec.bars}


def _forget_unknown(db: Session, sec: Security, exc: Exception) -> bool:
    """A symbol the source does not know, with nothing stored, is not kept: otherwise a mistyped
    code (e.g. TENCENT) would stay tracked and fail in every daily update. Symbols with history
    (delisted, renamed) keep their data and their error."""
    if isinstance(exc, SymbolNotFound) and not sec.bars and sec.backfilled_at is None:
        db.delete(sec)
        db.flush()
        return True
    return False


def _upsert_bars(db: Session, ticker: str, bars: list[Bar], source: str) -> tuple[int, int]:
    if not bars:
        return 0, 0
    existing = {b.date: b for b in db.scalars(select(PriceBar).where(
        PriceBar.ticker == ticker, PriceBar.date >= bars[0].date, PriceBar.date <= bars[-1].date))}
    new_rows, revised, now = [], 0, _now()
    for b in bars:
        old = existing.get(b.date)
        if old is None:
            new_rows.append({"ticker": ticker, "date": b.date, "open": b.open, "high": b.high, "low": b.low,
                             "close": b.close, "volume": b.volume, "source": source, "fetched_at": now})
            continue
        changed = False
        for f in FIELDS:
            if _differs(getattr(old, f), getattr(b, f)):
                db.add(PriceRevision(ticker=ticker, date=b.date, field=f, old=getattr(old, f), new=getattr(b, f),
                                     source=source, seen_at=now))
                setattr(old, f, getattr(b, f))
                changed = True
        if old.volume != b.volume and b.volume is not None:
            old.volume = b.volume          # volume is often finalised a day later: not a revision
        if changed:
            old.fetched_at, revised = now, revised + 1
    for i in range(0, len(new_rows), 2000):
        # ON CONFLICT: a concurrent sync of the same ticker (scheduler vs. first use) is harmless
        db.execute(insert(PriceBar).values(new_rows[i:i + 2000]).on_conflict_do_nothing())
    return len(new_rows), revised


def _upsert_actions(db: Session, ticker: str, actions: list[tuple[dt.date, str, float]], source: str) -> tuple[int, int]:
    if not actions:
        return 0, 0
    existing = {(a.date, a.kind): a for a in db.scalars(select(CorporateAction).where(
        CorporateAction.ticker == ticker, CorporateAction.date >= actions[0][0]))}
    rows, revised, now = [], 0, _now()
    for d, kind, v in actions:
        old = existing.get((d, kind))
        if old is None:
            rows.append({"ticker": ticker, "date": d, "kind": kind, "value": v, "source": source, "fetched_at": now})
        elif _differs(old.value, v):
            db.add(PriceRevision(ticker=ticker, date=d, field=kind, old=old.value, new=v, source=source, seen_at=now))
            old.value, old.fetched_at, revised = v, now, revised + 1
    if rows:
        db.execute(insert(CorporateAction).values(rows).on_conflict_do_nothing())
    return len(rows), revised


def _refresh_coverage(db: Session, sec: Security) -> None:
    from sqlalchemy import func

    first, last, n = db.execute(select(func.min(PriceBar.date), func.max(PriceBar.date), func.count())
                                .where(PriceBar.ticker == sec.ticker)).one()
    sec.first_date, sec.last_date, sec.bars = first, last, n or 0


# ------------------------------------------------------------------ reading

@dataclass
class Point:
    date: dt.date
    close: float            # unadjusted, as traded
    adj_close: float        # split- and dividend-adjusted (total return), latest = latest close


def series(db: Session, ticker: str, start: Optional[dt.date] = None, end: Optional[dt.date] = None) -> list[Point]:
    """Whole history is used for the adjustment, then trimmed, so adj_close is the same whatever the window."""
    rows = db.execute(select(PriceBar.date, PriceBar.close).where(PriceBar.ticker == ticker)
                      .order_by(PriceBar.date)).all()
    acts = db.execute(select(CorporateAction.date, CorporateAction.kind, CorporateAction.value)
                      .where(CorporateAction.ticker == ticker)).all()
    adj = total_return([(r[0], r[1]) for r in rows], [(a[0], a[1], a[2]) for a in acts])
    return [Point(r[0], r[1], a) for r, a in zip(rows, adj)
            if (start is None or r[0] >= start) and (end is None or r[0] <= end)]


def fx_rates(db: Session, base: str, quote: str, start: Optional[dt.date] = None,
             end: Optional[dt.date] = None) -> dict[dt.date, float]:
    """1 unit of ``base`` in ``quote`` per day (same currency → empty dict; callers use 1.0)."""
    if base.upper() == quote.upper():
        return {}
    return {p.date: p.close for p in series(db, fx_symbol(base, quote), start, end)}


def tracked(db: Session) -> list[Security]:
    return list(db.scalars(select(Security).where(Security.tracked.is_(True)).order_by(Security.ticker)))


def update_all(session_factory) -> dict:
    """The daily job: sync every tracked security in its own transaction, so one failure does not
    stop the rest. Then make sure an FX pair exists (vs USD and HKD) for every currency in use and
    sync any pair that is new, so portfolios can be shown in any display currency."""
    started = _now()
    ok: list[str] = []
    failed: list[dict] = []

    def run(tickers: list[str]) -> None:
        for t in tickers:
            try:
                with session_factory() as db:
                    sync(db, t)
                ok.append(t)
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"[:300]
                failed.append({"ticker": t, "error": err})
                log.warning("market sync failed for %s: %s", t, exc)
                with session_factory() as db:      # the failed transaction rolled back; record it separately
                    sec = db.get(Security, t)
                    if sec is not None and not _forget_unknown(db, sec, exc):
                        sec.last_error, sec.updated_at = err, _now()

    with session_factory() as db:
        first = [s.ticker for s in tracked(db)]
    run(first)
    with session_factory() as db:
        currencies = {s.currency.upper() for s in tracked(db) if s.currency and not s.ticker.endswith("=X")}
        new_pairs = []
        for c in sorted(currencies):
            for q in ("USD", "HKD"):
                sym = fx_symbol(c, q)
                if c != q and sym not in first:
                    track(db, sym)
                    new_pairs.append(sym)
    run(new_pairs)
    return {"started_at": started.isoformat(timespec="seconds"), "finished_at": _now().isoformat(timespec="seconds"),
            "synced": len(ok), "failed": failed}

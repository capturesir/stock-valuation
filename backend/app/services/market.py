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
"""
from __future__ import annotations

import datetime as dt
import logging
import math
from dataclasses import dataclass, field
from typing import Iterable, Optional, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..models import CorporateAction, PriceBar, PriceRevision, Security

log = logging.getLogger("sv.market")

FIELDS = ("open", "high", "low", "close")
REL_TOL = 1e-5                  # differences below 0.001 % are rounding, not revisions
INCREMENTAL_DAYS = 14           # overlap re-read on each update, to catch late corrections
CLOSE_HOUR = 17                 # local exchange time after which today's bar counts as final
ADJ_CHECK_TOL = 0.001           # total-return series must match the source within 0.1 %
USD_HKD_BAND = (7.70, 7.90)     # Linked Exchange Rate band (7.75–7.85) with a little room for the source's FX
DIV_MATCH_TOL = 0.02            # a source dividend step within 2 % of the standard one counts as the same


class SymbolNotFound(LookupError):
    """The source says this symbol does not exist (as opposed to the source being down)."""




# ------------------------------------------------------------------ source interface

@dataclass
class SourceBar:
    """As the source reports it: Yahoo prices, volume and dividends are split-adjusted."""
    date: dt.date
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: float
    adj_close: Optional[float]
    volume: Optional[float]
    dividend: float = 0.0
    split: float = 0.0          # ratio on the split date, 0 when none


@dataclass
class SourceHistory:
    meta: dict = field(default_factory=dict)   # name, exchange, currency, quote_type, timezone
    bars: list[SourceBar] = field(default_factory=list)


class MarketSource(Protocol):
    name: str

    def history(self, ticker: str, start: Optional[dt.date] = None) -> SourceHistory: ...


class YahooSource:
    name = "yahoo"

    def history(self, ticker: str, start: Optional[dt.date] = None) -> SourceHistory:
        import yfinance as yf

        from src.fetch import guard

        t = yf.Ticker(ticker)
        kw = {"start": start.isoformat()} if start else {"period": "max"}
        try:
            with guard.call("yahoo"):
                df = t.history(auto_adjust=False, actions=True, raise_errors=True, **kw)
        except Exception as exc:
            if type(exc).__name__ in guard.NOT_FAILURES:      # yfinance: no such symbol
                raise SymbolNotFound(f"{ticker}: {exc}") from exc
            raise
        md = getattr(t, "history_metadata", None) or {}
        meta = {"name": md.get("longName") or md.get("shortName"), "exchange": md.get("fullExchangeName") or md.get("exchangeName"),
                "currency": md.get("currency"), "quote_type": md.get("instrumentType"),
                "timezone": md.get("exchangeTimezoneName")}
        bars = []
        for idx, row in df.iterrows():
            close = _num(row.get("Close"))
            if close is None:
                continue
            bars.append(SourceBar(date=idx.date(), open=_num(row.get("Open")), high=_num(row.get("High")),
                                  low=_num(row.get("Low")), close=close, adj_close=_num(row.get("Adj Close")),
                                  volume=_num(row.get("Volume")), dividend=_num(row.get("Dividends")) or 0.0,
                                  split=_num(row.get("Stock Splits")) or 0.0))
        return SourceHistory(meta=meta, bars=bars)


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


_source: MarketSource = YahooSource()


def set_source(src: MarketSource) -> None:
    global _source
    _source = src


def get_source() -> MarketSource:
    return _source


def fx_symbol(base: str, quote: str) -> str:
    return f"{base.upper()}{quote.upper()}=X"


# ------------------------------------------------------------------ unadjusting

@dataclass
class Bar:
    date: dt.date
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: float
    volume: Optional[int]


def unadjust(bars: list[SourceBar]) -> tuple[list[Bar], list[tuple[dt.date, str, float]]]:
    """Undo the source's split adjustment. Factor for a day = product of split ratios strictly after it."""
    bars = sorted(bars, key=lambda b: b.date)
    factor = 1.0
    out: list[Bar] = []
    actions: list[tuple[dt.date, str, float]] = []
    for b in reversed(bars):
        f = factor
        out.append(Bar(b.date, *(None if v is None else v * f for v in (b.open, b.high, b.low)), b.close * f,
                       None if b.volume is None else int(round(b.volume / f))))
        if b.dividend:
            actions.append((b.date, "dividend", b.dividend * f))
        if b.split and b.split > 0 and b.split != 1:
            actions.append((b.date, "split", b.split))
            factor *= b.split               # every earlier day was priced before this split
    out.reverse()
    return out, sorted(actions)


def total_return(bars: list[tuple[dt.date, float]], actions: Iterable[tuple[dt.date, str, float]]) -> list[float]:
    """Split- and dividend-adjusted closes, scaled so the latest equals the latest close
    (the convention data vendors use for "Adj Close"). ``bars`` are unadjusted (date, close)."""
    by_date: dict[dt.date, list[tuple[str, float]]] = {}
    for d, kind, v in actions:
        by_date.setdefault(d, []).append((kind, v))
    n = len(bars)
    adj = [0.0] * n
    factor = 1.0
    for i in range(n - 1, -1, -1):
        d, close = bars[i]
        adj[i] = close * factor
        for kind, v in by_date.get(d, []):
            if kind == "split" and v > 0:
                factor /= v
            elif kind == "dividend" and i > 0 and bars[i - 1][1] > 0:
                factor *= max(0.0, 1 - v / bars[i - 1][1])
    return adj


# ------------------------------------------------------------------ sync

def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _final_cutoff(tz: Optional[str], now: Optional[dt.datetime] = None) -> dt.date:
    """Latest date whose bar is final: today after the local close, otherwise yesterday."""
    now = now or _now()
    try:
        local = now.astimezone(ZoneInfo(tz or "America/New_York"))
    except Exception:
        local = now.astimezone(ZoneInfo("America/New_York"))
    return local.date() if local.hour >= CLOSE_HOUR else local.date() - dt.timedelta(days=1)


def _differs(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return (a is None) != (b is None)
    return abs(a - b) > max(1e-9, REL_TOL * max(abs(a), abs(b)))


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


def _iso(d: Optional[dt.date]) -> Optional[str]:
    return d.isoformat() if d else None


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


def _max_deviation(pairs, bars: list[Bar], actions) -> tuple[float, Optional[dt.date]]:
    """Largest relative gap between our series and the source's adjusted close, both scaled to the latest day."""
    ours_by_date = dict(zip((b.date for b in bars), total_return([(b.date, b.close) for b in bars], actions)))
    last_src, last_ours = pairs[-1][0].adj_close, ours_by_date[pairs[-1][1].date]
    worst, worst_date = 0.0, None
    for sb, b in pairs:
        s = sb.adj_close / last_src
        if s > 0:
            dev = abs((ours_by_date[b.date] / last_ours) / s - 1)
            if dev > worst:
                worst, worst_date = dev, b.date
    return worst, worst_date


def _source_dividend_steps(source_bars: list[SourceBar]) -> list[dict]:
    """For each ex-date, the dividend the source's adjusted close actually deducted:
    step = (adj/close the day before) / (adj/close on the day) = 1 − D_applied / previous close."""
    sb = sorted(source_bars, key=lambda x: x.date)
    out = []
    for i in range(1, len(sb)):
        b, prev = sb[i], sb[i - 1]
        if not b.dividend or not (b.adj_close and prev.adj_close and b.close and prev.close):
            continue
        step = (prev.adj_close / prev.close) / (b.adj_close / b.close)
        applied = (1 - step) * prev.close
        out.append({"date": b.date, "dividend": b.dividend, "applied": applied,
                    "ratio": b.dividend / applied if applied > 0 else None})
    return out


def _check_total_return(source_bars: list[SourceBar], bars: list[Bar], actions, currency: Optional[str] = None) -> dict:
    """Our total-return series vs the source's adjusted close.

    When they differ, look for the one known source error: a dividend declared in USD but traded in
    HKD (HSBC 0005.HK, MINISO 9896.HK) whose USD amount Yahoo deducts from the HKD price, so its step
    is ~7.8 times too small. If every mismatching ex-date has that ratio, and repeating the source's
    mistake reproduces its series within tolerance, the gap is explained: our figure is the correct one."""
    src_sorted = sorted(source_bars, key=lambda x: x.date)
    pairs = [(sb, b) for sb, b in zip(src_sorted, bars) if sb.adj_close]
    if len(pairs) < 2:
        return {"status": "skipped", "reason_code": "no_source_adjusted", "reason": "來源沒有調整後價格"}
    worst, worst_date = _max_deviation(pairs, bars, actions)
    out = {"status": "ok" if worst <= ADJ_CHECK_TOL else "warn", "max_deviation": round(worst, 6),
           "at": _iso(worst_date), "tolerance": ADJ_CHECK_TOL, "checked_at": _now().isoformat(timespec="seconds")}
    if out["status"] == "ok":
        return out
    steps = _source_dividend_steps(source_bars)
    wrong = [e for e in steps if e["ratio"] is None or abs(e["ratio"] - 1) > DIV_MATCH_TOL]
    lo, hi = USD_HKD_BAND
    usd = [e for e in wrong if e["ratio"] and lo <= e["ratio"] <= hi]
    if (currency or "").upper() == "HKD" and wrong and len(usd) == len(wrong):
        # repeat the source's mistake on those dates; what is left must be within tolerance
        fix = {e["date"]: e["ratio"] for e in usd}
        mimic = [(d, k, v / fix[d]) if k == "dividend" and d in fix else (d, k, v) for d, k, v in actions]
        residual, _ = _max_deviation(pairs, bars, mimic)
        if residual <= ADJ_CHECK_TOL:
            out.update(status="explained", residual_deviation=round(residual, 6), note_code="source_usd_dividend",
                       events=[{"date": _iso(e["date"]), "dividend": round(e["dividend"], 6),
                                "source_applied": round(e["applied"], 6)} for e in usd],
                       note=("來源的調整後價格把以美元宣派的派息當作港元扣減（約少扣 7.8 倍）；"
                             "系統的總回報按港元派息計算，是正確的數字。"))
            return out
    out["note_code"] = "adjusted_mismatch"
    out["note"] = ("系統計算的總回報與來源的調整後價格不一致，需要人工覆核。常見原因：來源以錯誤貨幣計算派息"
                   "（例如以美元宣派、港元交易的股票）、或漏計供股等公司行動。系統的總回報由未調整價格與派息自行計算。")
    return out


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

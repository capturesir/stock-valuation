"""Market-data completeness (alpha.4, step 1): gaps against the exchange calendar, repair, a monthly
full re-read, and dividends cross-checked with SEC filings.

- Each security is checked against its exchange's official trading calendar (exchange_calendars):
  every session between its first and last stored day should have a bar. Missing sessions are grouped
  into contiguous segments; the source is re-read from the earliest gap. Whatever is still missing after
  that is recorded as missing at the source — never filled in or interpolated.
- A series whose last bar is several sessions behind the calendar is flagged as stale.
- Bars on days the exchange was closed are counted (usually the source's own quirk; kept, but noted).
- Once a month each security's whole history is re-read (a few per day), so silent source corrections are
  caught; changed numbers keep their old value in price_revisions, as always.
- Dividends: for US filers, the yearly dividends per share in market data are compared with SEC filings
  (CommonStockDividendsPerShareDeclared / ...CashPaid), over three fiscal years to absorb timing.
"""
from __future__ import annotations

import datetime as dt
import logging
from functools import lru_cache
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CorporateAction, FiledFactRow, PriceBar, SecTicker, Security
from . import market

log = logging.getLogger("sv.completeness")
WEEKDAYS = "WEEKDAYS"                  # currency pairs trade Monday to Friday
EXCHANGES = {"NMS": "XNYS", "NGM": "XNYS", "NCM": "XNYS", "NYQ": "XNYS", "ASE": "XNYS", "PCX": "XNYS", "BTS": "XNYS",
             "NAS": "XNYS", "NIM": "XNYS", "HKG": "XHKG", "SHH": "XSHG", "SHZ": "XSHG", "JPX": "XTKS", "TYO": "XTKS",
             "LSE": "XLON", "IOB": "XLON"}
SUFFIXES = {".HK": "XHKG", ".SS": "XSHG", ".SZ": "XSHG", ".T": "XTKS", ".L": "XLON"}
STALE_SESSIONS = 3
MAX_SEGMENTS = 20
REREAD_DAYS = 30
REREAD_PER_RUN = 10
DIVIDEND_TOL = 0.02
DIVIDEND_SHIFTS = (0, 15, 30, 45, 60, 75)   # days the market window may be moved to meet "declared" timing
CALENDAR_FLOOR = dt.date(1990, 1, 2)   # exchange_calendars' reliable range starts around here


def calendar_for(sec: Security) -> Optional[str]:
    if sec.ticker.endswith("=X") or (sec.quote_type or "").upper() == "CURRENCY":
        return WEEKDAYS
    if sec.exchange and sec.exchange.upper() in EXCHANGES:
        return EXCHANGES[sec.exchange.upper()]
    for suffix, code in SUFFIXES.items():
        if sec.ticker.upper().endswith(suffix):
            return code
    if "." not in sec.ticker and (sec.quote_type or "EQUITY").upper() in ("EQUITY", "ETF", "MUTUALFUND", "INDEX"):
        return "XNYS"
    return None


@lru_cache(maxsize=16)
def _calendar(code: str, start_year: int):
    import exchange_calendars as xc
    import pandas as pd

    return xc.get_calendar(code, start=pd.Timestamp(dt.date(start_year, 1, 1)))


def sessions(code: str, start: dt.date, end: dt.date) -> list[dt.date]:
    """Trading days from ``start`` to ``end`` inclusive (replaced in tests)."""
    if end < start:
        return []
    if code == WEEKDAYS:
        out, d = [], start
        while d <= end:
            # Christmas Day and New Year's Day have no FX fixing at the source
            if d.weekday() < 5 and (d.month, d.day) not in ((12, 25), (1, 1)):
                out.append(d)
            d += dt.timedelta(days=1)
        return out
    start = max(start, CALENDAR_FLOOR)
    cal = _calendar(code, start.year)
    last = cal.last_session.date()
    if start > last:
        return []
    return [t.date() for t in cal.sessions_in_range(start.isoformat(), min(end, last).isoformat())]


def _segments(missing: list[dt.date], sess: list[dt.date]) -> list[list]:
    """Contiguous runs of missing sessions: [[first, last, count], …]."""
    pos = {d: i for i, d in enumerate(sess)}
    out: list[list] = []
    for d in missing:
        if out and pos[d] == pos[out[-1][1]] + 1:
            out[-1][1], out[-1][2] = d, out[-1][2] + 1
        else:
            out.append([d, d, 1])
    return out


def _scan(db: Session, sec: Security, code: str, now: Optional[dt.datetime]) -> dict:
    have = set(db.scalars(select(PriceBar.date).where(PriceBar.ticker == sec.ticker)))
    first, last = min(have), max(have)
    expected_last = market._final_cutoff(sec.timezone, now)
    sess = sessions(code, first, expected_last)
    inside = [d for d in sess if d <= last]
    missing = [d for d in inside if d not in have]
    behind = len([d for d in sess if d > last])
    extra = len([d for d in have if d >= (inside[0] if inside else first) and d not in set(inside)])
    return {"first": first, "last": last, "sessions": len(inside), "missing": missing, "behind": behind, "extra": extra,
            "checked_from": inside[0] if inside else None, "sess": sess}


def check(db: Session, ticker: str, repair: bool = True, now: Optional[dt.datetime] = None) -> dict:
    """Check one security, re-read the source over any gap, and record the result on the security."""
    sec = db.get(Security, ticker)
    if sec is None or not sec.bars:
        return {"ticker": ticker, "status": "no_data"}
    code = calendar_for(sec)
    if code is None:
        out = {"status": "no_calendar", "checked_at": market._now().isoformat(timespec="seconds")}
        sec.checks = dict(sec.checks or {}, completeness=out)
        return {"ticker": ticker, **out}
    scan = _scan(db, sec, code, now)
    found = len(scan["missing"])
    repaired = 0
    if repair and (found or scan["behind"] > STALE_SESSIONS):
        start = (min(scan["missing"]) if found else scan["last"]) - dt.timedelta(days=7)
        try:
            market.sync(db, ticker, now=now, start=start)
            db.expire(sec)
            sec = db.get(Security, ticker)
            after = _scan(db, sec, code, now)
            repaired = found - len(after["missing"])
            scan = after
        except Exception as exc:          # recorded; the gaps stay listed
            log.warning("gap repair for %s failed: %s", ticker, type(exc).__name__)
    missing = scan["missing"]
    stale = scan["behind"] > STALE_SESSIONS
    status = "stale" if stale else ("source_missing" if missing else "complete")
    prev = (sec.checks or {}).get("completeness") or {}
    out = {"status": status, "calendar": code, "checked_at": market._now().isoformat(timespec="seconds"),
           "checked_from": scan["checked_from"].isoformat() if scan["checked_from"] else None,
           "sessions": scan["sessions"], "found_missing": found, "repaired": repaired, "missing": len(missing),
           "segments": [[a.isoformat(), b.isoformat(), n] for a, b, n in _segments(missing, scan["sess"])[:MAX_SEGMENTS]],
           "sessions_behind": scan["behind"], "extra_days": scan["extra"],
           "full_reread_at": prev.get("full_reread_at")}
    sec.checks = dict(sec.checks or {}, completeness=out)
    db.flush()
    return {"ticker": ticker, **out}


def reread(db: Session, ticker: str, now: Optional[dt.datetime] = None) -> dict:
    """Read the whole history again (revisions are logged), then check completeness."""
    r = market.sync(db, ticker, full=True, now=now)
    out = check(db, ticker, repair=False, now=now)
    sec = db.get(Security, ticker)
    comp = dict((sec.checks or {}).get("completeness") or {}, full_reread_at=market._now().isoformat(timespec="seconds"),
                full_reread_revised=r["bars_revised"] + r["actions_revised"])
    sec.checks = dict(sec.checks or {}, completeness=comp)
    db.flush()
    return {**out, "revised": comp["full_reread_revised"]}


def dividend_check(db: Session, ticker: str) -> Optional[dict]:
    """Yearly dividends per share in market data vs SEC filings (first-reported, so on the same share basis)."""
    row = db.get(SecTicker, ticker)
    if row is None:
        return None
    facts = db.execute(select(FiledFactRow.concept, FiledFactRow.period_start, FiledFactRow.period_end, FiledFactRow.value,
                              FiledFactRow.filed).where(
        FiledFactRow.cik == row.cik, FiledFactRow.taxonomy == "us-gaap", FiledFactRow.unit == "USD/shares",
        FiledFactRow.form.in_(("10-K", "10-K/A")),
        FiledFactRow.concept.in_(("CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid")))
    ).all()
    # both sides on today's share basis: a value from before a split is divided by the splits after it
    splits = db.execute(select(CorporateAction.date, CorporateAction.value).where(
        CorporateAction.ticker == ticker, CorporateAction.kind == "split")).all()

    def basis(day: dt.date) -> float:
        f = 1.0
        for d, r in splits:
            if r and d > day:
                f *= r
        return f

    years: dict[tuple, tuple] = {}
    for concept, s, e, v, filed in sorted(facts, key=lambda f: (f[0] != "CommonStockDividendsPerShareCashPaid", f[4])):
        if s is None or not (350 <= (e - s).days + 1 <= 380):
            continue
        years.setdefault((s, e), (float(v) / basis(filed), concept))   # paid preferred; first filed kept
    if not years:
        return None
    divs = [(d, v / basis(d)) for d, v in db.execute(select(CorporateAction.date, CorporateAction.value).where(
        CorporateAction.ticker == ticker, CorporateAction.kind == "dividend")).all()]
    if not divs:
        return None
    sec_row = db.get(Security, ticker)
    covered_from, today = (sec_row.first_date if sec_row and sec_row.first_date else min(d for d, _ in divs)), dt.date.today()
    # years the price history covers (a fiscal year may start on a holiday, before the first trading day)
    fys = [(s, e, v, c) for (s, e), (v, c) in sorted(years.items()) if s >= covered_from - dt.timedelta(days=7)]
    if len(fys) < 3:
        return None
    last3 = fys[-3:]
    sec3 = sum(v for *_, v, _c in last3)
    # "declared in the year" can run a quarter ahead of "ex-date in the year" (e.g. JPMorgan declares in December
    # with a January ex-date): try small shifts of the market window and keep the one that aligns best
    best = None
    for shift in DIVIDEND_SHIFTS:
        delta = dt.timedelta(days=shift)
        if last3[-1][1] + delta > today:
            continue
        mkt3 = sum(v for d, v in divs for s, e, *_ in last3 if s + delta <= d <= e + delta)
        diff = abs(sec3 - mkt3) / max(abs(sec3), abs(mkt3), 1e-9)
        if best is None or diff < best[0]:
            best = (diff, shift, mkt3)
    if best is None:
        return None
    diff, shift, mkt3 = best
    delta = dt.timedelta(days=shift)
    rows = [{"start": s.isoformat(), "end": e.isoformat(), "sec": round(v, 6), "concept": c,
             "market": round(sum(x for d, x in divs if s + delta <= d <= e + delta), 6)} for s, e, v, c in fys[-5:]]
    return {"status": "ok" if diff <= DIVIDEND_TOL else "warn", "difference_3y": round(diff, 4), "shift_days": shift,
            "sec_3y": round(sec3, 6), "market_3y": round(mkt3, 6), "years": rows, "basis": "today's shares",
            "checked_at": market._now().isoformat(timespec="seconds")}


def run_all(session_factory, now: Optional[dt.datetime] = None) -> dict:
    """The daily part: check (and repair) every tracked security; re-read a few whose last full read is the
    oldest; cross-check dividends for US filers. Each security in its own transaction."""
    with session_factory() as db:
        tickers = [s.ticker for s in market.tracked(db)]
    checked, gaps, repaired, failed = 0, [], 0, []
    for t in tickers:
        try:
            with session_factory() as db:
                r = check(db, t, now=now)
                d = dividend_check(db, t)
                if d is not None:
                    sec = db.get(Security, t)
                    sec.checks = dict(sec.checks or {}, dividends=d)
            checked += 1
            repaired += r.get("repaired", 0)
            if r.get("missing") or r.get("status") == "stale":
                gaps.append({"ticker": t, "status": r["status"], "missing": r.get("missing", 0)})
        except Exception as exc:
            failed.append({"ticker": t, "error": type(exc).__name__})
    # monthly full re-read, oldest first, a few per day
    cutoff = market._now() - dt.timedelta(days=REREAD_DAYS)
    with session_factory() as db:
        due = []
        for s in market.tracked(db):
            at = ((s.checks or {}).get("completeness") or {}).get("full_reread_at")
            if at is None or dt.datetime.fromisoformat(at) < cutoff:
                due.append((at or "", s.ticker))
    reread_done = []
    for _, t in sorted(due)[:REREAD_PER_RUN]:
        try:
            with session_factory() as db:
                r = reread(db, t, now=now)
            reread_done.append({"ticker": t, "revised": r["revised"]})
        except Exception as exc:
            failed.append({"ticker": t, "error": type(exc).__name__, "during": "reread"})
    return {"checked": checked, "repaired": repaired, "gaps": gaps, "reread": reread_done, "failed": failed}

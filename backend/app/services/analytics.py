"""Comparison chart and portfolio series from the accumulated market data (roadmap §5.4, alpha.4).

Every line is a total-return series (dividends reinvested) in the user's display currency (D7). Lines are
aligned on the days all of them traded; the window starts no earlier than the shortest history allows, and
the response says which line limited it. Metrics come from libs/sv_market (checked against a spreadsheet).
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy.orm import Session

from libs.sv_market import metrics, portfolio as pf
from libs.sv_market.series import align, convert, rebase

from ..i18n import AppError
from ..models import Portfolio, Security
from . import completeness, market

MAX_LINES = 10
RANGES = {"1y": 12, "2y": 24, "3y": 36, "5y": 60, "10y": 120}
METRIC_KEYS = ("total_return", "annualised_return", "volatility", "sharpe", "sortino", "max_drawdown", "drawdown_days",
               "calmar", "beta", "alpha", "correlation", "tracking_error", "information_ratio", "up_capture",
               "down_capture", "months_beating", "best_month", "worst_month")


def ensure(db: Session, ticker: str) -> Security:
    """Track a ticker and backfill its whole history the first time it is used (roadmap §4)."""
    sec = db.get(Security, ticker)
    if sec is None or sec.backfilled_at is None:
        market.sync(db, ticker)
        sec = db.get(Security, ticker)
    if sec is None or not sec.bars:
        raise AppError("no_prices", ticker=ticker)
    return sec


def ticker_series(db: Session, ticker: str, currency: str) -> tuple[list[tuple[dt.date, float]], Security]:
    sec = ensure(db, ticker)
    s = [(p.date, p.adj_close) for p in market.series(db, ticker)]
    src = (sec.currency or currency).upper()
    if src != currency.upper():
        pair = market.fx_symbol(src, currency)
        ensure(db, pair)
        s = convert(s, market.fx_rates(db, src, currency))
    return s, sec


def portfolio_series(db: Session, p: Portfolio, currency: str) -> tuple[list[tuple[dt.date, float]], list[Security]]:
    """Value of 1 invested at the first common date, rebalanced as the portfolio says."""
    parts, secs = {}, []
    for h in p.holdings:
        s, sec = ticker_series(db, h["ticker"], currency)
        parts[h["ticker"]] = s
        secs.append(sec)
    dates, vals = align(parts)
    if len(dates) < 2:
        raise AppError("no_common_dates")
    v = pf.values(dates, vals, {h["ticker"]: h["weight"] for h in p.holdings}, p.rebalance or "none")
    return list(zip(dates, v)), secs


def _window(dates: list[dt.date], range_: str, start: Optional[dt.date], end: Optional[dt.date]) -> tuple[dt.date, dt.date]:
    last = min(end, dates[-1]) if end else dates[-1]
    if range_ == "custom" and start:
        first = start
    elif range_ == "ytd":
        first = dt.date(last.year - 1, 12, 31)
    elif range_ in RANGES:
        months = RANGES[range_]
        y, m = last.year, last.month - months
        while m <= 0:
            y, m = y - 1, m + 12
        import calendar
        first = dt.date(y, m, min(last.day, calendar.monthrange(y, m)[1]))
    else:                                   # max
        first = dates[0]
    return first, last


def compare(db: Session, lines: list[dict], benchmark: int, range_: str = "3y", start: Optional[dt.date] = None,
            end: Optional[dt.date] = None, currency: str = "USD", rf: float = 0.0, user_id: Optional[str] = None) -> dict:
    if not 1 <= len(lines) <= MAX_LINES:
        raise AppError("compare_lines", n=MAX_LINES)
    if not 0 <= benchmark < len(lines):
        raise AppError("compare_benchmark")
    currency = currency.upper()
    raw, info, calendars = {}, [], set()
    for i, ln in enumerate(lines):
        key = f"l{i}"
        if ln.get("kind") == "portfolio":
            p = db.get(Portfolio, ln["ref"])
            if p is None or (user_id and p.user_id != user_id):
                raise AppError("portfolio_not_found")
            s, secs = portfolio_series(db, p, currency)
            info.append({"key": key, "kind": "portfolio", "ref": p.id, "label": p.name, "currency": currency,
                         "first_date": s[0][0].isoformat() if s else None})
            calendars |= {completeness.calendar_for(x) for x in secs}
        else:
            s, sec = ticker_series(db, ln["ref"], currency)
            info.append({"key": key, "kind": "ticker", "ref": sec.ticker, "label": sec.name or sec.ticker,
                         "currency": (sec.currency or currency).upper(), "first_date": s[0][0].isoformat() if s else None})
            calendars.add(completeness.calendar_for(sec))
        raw[key] = s
    all_dates, all_vals = align(raw)
    if len(all_dates) < 2:
        raise AppError("no_common_dates")
    first, last = _window(all_dates, range_, start, end)
    limited_by = None
    if first < all_dates[0]:
        limited_by = max(info, key=lambda x: x["first_date"] or "")["ref"]
        first = all_dates[0]
    idx = [i for i, d in enumerate(all_dates) if first <= d <= last]
    if len(idx) < 2:
        raise AppError("no_common_dates")
    dates = [all_dates[i] for i in idx]
    vals = {k: [v[i] for i in idx] for k, v in all_vals.items()}
    bkey = f"l{benchmark}"
    # different exchanges close at different times: daily returns are not simultaneous, so use weekly ones
    freq = "daily" if len({c for c in calendars if c}) <= 1 else "weekly"
    out_lines, out_rel, out_metrics, out_periods = [], [], [], []
    upto = [i for i, d in enumerate(all_dates) if d <= last]
    for ln in info:
        k = ln["key"]
        v = vals[k]
        out_lines.append({**ln, "values": [round(x, 6) for x in rebase(v)]})
        if k != bkey:
            out_rel.append({"key": k, "values": [round(a / b * 100 / (v[0] / vals[bkey][0]), 6) for a, b in zip(v, vals[bkey])]})
        m = metrics.summary(dates, v, rf=rf, freq=freq)
        if k != bkey:
            m.update(metrics.relative(dates, v, vals[bkey], rf=rf, freq=freq))
        else:
            m.update(metrics.monthly(dates, v))
        dd = metrics.drawdowns(dates, v)
        m.update({"drawdown_peak": dd["peak"].isoformat(), "drawdown_trough": dd["trough"].isoformat(),
                  "drawdown_recovery": dd["recovery"].isoformat() if dd["recovery"] else None})
        out_metrics.append({"key": k, **{x: m.get(x) for x in METRIC_KEYS},
                            **{x: m[x] for x in ("drawdown_peak", "drawdown_trough", "drawdown_recovery")}})
        # period returns use the whole common history up to the window end (so 5Y exists even for a 1Y chart)
        out_periods.append({"key": k, **metrics.period_returns([all_dates[i] for i in upto], [all_vals[k][i] for i in upto])})
    return {"currency": currency, "rf": rf, "frequency": freq, "benchmark": bkey,
            "window": {"start": dates[0].isoformat(), "end": dates[-1].isoformat(), "requested_start": first.isoformat()
                       if not limited_by else _window(all_dates, range_, start, end)[0].isoformat(), "limited_by": limited_by,
                       "range": range_, "days": len(dates)},
            "dates": [d.isoformat() for d in dates], "lines": out_lines, "relative": out_rel,
            "metrics": out_metrics, "periods": out_periods}

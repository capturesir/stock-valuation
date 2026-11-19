"""Portfolio optimizer service (roadmap §5.3): accumulated total-return data in the display currency (D7),
aligned on common trading days, optimised by libs/sv_market.optimize; out-of-sample check on request."""
from __future__ import annotations

import datetime as dt
import hashlib
from typing import Optional

import numpy as np
from sqlalchemy.orm import Session

from libs.sv_market import optimize as opt
from libs.sv_market.series import align

from .. import __version__
from ..i18n import AppError
from . import analytics, completeness

MAX_ASSETS = 10
MAX_SAMPLES = 50000
MIN_DAYS = 60                   # each optimisation period needs at least this many common trading days


def _fingerprint(dates: list[dt.date], tickers: list[str], prices: np.ndarray) -> str:
    """Identifies the exact data used: same fingerprint → same inputs → same result."""
    h = hashlib.sha256()
    h.update("|".join(tickers).encode())
    h.update("|".join(d.isoformat() for d in dates).encode())
    h.update(np.round(prices, 10).tobytes())
    return h.hexdigest()[:16]


def run(db: Session, assets: list[dict], objective: dict[str, float], range_: str = "5y", start: Optional[dt.date] = None,
        end: Optional[dt.date] = None, currency: str = "USD", rebalance: str = "none", rf: float = 0.0, seed: int = 42,
        samples: int = 20000, oos_fraction: float = 0.0) -> dict:
    if not 2 <= len(assets) <= MAX_ASSETS:
        raise AppError("optimize_assets", n=MAX_ASSETS)
    tickers = [a["ticker"] for a in assets]
    if len(set(tickers)) != len(tickers):
        raise AppError("optimize_duplicate")
    lo, hi = [int(a.get("min", 0)) for a in assets], [int(a.get("max", 100)) for a in assets]
    if sum(lo) > 100 or sum(hi) < 100 or any(a > b for a, b in zip(lo, hi)):
        raise AppError("optimize_bounds")
    currency = currency.upper()
    raw, cals = {}, set()
    for t in tickers:
        s, sec = analytics.ticker_series(db, t, currency)
        raw[t] = s
        cals.add(completeness.calendar_for(sec))
    all_dates, vals = align(raw)
    if len(all_dates) < 2:
        raise AppError("no_common_dates")
    first, last = analytics._window(all_dates, range_, start, end)
    limited_by = None
    if first < all_dates[0]:
        limited_by = max(tickers, key=lambda t: raw[t][0][0])
        first = all_dates[0]
    idx = [i for i, d in enumerate(all_dates) if first <= d <= last]
    dates = [all_dates[i] for i in idx]
    prices = np.array([[vals[t][i] for t in tickers] for i in idx])
    freq = "daily" if len({c for c in cals if c}) <= 1 else "weekly"
    split = None
    if oos_fraction:
        split = int(round(len(dates) * (1 - oos_fraction)))
        if split < MIN_DAYS or len(dates) - split < MIN_DAYS:
            raise AppError("optimize_too_short", n=MIN_DAYS)
    elif len(dates) < MIN_DAYS:
        raise AppError("optimize_too_short", n=MIN_DAYS)
    in_dates, in_prices = (dates[:split + 1], prices[:split + 1]) if split else (dates, prices)
    try:
        r = opt.optimise(in_dates, tickers, in_prices, lo=lo, hi=hi, objective=objective, rebalance=rebalance, rf=rf,
                         freq=freq, samples=min(samples, MAX_SAMPLES), seed=seed)
    except ValueError as exc:
        raise AppError("bad_request", detail=str(exc))
    out_of_sample = None
    if split:
        equal = {t: 100 // len(tickers) + (1 if i < 100 % len(tickers) else 0) for i, t in enumerate(tickers)}
        oos_d, oos_p = dates[split:], prices[split:]
        out_of_sample = {
            "in_sample": {"start": in_dates[0].isoformat(), "end": in_dates[-1].isoformat(), "metrics": r.metrics},
            "out_of_sample": {"start": oos_d[0].isoformat(), "end": oos_d[-1].isoformat(),
                              "metrics": opt.evaluate(oos_d, tickers, oos_p, r.weights, rebalance, rf, freq),
                              "equal_weight": opt.evaluate(oos_d, tickers, oos_p, equal, rebalance, rf, freq)},
            "fraction": oos_fraction}
    full = opt.evaluate(dates, tickers, prices, r.weights, rebalance, rf, freq)
    return {"weights": r.weights, "metrics": r.metrics, "full_period": full, "score": r.score, "breakdown": r.breakdown,
            "top": r.top, "frontier": r.frontier, "cloud": r.cloud, "assets": r.assets, "out_of_sample": out_of_sample,
            "samples": r.samples, "seed": seed, "refined": r.refined, "frequency": freq, "currency": currency,
            "rebalance": rebalance, "rf": rf, "objective": objective,
            "window": {"start": dates[0].isoformat(), "end": dates[-1].isoformat(), "days": len(dates), "range": range_,
                       "limited_by": limited_by},
            "data_version": _fingerprint(dates, tickers, prices), "system_version": __version__,
            "bounds": [{"ticker": t, "min": a, "max": b} for t, a, b in zip(tickers, lo, hi)]}

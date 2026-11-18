"""Performance metrics, following CFA Institute / industry conventions.

Conventions (all stated so results can be reproduced in a spreadsheet):
- Returns are simple returns r_t = V_t / V_{t−1} − 1 of a total-return value series (dividends reinvested).
- Annualised return is geometric: (V_end / V_start)^(365.25 / calendar days) − 1.
- Annualised volatility: sample standard deviation (n − 1) of period returns × √P, with P periods per year
  (252 for daily, 52 weekly, 12 monthly).
- Risk-free rate: a constant annual rate rf, converted per period as (1 + rf)^(1/P) − 1.
- Sharpe (ex post): mean of excess returns × P ÷ (sample stdev of excess returns × √P).
- Sortino: mean excess return × P ÷ downside deviation × √P, where downside deviation =
  √( Σ min(r_t − rf_p, 0)² / n ) over all n periods (target = the risk-free rate).
- Maximum drawdown: the largest fall from a running peak, V_t / max(V_≤t) − 1. Drawdown duration: the
  longest stretch, in calendar days, from a peak until the value regains it (or to the end if it has not).
- Calmar: annualised return ÷ |maximum drawdown|, over the whole period.
- Against a benchmark (same dates): beta = sample covariance ÷ sample variance of benchmark returns;
  Jensen's alpha (annualised) = (mean(r − rf_p) − β · mean(r_b − rf_p)) × P; correlation (Pearson);
  tracking error = stdev(r − r_b) × √P; information ratio = mean(r − r_b) × P ÷ tracking error.
- Monthly statistics use calendar-month returns: up/down capture (Morningstar): geometric mean of the
  asset's returns in months the benchmark rose (fell) ÷ the benchmark's geometric mean in those months;
  share of months beating the benchmark; best and worst month.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional, Sequence

from .series import resample, returns

PERIODS = {"daily": 252, "weekly": 52, "monthly": 12}


def _mean(x: Sequence[float]) -> float:
    return sum(x) / len(x)


def _stdev(x: Sequence[float]) -> Optional[float]:
    if len(x) < 2:
        return None
    m = _mean(x)
    return math.sqrt(sum((v - m) ** 2 for v in x) / (len(x) - 1))


def _cov(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    if len(x) < 2:
        return None
    mx, my = _mean(x), _mean(y)
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (len(x) - 1)


def total_return(values: Sequence[float]) -> Optional[float]:
    return None if len(values) < 2 else values[-1] / values[0] - 1


def annualised_return(dates: Sequence[dt.date], values: Sequence[float]) -> Optional[float]:
    if len(values) < 2 or values[0] <= 0 or values[-1] <= 0:
        return None
    days = (dates[-1] - dates[0]).days
    return None if days <= 0 else (values[-1] / values[0]) ** (365.25 / days) - 1


def drawdowns(dates: Sequence[dt.date], values: Sequence[float]) -> dict:
    """Maximum drawdown (≤ 0), its peak and trough dates, recovery date, longest duration in days, current drawdown."""
    peak_v, peak_d = values[0], dates[0]
    worst, worst_peak, worst_trough = 0.0, dates[0], dates[0]
    longest, run_start = 0, None
    for d, v in zip(dates, values):
        if v >= peak_v:
            if run_start is not None:
                longest = max(longest, (d - run_start).days)
                run_start = None
            peak_v, peak_d = v, d
            continue
        if run_start is None:
            run_start = peak_d
        dd = v / peak_v - 1
        if dd < worst:
            worst, worst_peak, worst_trough = dd, peak_d, d
    if run_start is not None:
        longest = max(longest, (dates[-1] - run_start).days)
    recovery = None
    if worst < 0:
        target = dict(zip(dates, values))[worst_peak]
        recovery = next((d for d, v in zip(dates, values) if d > worst_trough and v >= target), None)
    return {"max_drawdown": worst, "peak": worst_peak, "trough": worst_trough, "recovery": recovery,
            "longest_days": longest, "current": values[-1] / max(values) - 1}


def summary(dates: Sequence[dt.date], values: Sequence[float], rf: float = 0.0, freq: str = "daily") -> dict:
    """Stand-alone metrics of one value series."""
    p = PERIODS[freq]
    d2, v2 = resample(dates, values, freq)
    r = returns(v2)
    rf_p = (1 + rf) ** (1 / p) - 1
    ex = [x - rf_p for x in r]
    sd = _stdev(r)
    sd_ex = _stdev(ex)
    down = math.sqrt(sum(min(x, 0.0) ** 2 for x in ex) / len(ex)) if ex else None
    ann = annualised_return(dates, values)
    dd = drawdowns(dates, values)
    return {
        "total_return": total_return(values),
        "annualised_return": ann,
        "volatility": None if sd is None else sd * math.sqrt(p),
        "sharpe": None if not sd_ex else _mean(ex) * p / (sd_ex * math.sqrt(p)),
        "sortino": None if not down else _mean(ex) * p / (down * math.sqrt(p)),
        "max_drawdown": dd["max_drawdown"],
        "drawdown_days": dd["longest_days"],
        "current_drawdown": dd["current"],
        "calmar": None if ann is None or dd["max_drawdown"] == 0 else ann / abs(dd["max_drawdown"]),
        "observations": len(r),
    }


def relative(dates: Sequence[dt.date], values: Sequence[float], bench: Sequence[float], rf: float = 0.0,
             freq: str = "daily") -> dict:
    """Metrics against a benchmark over the same dates."""
    p = PERIODS[freq]
    d2, a = resample(dates, values, freq)
    _, b = resample(dates, bench, freq)
    ra, rb = returns(a), returns(b)
    rf_p = (1 + rf) ** (1 / p) - 1
    var_b = _stdev(rb)
    beta = None if not var_b else _cov(ra, rb) / var_b ** 2
    sa, sb = _stdev(ra), _stdev(rb)
    corr = None if not sa or not sb else _cov(ra, rb) / (sa * sb)
    diff = [x - y for x, y in zip(ra, rb)]
    te = _stdev(diff)
    alpha = None if beta is None else (_mean([x - rf_p for x in ra]) - beta * _mean([y - rf_p for y in rb])) * p
    out = {"beta": beta, "alpha": alpha, "correlation": corr,
           "tracking_error": None if te is None else te * math.sqrt(p),
           "information_ratio": None if not te else _mean(diff) * p / (te * math.sqrt(p))}
    out.update(monthly(dates, values, bench))
    return out


def month_returns(dates: Sequence[dt.date], values: Sequence[float]) -> list[tuple[tuple[int, int], float]]:
    md, mv = resample(dates, values, "monthly")
    return [((md[i].year, md[i].month), mv[i] / mv[i - 1] - 1) for i in range(1, len(md))]


def _geo(x: Sequence[float]) -> float:
    return math.prod(1 + v for v in x) ** (1 / len(x)) - 1


def monthly(dates: Sequence[dt.date], values: Sequence[float], bench: Optional[Sequence[float]] = None) -> dict:
    ma = month_returns(dates, values)
    out = {"best_month": max((v for _, v in ma), default=None), "worst_month": min((v for _, v in ma), default=None)}
    if bench is None:
        return out
    mb = dict(month_returns(dates, bench))
    pairs = [(v, mb[k]) for k, v in ma if k in mb]
    up = [(a, b) for a, b in pairs if b > 0]
    down = [(a, b) for a, b in pairs if b < 0]
    out["up_capture"] = None if not up or _geo([b for _, b in up]) == 0 else _geo([a for a, _ in up]) / _geo([b for _, b in up])
    out["down_capture"] = None if not down or _geo([b for _, b in down]) == 0 else _geo([a for a, _ in down]) / _geo([b for _, b in down])
    out["months_beating"] = None if not pairs else sum(1 for a, b in pairs if a > b) / len(pairs)
    return out


def period_returns(dates: Sequence[dt.date], values: Sequence[float]) -> dict[str, Optional[float]]:
    """1M, 3M, 6M, YTD, 1Y, 3Y and 5Y (annualised beyond a year). A period longer than the data is None.
    Each starts at the last value on or before the same calendar day back (month-end safe)."""
    end = dates[-1]

    def back(months: int) -> dt.date:
        y, m = end.year, end.month - months
        while m <= 0:
            y, m = y - 1, m + 12
        import calendar
        return dt.date(y, m, min(end.day, calendar.monthrange(y, m)[1]))

    def at_or_before(day: dt.date) -> Optional[int]:
        idx = [i for i, d in enumerate(dates) if d <= day]
        return idx[-1] if idx else None

    out: dict[str, Optional[float]] = {}
    for key, months in (("1m", 1), ("3m", 3), ("6m", 6), ("1y", 12), ("3y", 36), ("5y", 60)):
        start = back(months)
        i = at_or_before(start)
        if i is None or dates[0] > start:
            out[key] = None
            continue
        r = values[-1] / values[i] - 1
        out[key] = (1 + r) ** (12 / months) - 1 if months > 12 else r
    i = at_or_before(dt.date(end.year - 1, 12, 31))
    out["ytd"] = None if i is None else values[-1] / values[i] - 1
    return out

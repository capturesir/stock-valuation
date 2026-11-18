"""Portfolio value series with optional rebalancing (decision D8; default: buy and hold).

Convention: start with value 1 split by the target weights at the first date's close. With rebalancing,
holdings are reset to the target weights at the close of the first trading day of each new month,
quarter or year (the portfolio's value that day is unchanged by the reset). No costs or taxes.
"""
from __future__ import annotations

import datetime as dt
from typing import Mapping, Sequence

REBALANCE = ("none", "monthly", "quarterly", "yearly")


def _period(d: dt.date, how: str):
    if how == "monthly":
        return d.year, d.month
    if how == "quarterly":
        return d.year, (d.month - 1) // 3
    return d.year


def values(dates: Sequence[dt.date], prices: Mapping[str, Sequence[float]], weights: Mapping[str, float],
           rebalance: str = "none") -> list[float]:
    if rebalance not in REBALANCE:
        raise ValueError(f"rebalance must be one of {REBALANCE}")
    total = sum(weights.values())
    if total <= 0 or any(w < 0 for w in weights.values()):
        raise ValueError("weights must be non-negative and not all zero")
    w = {k: v / total for k, v in weights.items() if v > 0}
    units = {k: w[k] / prices[k][0] for k in w}
    out = []
    for i, d in enumerate(dates):
        v = sum(units[k] * prices[k][i] for k in w)
        out.append(v)
        if rebalance != "none" and i > 0 and _period(d, rebalance) != _period(dates[i - 1], rebalance):
            units = {k: w[k] * v / prices[k][i] for k in w}
    return out

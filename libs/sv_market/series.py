"""Series: align several total-return series on common dates, convert currencies, rebase, return."""
from __future__ import annotations

import datetime as dt
from bisect import bisect_right
from typing import Mapping, Optional, Sequence

Series = list[tuple[dt.date, float]]          # (date, value), ascending
FX_MAX_GAP = 7                                # days an FX rate may be carried forward (holidays)


def convert(series: Series, fx: Mapping[dt.date, float]) -> Series:
    """Value × FX rate of the same day, or the latest rate up to FX_MAX_GAP days before (FX holidays).
    Days without a usable rate are dropped, never guessed."""
    if not fx:
        return list(series)
    days = sorted(fx)
    out = []
    for d, v in series:
        i = bisect_right(days, d) - 1
        if i >= 0 and (d - days[i]).days <= FX_MAX_GAP:
            out.append((d, v * fx[days[i]]))
    return out


def align(series: Mapping[str, Series], start: Optional[dt.date] = None, end: Optional[dt.date] = None
          ) -> tuple[list[dt.date], dict[str, list[float]]]:
    """Common dates only: a day counts when every series has a value (different exchange holidays drop the day)."""
    maps = {k: dict(v) for k, v in series.items()}
    if not maps:
        return [], {}
    common = set.intersection(*(set(m) for m in maps.values()))
    dates = sorted(d for d in common if (start is None or d >= start) and (end is None or d <= end))
    return dates, {k: [m[d] for d in dates] for k, m in maps.items()}


def rebase(values: Sequence[float], base: float = 100.0) -> list[float]:
    return [] if not values else [v / values[0] * base for v in values]


def returns(values: Sequence[float]) -> list[float]:
    """Simple period returns r_t = V_t / V_{t-1} − 1."""
    return [values[i] / values[i - 1] - 1 for i in range(1, len(values))]


def resample(dates: Sequence[dt.date], values: Sequence[float], freq: str) -> tuple[list[dt.date], list[float]]:
    """Last value of each week (ISO week) or calendar month; the first observation is kept as the start."""
    if freq == "daily":
        return list(dates), list(values)
    key = (lambda d: d.isocalendar()[:2]) if freq == "weekly" else (lambda d: (d.year, d.month))
    out_d, out_v = [dates[0]], [values[0]]
    for i in range(1, len(dates)):
        if i + 1 == len(dates) or key(dates[i + 1]) != key(dates[i]):
            if dates[i] != out_d[-1]:
                out_d.append(dates[i])
                out_v.append(values[i])
    return out_d, out_v

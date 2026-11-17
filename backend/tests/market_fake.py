"""A deterministic stand-in for Yahoo that behaves like it: closes, volumes and dividends come back
split-adjusted, with an "Adj Close" computed independently of backend.app.services.market."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from backend.app.services.market import SourceBar, SourceHistory

END = dt.date(2026, 11, 13)          # a Friday before "today" in every test run


@dataclass
class Series:
    meta: dict
    days: list[dt.date]
    close: list[float]                 # unadjusted, as traded
    volume: list[int]
    dividends: dict[dt.date, float] = field(default_factory=dict)   # unadjusted cash per share
    splits: dict[dt.date, float] = field(default_factory=dict)


def weekdays(start: dt.date, end: dt.date) -> list[dt.date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


class FakeSource:
    name = "fake"

    def __init__(self) -> None:
        self.data: dict[str, Series] = {}
        self.fail: set[str] = set()
        self.calls: list[tuple[str, dt.date | None]] = []

    def add(self, ticker: str, start=dt.date(2024, 1, 1), end=END, base=100.0, drift=0.0005, currency="USD",
            tz="America/New_York", splits=None, dividends=None, quote_type="EQUITY") -> Series:
        days = weekdays(start, end)
        splits = dict(splits or {})
        close, level = [], base
        for i, d in enumerate(days):
            if d in splits:
                level /= splits[d]
            close.append(round(level * (1 + drift) ** i, 4))
        s = Series({"name": f"{ticker} Inc.", "exchange": "TEST", "currency": currency, "quote_type": quote_type,
                    "timezone": tz}, days, close, [1_000_000 + i for i in range(len(days))], dict(dividends or {}), splits)
        self.data[ticker] = s
        return s

    def _default(self, ticker: str) -> Series:
        if ticker.endswith("=X"):
            return self.add(ticker, base=7.8 if ticker.endswith("HKD=X") else 1.0, drift=0.0, currency=ticker[3:6],
                            quote_type="CURRENCY")
        if ticker.endswith(".HK"):
            return self.add(ticker, base=50.0, currency="HKD", tz="Asia/Hong_Kong")
        return self.add(ticker)

    def history(self, ticker: str, start: dt.date | None = None) -> SourceHistory:
        self.calls.append((ticker, start))
        if ticker in self.fail:
            raise RuntimeError(f"source down for {ticker}")
        s = self.data.get(ticker) or self._default(ticker)
        n = len(s.days)
        # split-adjust like Yahoo: factor for a day = product of splits strictly after it
        factor, f = [1.0] * n, 1.0
        for i in range(n - 1, -1, -1):
            factor[i] = f
            if s.days[i] in s.splits:
                f *= s.splits[s.days[i]]
        c = [s.close[i] / factor[i] for i in range(n)]
        div = {d: v / factor[s.days.index(d)] for d, v in s.dividends.items()}
        # Adj Close, forward formulation: each ex-date scales every earlier close by (1 - D / previous close)
        mult = [1.0] * n
        for d, v in div.items():
            k = s.days.index(d)
            m = 1 - v / c[k - 1]
            for i in range(k):
                mult[i] *= m
        bars = [SourceBar(date=s.days[i], open=c[i] * 0.99, high=c[i] * 1.01, low=c[i] * 0.98, close=c[i],
                          adj_close=c[i] * mult[i], volume=s.volume[i] * factor[i],
                          dividend=div.get(s.days[i], 0.0), split=s.splits.get(s.days[i], 0.0))
                for i in range(n) if start is None or s.days[i] >= start]
        return SourceHistory(meta=dict(s.meta), bars=bars)

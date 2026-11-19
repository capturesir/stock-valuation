"""Market data sources (moved from backend/app/services/market.py in alpha.3 step 10; behaviour unchanged).

A source returns a symbol's daily history as the source reports it; Yahoo's prices, volume and dividends
are split-adjusted. Tests use a fake source and never touch the network.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Optional, Protocol


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


NOT_FOUND = {"YFTzMissingError", "YFPricesMissingError", "YFTickerMissingError"}   # yfinance: no such symbol


class YahooSource:
    """``guard`` (optional): a context-manager factory ``guard("yahoo")`` around each request, e.g. the
    system's shared rate limit and circuit breaker; the library itself depends on nothing outside it."""
    name = "yahoo"

    def __init__(self, guard=None):
        self.guard = guard

    def history(self, ticker: str, start: Optional[dt.date] = None) -> SourceHistory:
        import contextlib

        import yfinance as yf

        t = yf.Ticker(ticker)
        kw = {"start": start.isoformat()} if start else {"period": "max"}
        try:
            with (self.guard("yahoo") if self.guard else contextlib.nullcontext()):
                df = t.history(auto_adjust=False, actions=True, raise_errors=True, **kw)
        except Exception as exc:
            if type(exc).__name__ in NOT_FOUND:
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


def fx_symbol(base: str, quote: str) -> str:
    return f"{base.upper()}{quote.upper()}=X"

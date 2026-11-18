"""Company profile for a ticker: what the user checks before valuing (name, exchange, currency …).

A ticker that resolves to nothing answers 404 with suggestions from Yahoo search, so a typo
(BRK.B instead of BRK-B, "tencent" instead of 0700.HK) is caught before any valuation runs.
The network calls are module functions so tests can replace them.
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Optional

from sqlalchemy.orm import Session

from ..models import Security

TTL_SECONDS = 600
STALE_SECONDS = 7 * 86400          # shown, labelled, when the source is down
NOT_FOUND_SECONDS = 120            # a typo does not hit Yahoo again for two minutes
SUMMARY_CHARS = 480
SEARCH_TYPES = {"EQUITY", "ETF", "INDEX", "MUTUALFUND"}


class NotFound(Exception):
    def __init__(self, ticker: str, suggestions: list[dict]):
        super().__init__(ticker)
        self.ticker, self.suggestions = ticker, suggestions


class Unavailable(Exception):
    """The source could not be asked (paused after failures, rate limited, network down)."""

    def __init__(self, reason: str, retry_after: float = 60):
        super().__init__(reason)
        self.reason, self.retry_after = reason, retry_after


def fetch_quote(ticker: str) -> Optional[dict]:
    """Last close and chart metadata; None when the symbol does not exist; Unavailable when the
    source could not answer (so an outage is never reported as "ticker not found")."""
    from src.fetch import guard, yahoo

    ch = yahoo.chart_quote(ticker)
    if ch.get("ok") and ch.get("last"):
        return {"meta": ch.get("meta") or {}, "last": ch["last"]}
    status = ch.get("http_status")
    if status in guard.FAILURE_STATUS or status is None:
        st = guard.status().get("yahoo") or {}
        raise Unavailable(ch.get("error") or "Yahoo 暫時無法連線", st.get("retry_after") or 60)
    return None


def fetch_info(ticker: str) -> dict:
    try:
        import yfinance as yf

        from src.fetch import guard
        with guard.call("yahoo"):
            return yf.Ticker(ticker).info or {}
    except Exception:
        return {}


def search(query: str, limit: int = 6) -> list[dict]:
    from src.fetch import httputil              # paced and circuit-broken like every Yahoo call

    url = ("https://query2.finance.yahoo.com/v1/finance/search?"
           + urllib.parse.urlencode({"q": query, "quotesCount": 10, "newsCount": 0}))
    try:
        status, data = httputil.get_json(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    except Exception:
        return []
    if status != 200 or not isinstance(data, dict):
        return []
    out = []
    for q in data.get("quotes") or []:
        if q.get("quoteType") in SEARCH_TYPES and q.get("symbol"):
            out.append({"ticker": q["symbol"], "name": q.get("longname") or q.get("shortname"),
                        "exchange": q.get("exchDisp"), "type": q.get("quoteType")})
    return out[:limit]


def suggestions_for(ticker: str) -> list[dict]:
    found: list[dict] = []
    # US share classes are written with a dot elsewhere (BRK.B) but with a dash on Yahoo (BRK-B)
    m = re.fullmatch(r"([A-Z]{1,5})\.([A-Z])", ticker)
    if m:
        dash = f"{m.group(1)}-{m.group(2)}"
        q = fetch_quote(dash)
        if q:
            found.append({"ticker": dash, "name": q["meta"].get("longName") or q["meta"].get("shortName"),
                          "exchange": q["meta"].get("exchangeName"), "type": q["meta"].get("instrumentType")})
    query = ticker[:-3] if ticker.endswith(".HK") else ticker
    for s in search(query):
        if s["ticker"] not in {f["ticker"] for f in found}:
            found.append(s)
    return found[:6]


def _build(ticker: str) -> dict:
    quote = fetch_quote(ticker)
    if quote is None:
        return {"_not_found": True, "suggestions": suggestions_for(ticker)}
    info = fetch_info(ticker)
    meta, last = quote["meta"], quote["last"]
    summary = (info.get("longBusinessSummary") or "").strip()
    if len(summary) > SUMMARY_CHARS:
        summary = summary[:SUMMARY_CHARS].rsplit(" ", 1)[0] + "…"
    return {
        "ticker": ticker,
        "name": info.get("longName") or info.get("shortName") or meta.get("longName") or meta.get("shortName"),
        "exchange": info.get("fullExchangeName") or meta.get("fullExchangeName") or meta.get("exchangeName"),
        "currency": info.get("currency") or meta.get("currency"),
        "financial_currency": info.get("financialCurrency"),
        "quote_type": info.get("quoteType") or meta.get("instrumentType"),
        "sector": info.get("sector"), "industry": info.get("industry"), "country": info.get("country"),
        "market_cap": info.get("marketCap"),
        "price": last.get("close"), "price_date": last.get("date"),
        "week52_high": info.get("fiftyTwoWeekHigh") or meta.get("fiftyTwoWeekHigh"),
        "week52_low": info.get("fiftyTwoWeekLow") or meta.get("fiftyTwoWeekLow"),
        "website": info.get("website"), "summary": summary or None,
        "source": "Yahoo Finance",
    }


def profile(db: Session, ticker: str, has_name_spec: bool = False, valued: bool = False) -> dict:
    """Shared, single-flight cache: users asking for the same ticker at once cause one upstream call."""
    from .. import cache

    key = f"profile:{ticker}"
    try:
        out = cache.get(key)
        if out is None:
            out = _build_once(key, ticker)
    except Unavailable:
        stale = cache.get_stale(key)
        if stale is None:
            raise
        out = dict(stale["value"], stale=True, as_of=stale["at"])
    if out.get("_not_found"):
        raise NotFound(ticker, out.get("suggestions") or [])
    sec = db.get(Security, ticker)
    if sec is not None:                      # keep the securities master in step
        for k, v in (("name", out.get("name")), ("exchange", out.get("exchange")), ("currency", out.get("currency")),
                     ("quote_type", out.get("quote_type"))):
            if v and not getattr(sec, k):
                setattr(sec, k, v)
    return dict(out, has_name_spec=has_name_spec, valued=valued)


def _build_once(key: str, ticker: str) -> dict:
    from .. import cache

    def compute():
        return _build(ticker)
    value = cache.single_flight(key, compute, ttl=TTL_SECONDS, stale_ttl=STALE_SECONDS)
    if value.get("_not_found"):              # shorter memory for "not found", and never a stale copy of it
        cache.put(key, value, ttl=NOT_FOUND_SECONDS)
    return value

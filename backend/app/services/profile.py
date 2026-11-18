"""Company profile for a ticker: what the user checks before valuing (name, exchange, currency …).

A ticker that resolves to nothing answers 404 with suggestions from Yahoo search, so a typo
(BRK.B instead of BRK-B, "tencent" instead of 0700.HK) is caught before any valuation runs.
The network calls are module functions so tests can replace them.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from typing import Optional

from sqlalchemy.orm import Session

from ..models import Security

TTL_SECONDS = 600
SUMMARY_CHARS = 480
SEARCH_TYPES = {"EQUITY", "ETF", "INDEX", "MUTUALFUND"}
_cache: dict[str, tuple[float, dict]] = {}


class NotFound(Exception):
    def __init__(self, ticker: str, suggestions: list[dict]):
        super().__init__(ticker)
        self.ticker, self.suggestions = ticker, suggestions


def fetch_quote(ticker: str) -> Optional[dict]:
    """Last close and chart metadata, or None when the symbol does not exist."""
    from src.fetch import yahoo

    ch = yahoo.chart_quote(ticker)
    if not ch.get("ok") or not ch.get("last"):
        return None
    return {"meta": ch.get("meta") or {}, "last": ch["last"]}


def fetch_info(ticker: str) -> dict:
    try:
        import yfinance as yf
        return yf.Ticker(ticker).info or {}
    except Exception:
        return {}


def search(query: str, limit: int = 6) -> list[dict]:
    url = ("https://query2.finance.yahoo.com/v1/finance/search?"
           + urllib.parse.urlencode({"q": query, "quotesCount": 10, "newsCount": 0}))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
    except Exception:
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


def profile(db: Session, ticker: str, has_name_spec: bool = False, valued: bool = False) -> dict:
    hit = _cache.get(ticker)
    if hit and time.time() - hit[0] < TTL_SECONDS:
        return dict(hit[1], has_name_spec=has_name_spec, valued=valued)
    quote = fetch_quote(ticker)
    if quote is None:
        raise NotFound(ticker, suggestions_for(ticker))
    info = fetch_info(ticker)
    meta, last = quote["meta"], quote["last"]
    summary = (info.get("longBusinessSummary") or "").strip()
    if len(summary) > SUMMARY_CHARS:
        summary = summary[:SUMMARY_CHARS].rsplit(" ", 1)[0] + "…"
    out = {
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
    _cache[ticker] = (time.time(), out)
    sec = db.get(Security, ticker)
    if sec is not None:                      # keep the securities master in step
        for k, v in (("name", out["name"]), ("exchange", out["exchange"]), ("currency", out["currency"]),
                     ("quote_type", out["quote_type"])):
            if v and not getattr(sec, k):
                setattr(sec, k, v)
    return dict(out, has_name_spec=has_name_spec, valued=valued)

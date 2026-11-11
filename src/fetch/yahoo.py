"""Yahoo Finance: prices, statements, consensus, targets, beta, shares."""
from __future__ import annotations

import datetime as dt
import json
import math
from typing import Any, Optional

from . import httputil


def chart_quote(ticker: str, days: int = 15) -> dict:
    end = dt.datetime.now() + dt.timedelta(days=1)
    start = end - dt.timedelta(days=max(days, 10))
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?period1={int(start.timestamp())}&period2={int(end.timestamp())}&interval=1d&events=div,split")
    status, data = httputil.get_json(url, headers={"User-Agent": "Mozilla/5.0"})
    out = {"url": url, "http_status": status, "ok": False}
    if status != 200 or not data or "chart" not in data:
        out["error"] = f"Yahoo chart HTTP {status}"
        return out
    res = (data.get("chart") or {}).get("result") or []
    if not res:
        out["error"] = "Yahoo chart 無結果"
        return out
    r = res[0]
    meta = r.get("meta") or {}
    off = meta.get("gmtoffset") or 0
    ts = r.get("timestamp") or []
    closes = ((r.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    pts = []
    for a, c in zip(ts, closes):
        if c is None:
            continue
        d = dt.datetime.utcfromtimestamp(a + off).date().isoformat()
        pts.append({"date": d, "close": float(c)})
    # fill last session from meta if needed
    rmt, rmp = meta.get("regularMarketTime"), meta.get("regularMarketPrice")
    if rmt and rmp is not None:
        d_rm = dt.datetime.utcfromtimestamp(rmt + off).date().isoformat()
        if not pts or d_rm > pts[-1]["date"]:
            pts.append({"date": d_rm, "close": float(rmp), "from_meta": True})
    out.update(ok=True, meta={"currency": meta.get("currency"), "exchange": meta.get("exchangeName"),
                              "symbol": meta.get("symbol"), "regularMarketPrice": meta.get("regularMarketPrice"),
                              "regularMarketTime": meta.get("regularMarketTime"),
                              "gmtoffset": off, "shortName": meta.get("shortName"),
                              "instrumentType": meta.get("instrumentType")},
               closes=pts, last=pts[-1] if pts else None)
    return out


def _df2(df) -> dict:
    if df is None or len(df) == 0:
        return {}
    out = {}
    for c in df.columns:
        k = str(c.date()) if hasattr(c, "date") else str(c)
        out[k] = {str(i): (None if (v is None or (isinstance(v, float) and math.isnan(v))) else float(v))
                  for i, v in df[c].items()}
    return out


def _tab(df) -> dict:
    if df is None or len(df) == 0:
        return {}
    df = df.copy()
    df.index = [str(i) for i in df.index]
    return json.loads(df.to_json(orient="index"))


def fundamentals(ticker: str) -> dict:
    import yfinance as yf
    T = yf.Ticker(ticker)
    o: dict[str, Any] = {
        "ticker": ticker,
        "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source": "Yahoo Finance via yfinance",
        "urls": {k: f"https://finance.yahoo.com/quote/{ticker}/{p}" for k, p in
                 [("financials", "financials"), ("balance", "balance-sheet"), ("cashflow", "cash-flow"),
                  ("stats", "key-statistics"), ("analysis", "analysis"), ("profile", "profile")]},
        "quote_url": f"https://finance.yahoo.com/quote/{ticker}",
    }
    try:
        o["info"] = T.info or {}
    except Exception as e:
        o["info"] = {}
        o["info_error"] = str(e)
    for name, attr in [("is_a", "income_stmt"), ("is_q", "quarterly_income_stmt"),
                       ("bs_a", "balance_sheet"), ("bs_q", "quarterly_balance_sheet"),
                       ("cf_a", "cashflow"), ("cf_q", "quarterly_cashflow")]:
        try:
            o[name] = _df2(getattr(T, attr))
        except Exception as e:
            o[name] = {"error": str(e)}
    for name, attr in [("rev_est", "revenue_estimate"), ("eps_est", "earnings_estimate"),
                       ("growth_est", "growth_estimates")]:
        try:
            o[name] = _tab(getattr(T, attr))
        except Exception as e:
            o[name] = {"error": str(e)}
    try:
        apt = getattr(T, "analyst_price_targets", None)
        o["analyst_price_targets"] = apt if isinstance(apt, dict) else (apt if apt is None else str(apt))
    except Exception as e:
        o["analyst_price_targets_error"] = str(e)
    try:
        rec = T.recommendations
        o["recommendations"] = rec.to_dict(orient="records") if rec is not None and len(rec) else []
    except Exception as e:
        o["recommendations_error"] = str(e)
    try:
        sh = T.get_shares_full(start="2016-01-01")
        hist = {}
        if sh is not None and len(sh):
            if hasattr(sh, "iloc"):
                col = sh.iloc[:, 0] if getattr(sh, "shape", (0, 0))[1] else sh
                for i, v in col.items():
                    hist[str(i.date() if hasattr(i, "date") else i)] = float(v)
            elif hasattr(sh, "items"):
                for k, v in sh.items():
                    hist[str(k)] = float(v)
        o["shares_history"] = hist
    except Exception as e:
        o["shares_history"] = {}
        o["shares_history_error"] = str(e)
    return o


def statement_cell(block: dict, date: str, *keys) -> Optional[float]:
    row = (block or {}).get(date) or {}
    for k in keys:
        if k in row and row[k] is not None:
            return float(row[k])
    return None


def latest_dates(block: dict, n: int = 8) -> list[str]:
    if not block or not isinstance(block, dict) or "error" in block:
        return []
    return list(block.keys())[:n]

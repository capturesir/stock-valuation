"""Lookup, prior-valuation discovery, and filings status — no valuation until asked."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from ..fetch import yahoo, httputil
from ..pipeline.orchestrator import ROOT, _norm

KNOWN_SPECS = frozenset({"NVDA", "1830.HK"})
DATE_DIR_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(.+)$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def norm_ticker(ticker: str) -> str:
    return _norm(ticker or "")


def has_name_spec(ticker: str) -> bool:
    return norm_ticker(ticker) in KNOWN_SPECS


def validate_date(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if not ISO_DATE_RE.fullmatch(text):
        raise ValueError("估值日須為 YYYY-MM-DD")
    return text


def _safe_out_path(path: Path) -> Path:
    root = (ROOT / "out").resolve()
    resolved = path.resolve()
    if root not in resolved.parents and resolved != root:
        raise PermissionError("路徑不在 out/ 目錄內")
    return resolved


def find_valuations(ticker: str) -> list[dict]:
    ticker = norm_ticker(ticker)
    out_root = ROOT / "out"
    items = []
    if not out_root.is_dir():
        return items
    for folder in out_root.iterdir():
        if not folder.is_dir():
            continue
        m = DATE_DIR_RE.match(folder.name)
        if not m or m.group(2) != ticker:
            continue
        date = m.group(1)
        xlsx = folder / f"{date}_{ticker}_FCFF.xlsx"
        if not xlsx.is_file():
            found = list(folder.glob("*_FCFF.xlsx"))
            xlsx = found[0] if found else None
        js = folder / f"{date}_{ticker}.json"
        md = folder / f"{date}_{ticker}_valuation.md"
        if not md.is_file():
            found_md = list(folder.glob("*_valuation.md"))
            md = found_md[0] if found_md else None
        meta = {}
        mp = folder / "run_meta.json"
        if mp.is_file():
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                meta = {}
        items.append({
            "ticker": ticker,
            "valuation_date": date,
            "folder": str(folder),
            "xlsx": str(xlsx) if xlsx and xlsx.is_file() else None,
            "json": str(js) if js.is_file() else None,
            "md": str(md) if md and md.is_file() else None,
            "company": meta.get("company"),
            "price": meta.get("price"),
            "price_date": meta.get("price_date"),
            "fair_value_per_share": meta.get("fair_value_per_share"),
            "wacc": meta.get("wacc"),
            "g": meta.get("g"),
            "verify_pass": meta.get("verify_pass"),
            "assumption_source": meta.get("assumption_source"),
        })
    items.sort(key=lambda x: x["valuation_date"], reverse=True)
    return items


def latest_valuation(ticker: str) -> Optional[dict]:
    items = find_valuations(ticker)
    return items[0] if items else None


def resolve_workbook(ticker: str, valuation_date: Optional[str] = None) -> dict:
    ticker = norm_ticker(ticker)
    date = validate_date(valuation_date)
    if date:
        folder = ROOT / "out" / f"{date}_{ticker}"
        xlsx = folder / f"{date}_{ticker}_FCFF.xlsx"
        js = folder / f"{date}_{ticker}.json"
        md = folder / f"{date}_{ticker}_valuation.md"
        if not md.is_file():
            found_md = list(folder.glob("*_valuation.md"))
            md = found_md[0] if found_md else None
        if not xlsx.is_file():
            raise FileNotFoundError(f"找不到 {ticker} 於 {date} 的 Excel（{xlsx.name}）")
        _safe_out_path(xlsx)
        rec = {"ticker": ticker, "valuation_date": date, "xlsx": str(xlsx),
               "json": str(js) if js.is_file() else None,
               "md": str(md) if md and md.is_file() else None}
        mp = folder / "run_meta.json"
        if mp.is_file():
            rec.update({k: v for k, v in json.loads(mp.read_text(encoding="utf-8")).items()
                        if k in ("company", "price", "price_date", "fair_value_per_share",
                                 "wacc", "g", "verify_pass", "assumption_source")})
        return rec
    last = latest_valuation(ticker)
    if not last or not last.get("xlsx"):
        raise FileNotFoundError(f"尚未找到 {ticker} 的估值 Excel")
    return last


def filings_status(result: dict) -> str:
    if result.get("rerun_needed"):
        return "RERUN_NEEDED"
    if result.get("blocked") or result.get("error"):
        return "CHECK_BLOCKED_OR_ERROR"
    return "NO_RERUN"


def _enrich_profile(ticker: str) -> dict:
    """Optional extra fields from Yahoo quote; never invent if the call fails."""
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={ticker}"
    try:
        status, data = httputil.get_json(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
    except Exception:
        return {}
    if status != 200 or not data:
        return {}
    result = ((data.get("quoteResponse") or {}).get("result") or [])
    if not result:
        return {}
    q = result[0] or {}
    out = {}
    for src, dest in [
        ("longName", "long_name"),
        ("shortName", "short_name"),
        ("fullExchangeName", "exchange"),
        ("exchange", "exchange_code"),
        ("currency", "currency"),
        ("marketCap", "market_cap"),
        ("quoteType", "instrument_type"),
        ("sector", "sector"),
        ("industry", "industry"),
        ("fiftyTwoWeekHigh", "week52_high"),
        ("fiftyTwoWeekLow", "week52_low"),
    ]:
        if q.get(src) not in (None, ""):
            out[dest] = q[src]
    return out


def lookup_ticker(ticker: str) -> dict:
    ticker = norm_ticker(ticker)
    if not ticker:
        return {"ok": False, "ticker": ticker, "error": "請輸入股票代號。"}
    ch = yahoo.chart_quote(ticker)
    if not ch.get("ok") or not ch.get("last"):
        err = ch.get("error") or "Yahoo 無結果"
        return {
            "ok": False,
            "ticker": ticker,
            "error": f"找不到「{ticker}」或無法取得市價：{err}",
            "chart": {"ok": False, "http_status": ch.get("http_status"), "error": ch.get("error")},
        }
    meta = ch.get("meta") or {}
    last = ch["last"]
    quote = {
        "ticker": ticker,
        "symbol": meta.get("symbol") or ticker,
        "short_name": meta.get("shortName"),
        "long_name": None,
        "currency": meta.get("currency"),
        "exchange": meta.get("exchange"),
        "instrument_type": meta.get("instrumentType"),
        "price": last.get("close"),
        "price_date": last.get("date"),
        "price_url": f"https://finance.yahoo.com/quote/{ticker}",
    }
    extra = _enrich_profile(ticker)
    if extra.get("long_name") and not quote.get("long_name"):
        quote["long_name"] = extra["long_name"]
    if extra.get("short_name") and not quote.get("short_name"):
        quote["short_name"] = extra["short_name"]
    if extra.get("exchange"):
        quote["exchange"] = extra["exchange"]
    if extra.get("currency") and not quote.get("currency"):
        quote["currency"] = extra["currency"]
    if extra.get("instrument_type") and not quote.get("instrument_type"):
        quote["instrument_type"] = extra["instrument_type"]
    for k in ("market_cap", "sector", "industry", "week52_high", "week52_low", "exchange_code"):
        if k in extra:
            quote[k] = extra[k]
    display_name = quote.get("long_name") or quote.get("short_name") or ticker
    return {
        "ok": True,
        "ticker": ticker,
        "display_name": display_name,
        "quote": quote,
        "has_name_spec": has_name_spec(ticker),
        "last_valuation": latest_valuation(ticker),
    }

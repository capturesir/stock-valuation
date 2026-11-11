"""SEC EDGAR: submissions JSON (works) and HTML archives (often 403/Cloudflare)."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from . import httputil

UA = "ValuationSystem research@localhost"


def cik_from_ticker(ticker: str, tickers_json: Optional[dict] = None) -> Optional[str]:
    # common known + company_tickers lookup if provided
    known = {"NVDA": "0001045810", "GOOGL": "0001652044", "AAPL": "0000320193"}
    if ticker.upper() in known:
        return known[ticker.upper()]
    return None


def submissions(cik: str) -> dict:
    cik = str(cik).zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    status, data = httputil.get_json(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=30)
    out = {"ok": status == 200 and isinstance(data, dict), "url": url, "http_status": status, "cik": cik}
    if not out["ok"]:
        out["error"] = f"SEC submissions HTTP {status}"
        return out
    rf = (data.get("filings") or {}).get("recent") or {}
    forms = rf.get("form") or []
    n = len(forms)
    recs = []
    for i in range(n):
        recs.append({
            "form": forms[i],
            "filingDate": (rf.get("filingDate") or [None])[i] if i < len(rf.get("filingDate") or []) else None,
            "reportDate": (rf.get("reportDate") or [None])[i] if i < len(rf.get("reportDate") or []) else None,
            "accession": (rf.get("accessionNumber") or [None])[i] if i < len(rf.get("accessionNumber") or []) else None,
            "primary": (rf.get("primaryDocument") or [None])[i] if i < len(rf.get("primaryDocument") or []) else None,
            "desc": (rf.get("primaryDocDescription") or [None])[i] if i < len(rf.get("primaryDocDescription") or []) else None,
            "items": (rf.get("items") or [None])[i] if i < len(rf.get("items") or []) else None,
        })
    out.update(name=data.get("name"), tickers=data.get("tickers"), sic=data.get("sic"),
               sicDescription=data.get("sicDescription"), fiscalYearEnd=data.get("fiscalYearEnd"),
               filings=recs)
    return out


def latest_results_filing(sub: dict) -> Optional[dict]:
    """Latest 10-K, 10-Q, or earnings 8-K (item 2.02)."""
    recs = sub.get("filings") or []
    for r in recs:
        if r.get("form") in ("10-K", "10-Q"):
            return {**r, "kind": r["form"]}
        items = (r.get("items") or "")
        if r.get("form") in ("8-K", "8-K/A") and "2.02" in items:
            return {**r, "kind": "8-K-earnings"}
    return None


def archive_url(cik: str, accession: str, primary: str) -> str:
    accdir = accession.replace("-", "")
    numeric = str(int(cik))
    return f"https://www.sec.gov/Archives/edgar/data/{numeric}/{accdir}/{primary}"


def try_fetch_html(url: str) -> dict:
    status, body, _ = httputil.get(url, headers={"User-Agent": UA}, timeout=25, retries=1)
    return {"url": url, "http_status": status, "ok": status == 200 and body and len(body) > 1000,
            "bytes": len(body or b""),
            "error": None if status == 200 else f"EDGAR HTML HTTP {status}（常見為 Cloudflare／403；改用 data.sec.gov JSON 或 Yahoo／IR）"}

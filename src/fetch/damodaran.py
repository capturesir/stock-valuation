"""Damodaran industry classification and beta lookup from local or downloaded files."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

DATA = Path(__file__).resolve().parents[2] / "data" / "damodaran"
BETA_XLS = DATA / "betaGlobal.xls"
U_BETA = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betaGlobal.xls"
U_IND = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/indname.xls"
FILE_DATE = "2026-01-05"  # Date updated in betaGlobal.xls Industry Averages row 0


def load_industry_betas(path: Optional[Path] = None) -> dict:
    import pandas as pd
    path = path or BETA_XLS
    df = pd.read_excel(path, sheet_name="Industry Averages", header=9)
    out = {}
    for _, r in df.iterrows():
        name = str(r["Industry Name"]).strip()
        if not name or name.lower() == "nan":
            continue
        out[name] = {
            "n_firms": r["Number of firms"],
            "beta_u_cash": float(r["Unlevered beta corrected for cash"]),
            "beta_u_avg_2020_24": float(r["Average (2020-24)"]),
            "beta_u_2025": float(r["2025"]) if r["2025"] == r["2025"] else None,
            "source": "Damodaran betaGlobal.xls, sheet Industry Averages, Date updated 2026-01-05",
            "url": U_BETA,
            "data_date": FILE_DATE,
        }
    return out


def lookup_company_industry(ticker: str, company_hint: str = "", indname_path: Optional[Path] = None) -> dict:
    """Search indname.xls if present; otherwise return not_found.

    ticker examples: 'NVDA', 'SEHK:1830', '1830.HK'
    """
    path = indname_path or (DATA / "indname.xls")
    out = {"ok": False, "ticker": ticker, "url": U_IND, "data_date": FILE_DATE}
    if not path.exists():
        out["error"] = f"本機沒有 indname.xls（可從 {U_IND} 下載放到 data/damodaran/）"
        return out
    import pandas as pd
    df = pd.read_excel(path, sheet_name="By industry", header=0)
    # columns: Company Name, Exchange:Ticker, Industry Group, ...
    tickers_col = df.iloc[:, 1].astype(str)
    names_col = df.iloc[:, 0].astype(str)
    want = ticker.upper().replace(".HK", "").lstrip("0")
    aliases = {ticker.upper(), ticker.replace(".HK", ""), "SEHK:" + ticker.replace(".HK", "").zfill(4) if ticker.endswith(".HK") or ticker[-1].isdigit() else "",
               "NASDAQGS:" + ticker.upper(), "NASDAQ:" + ticker.upper()}
    aliases = {a for a in aliases if a}
    hits = []
    for i, row in df.iterrows():
        ex_t = str(row.iloc[1])
        name = str(row.iloc[0])
        if any(a.lower() in ex_t.lower() or a.lower() in name.lower() for a in aliases):
            hits.append({
                "company": name, "exchange_ticker": ex_t, "industry": str(row.iloc[2]),
                "sector": str(row.iloc[3]), "sic": str(row.iloc[4]), "country": str(row.iloc[5]),
                "broad_group": str(row.iloc[6]) if len(row) > 6 else None,
                "sub_group": str(row.iloc[7]) if len(row) > 7 else None,
            })
    # tighter filter for 1830 vs SASE:1830
    if ticker.endswith(".HK") or ticker.replace(".", "").isdigit():
        code = ticker.replace(".HK", "").zfill(4)
        hits = [h for h in hits if f"SEHK:{code.lstrip('0')}" in h["exchange_ticker"].replace("01830", "1830")
                or f"SEHK:{code}" in h["exchange_ticker"] or f"SEHK:1830" in h["exchange_ticker"] and "Perfect Medical Health" in h["company"]]
    if ticker.upper() == "NVDA":
        hits = [h for h in hits if "NVIDIA" in h["company"].upper() and "NVDA" in h["exchange_ticker"].upper()]
    if not hits:
        out["error"] = "indname.xls 沒有精確匹配"
        return out
    out.update(ok=True, matches=hits, industry=hits[0]["industry"], company=hits[0]["company"])
    return out


# ---------------------------------------------------------------- implied ERP, refreshed monthly
#
# Method A uses Damodaran's implied ERP for the month of the valuation date. The value used to be a
# constant edited by hand (still 2026-10 in November); it is now read from a dated history: the
# file shipped in data/damodaran/ plus anything fetched at run time (saved under APP_DATA_DIR).

import datetime as _dt
import json as _json
import os as _os
import re as _re

U_HOME = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/home.htm"
ERP_SEED = DATA / "erp_history.json"
_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"], start=1)}


def _runtime_file() -> Path:
    base = Path(_os.environ.get("APP_DATA_DIR") or (Path(__file__).resolve().parents[2] / "var"))
    return base / "damodaran" / "erp_history.json"


def erp_history() -> list[dict]:
    """All known monthly entries, oldest first (runtime entries override the shipped ones)."""
    by_date: dict[str, dict] = {}
    for path in (ERP_SEED, _runtime_file()):
        try:
            for e in _json.loads(path.read_text(encoding="utf-8")).get("entries", []):
                by_date[e["date"]] = e
        except (OSError, ValueError, KeyError):
            continue
    return [by_date[k] for k in sorted(by_date)]


def erp_for(valuation_date: str) -> Optional[dict]:
    """The latest entry dated on or before the valuation date, with ``stale`` = it is from an earlier month."""
    vd = str(valuation_date)[:10]
    cands = [e for e in erp_history() if e["date"] <= vd]
    if not cands:
        return None
    e = dict(cands[-1])
    e["stale"] = e["date"][:7] < vd[:7]
    return e


def parse_implied_erp(html_text: str) -> Optional[dict]:
    """'Implied ERP on November 1, 2026 = 4.41 % (Trailing 12 month, with adjusted payout) … US treasury
    rate of 4.94%' -> {date, erp, rf}."""
    m = _re.search(r"Implied ERP on\s+([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*%", html_text)
    if not m or m.group(1).lower() not in _MONTHS:
        return None
    date = _dt.date(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2))).isoformat()
    rf = _re.search(r"treasury rate of\s+([0-9]+(?:\.[0-9]+)?)\s*%", html_text[m.end():m.end() + 1200])
    return {"date": date, "erp": round(float(m.group(4)) / 100, 6), "rf": round(float(rf.group(1)) / 100, 6) if rf else None}


def refresh_erp(timeout: int = 25) -> dict:
    """Fetch the current month's implied ERP and save it; returns {"ok", "entry"|"error"}."""
    from bs4 import BeautifulSoup
    from . import httputil

    try:
        status, body, _ = httputil.get(U_HOME, timeout=timeout)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if status != 200 or not body:
        return {"ok": False, "error": f"HTTP {status}"}
    text = BeautifulSoup(body.decode("latin-1", errors="replace"), "lxml").get_text(" ", strip=True)
    parsed = parse_implied_erp(text)
    if not parsed:
        return {"ok": False, "error": "Damodaran 首頁格式改變，找不到 Implied ERP"}
    entry = {**parsed, "source": "Damodaran Online（Implied ERP）", "url": U_HOME,
             "recorded": _dt.date.today().isoformat()}
    path = _runtime_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = _json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"entries": []}
        data["entries"] = [e for e in data.get("entries", []) if e.get("date") != entry["date"]] + [entry]
        path.write_text(_json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as exc:
        return {"ok": True, "entry": entry, "warning": f"未能保存：{exc}"}
    return {"ok": True, "entry": entry}


def ensure_erp(valuation_date: str) -> Optional[dict]:
    """Fetch only when the history has nothing for the valuation month yet."""
    e = erp_for(valuation_date)
    if e is None or e["stale"]:
        refresh_erp()
        e = erp_for(valuation_date)
    return e

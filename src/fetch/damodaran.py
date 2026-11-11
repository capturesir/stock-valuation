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

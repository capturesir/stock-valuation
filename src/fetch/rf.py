"""Risk-free rates: UST 10y (^TNX) and Hong Kong 10y (ADB, Trading Economics)."""
from __future__ import annotations

import datetime as dt
import re
from typing import Optional

from . import httputil, yahoo
from bs4 import BeautifulSoup


def ust_10y() -> dict:
    ch = yahoo.chart_quote("^TNX", days=10)
    out = {"source": "Yahoo Finance ^TNX", "url": "https://finance.yahoo.com/quote/%5ETNX",
           "ok": False, "chart": ch}
    if not ch.get("ok") or not ch.get("last"):
        out["error"] = ch.get("error") or "未取得 ^TNX"
        return out
    last = ch["last"]
    # ^TNX is quoted as 4.983 meaning 4.983%
    out.update(ok=True, value=float(last["close"]) / 100.0, data_date=last["date"],
               basis=f"市場數據：美國 10 年期國債 Yahoo ^TNX 收市 {last['close']:.3f}%（{last['date']}）")
    return out


def hk_10y(cfg: Optional[dict] = None) -> dict:
    """Prefer ADB AsianBondsOnline official table; TE as cross-check. Investing.com often 403."""
    cfg = cfg or {}
    mkt = ((cfg.get("markets") or {}).get("hk") or {})
    adb_url = mkt.get("adb_url") or "https://asianbondsonline.adb.org/hongkong/"
    te_url = mkt.get("te_url") or "https://tradingeconomics.com/hong-kong/government-bond-yield"
    inv_url = mkt.get("investing_url") or "https://www.investing.com/rates-bonds/hong-kong-10-year-bond-yield"
    attempts = []
    adb = _adb(adb_url)
    attempts.append(adb)
    te = _trading_economics(te_url)
    attempts.append(te)
    inv = _investing(inv_url)
    attempts.append(inv)
    primary = next((a for a in (adb, te, inv) if a.get("ok")), None)
    out = {"ok": bool(primary), "attempts": attempts, "cross_checks": [a for a in attempts if a is not primary]}
    if not primary:
        out["error"] = "未能取得港元 10 年期公債孳息（已試 ADB、Trading Economics、Investing.com）"
        return out
    out.update({k: primary[k] for k in ("value", "data_date", "source", "url", "basis")})
    return out


def _adb(url: str) -> dict:
    try:
        status, body, _ = httputil.get(url, timeout=25)
    except Exception as e:
        return {"ok": False, "source": "ADB AsianBondsOnline", "url": url, "error": str(e)}
    if status != 200 or not body:
        return {"ok": False, "source": "ADB AsianBondsOnline", "url": url, "error": f"HTTP {status}"}
    soup = BeautifulSoup(body, "lxml")
    # Close of November 9, 2026
    text = soup.get_text("\n", strip=True)
    close_m = re.search(r"Close of ([A-Za-z]+ \d{1,2}, \d{4})", text)
    data_date = None
    if close_m:
        data_date = dt.datetime.strptime(close_m.group(1), "%B %d, %Y").date().isoformat()
    ten = None
    for tbl in soup.find_all("table"):
        rows = [[c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])] for tr in tbl.find_all("tr")]
        for row in rows:
            if row and row[0].strip().lower().startswith("10 year") and len(row) >= 2:
                try:
                    ten = float(row[1].replace(",", ""))
                except ValueError:
                    ten = None
    if ten is None:
        return {"ok": False, "source": "ADB AsianBondsOnline", "url": url, "error": "頁面無 10 Year 列", "http_status": status}
    return {"ok": True, "source": "ADB AsianBondsOnline", "url": url, "value": ten / 100.0,
            "data_date": data_date, "http_status": status,
            "basis": f"市場數據：ADB AsianBondsOnline 香港 10 年期政府債券 Latest Yield {ten:.3f}%（Close of {data_date or '未標日期'}）"}


def _trading_economics(url: str) -> dict:
    try:
        status, body, _ = httputil.get(url, timeout=25)
    except Exception as e:
        return {"ok": False, "source": "Trading Economics", "url": url, "error": str(e)}
    if status != 200 or not body:
        return {"ok": False, "source": "Trading Economics", "url": url, "error": f"HTTP {status}"}
    html = body.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")
    desc = ""
    for m in soup.find_all("meta"):
        if (m.get("name") or m.get("property") or "") in ("description", "og:description"):
            desc = m.get("content") or ""
    # "held steady at 3.67% on November 11, 2026"
    m = re.search(r"at\s+([0-9]+(?:\.[0-9]+)?)%\s+on\s+([A-Za-z]+ \d{1,2}, \d{4})", desc)
    actual = None
    # "Actual | 3.6740"
    m2 = re.search(r"Actual\s*\|\s*([0-9]+\.[0-9]+)", soup.get_text(" | ", strip=True))
    if m2:
        actual = float(m2.group(1))
    if m:
        val = float(m.group(1)) / 100.0
        if actual and abs(actual / 100.0 - val) < 0.002:
            val = actual / 100.0
        date = dt.datetime.strptime(m.group(2), "%B %d, %Y").date().isoformat()
        return {"ok": True, "source": "Trading Economics", "url": url, "value": val, "data_date": date,
                "basis": f"市場數據：Trading Economics 香港 10 年期政府債券孳息約 {val:.3%}（{date}；OTC 銀行同業報價）"}
    if actual is not None:
        return {"ok": True, "source": "Trading Economics", "url": url, "value": actual / 100.0,
                "data_date": None,
                "basis": f"市場數據：Trading Economics 頁面 Actual {actual}（日期未解析）"}
    return {"ok": False, "source": "Trading Economics", "url": url, "error": "無法解析孳息", "http_status": status}


def _investing(url: str) -> dict:
    try:
        status, body, _ = httputil.get(url, timeout=20)
    except Exception as e:
        return {"ok": False, "source": "Investing.com", "url": url, "error": str(e)}
    if status != 200:
        return {"ok": False, "source": "Investing.com", "url": url, "error": f"HTTP {status}", "http_status": status}
    return {"ok": False, "source": "Investing.com", "url": url, "error": "頁面結構未解析（本次未作為主來源）", "http_status": status}


# ---------------------------------------------------------------- by model currency
#
# Method A: Rf is the 10-year government yield of the MODEL currency (the currency the cash flows
# are forecast in), not of the listing. For sovereigns below Aaa, Damodaran's local-currency
# practice subtracts the rating-based default spread, so default risk enters once, through the CRP.
# HKD too (Hong Kong Aa3), owner decision S9 2026-11-18. USD stays the gross UST yield: the
# Damodaran implied ERP is measured against it, so netting the US spread would need the ERP adjusted.

ADB_PAGE = {"CNY": "china", "JPY": "japan", "KRW": "korea", "SGD": "singapore"}
TE_PAGE = {"CNY": "china", "JPY": "japan", "KRW": "south-korea", "TWD": "taiwan", "SGD": "singapore"}
COUNTRY = {"USD": "US", "HKD": "HK", "CNY": "CN", "JPY": "JP", "KRW": "KR", "TWD": "TW"}
NAME = {"CNY": "中國", "JPY": "日本", "KRW": "韓國", "TWD": "台灣", "SGD": "新加坡"}
CROSS_CHECK_GAP = 0.005          # two sources more than 0.5pp apart: say so


def supported_currencies() -> set[str]:
    return {"USD", "HKD"} | set(ADB_PAGE) | set(TE_PAGE)


def for_currency(currency: str, cfg: Optional[dict] = None) -> dict:
    """Risk-free rate for cash flows in ``currency``. Never mixes currencies silently:
    an unsupported currency returns ok=False and the caller decides (and records the gap)."""
    from ..engine import method_a as MA

    ccy = (currency or "").upper()
    if ccy == "USD":
        return dict(ust_10y(), currency="USD")
    if ccy == "HKD":
        hk = hk_10y(cfg)
        if not hk.get("ok"):
            return dict(hk, currency="HKD")
        spread = MA.DEFAULT_SPREAD["HK"]
        gross = float(hk["value"])
        return dict(hk, currency="HKD", value=gross - spread, gross_yield=gross, default_spread=spread,
                    basis=f"{hk['basis']} 減 Damodaran HK 違約息差 {spread:.2%}（方法 A 本幣做法）＝ {gross - spread:.3%}")
    if ccy not in ADB_PAGE and ccy not in TE_PAGE:
        return {"ok": False, "currency": ccy, "error": f"未支援 {ccy} 的本幣 10 年期政府債券孳息"}
    attempts = []
    if ccy in ADB_PAGE:
        a = _adb(f"https://asianbondsonline.adb.org/{ADB_PAGE[ccy]}/")
        a["basis"] = (a.get("basis") or "").replace("香港", NAME.get(ccy, ccy))
        attempts.append(a)
    if ccy in TE_PAGE:
        t = _trading_economics(f"https://tradingeconomics.com/{TE_PAGE[ccy]}/government-bond-yield")
        t["basis"] = (t.get("basis") or "").replace("香港", NAME.get(ccy, ccy))
        attempts.append(t)
    ok = [a for a in attempts if a.get("ok")]
    if not ok:
        return {"ok": False, "currency": ccy, "attempts": attempts,
                "error": f"未能取得 {NAME.get(ccy, ccy)} 10 年期政府債券孳息（已試 {'、'.join(a['source'] for a in attempts)}）"}
    primary = ok[0]
    gross = float(primary["value"])
    data_date = primary.get("data_date") or next((a.get("data_date") for a in ok if a.get("data_date")), None)
    country = COUNTRY.get(ccy)
    spread = MA.DEFAULT_SPREAD.get(country, 0.0) if country else 0.0
    value = gross - spread
    out = {"ok": True, "currency": ccy, "value": value, "gross_yield": gross, "default_spread": spread,
           "data_date": data_date, "source": primary["source"], "url": primary["url"], "attempts": attempts,
           "basis": (f"市場數據：{NAME.get(ccy, ccy)} 10 年期政府債券孳息 {gross:.3%}（{primary['source']}"
                     f"{'，' + data_date if data_date else ''}）"
                     + (f" 減 Damodaran {country} 違約息差 {spread:.2%}（方法 A 本幣做法，違約風險只經 CRP 計入）"
                        if spread else "") + f" ＝ {value:.3%}")}
    if len(ok) > 1 and abs(float(ok[0]["value"]) - float(ok[1]["value"])) > CROSS_CHECK_GAP:
        out["cross_check_warning"] = (f"{ok[0]['source']} {float(ok[0]['value']):.3%} 與 {ok[1]['source']} "
                                      f"{float(ok[1]['value']):.3%} 相差超過 {CROSS_CHECK_GAP:.1%}，請人工核對。")
    return out

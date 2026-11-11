"""HKEX news search + company IR announcement pages."""
from __future__ import annotations

import json
import re
from typing import Optional
from urllib.parse import urljoin

from . import httputil


def stock_id(code: str) -> dict:
    """Resolve 1830 / 01830 / 1830.HK to HKEX stockId."""
    raw = code.upper().replace(".HK", "")
    padded = raw.zfill(5)
    url = f"https://www1.hkexnews.hk/search/prefix.do?callback=callback&lang=EN&type=A&name={padded}&market=SEHK"
    status, body, _ = httputil.get(url, timeout=20)
    out = {"ok": False, "url": url, "http_status": status, "code": padded}
    if status != 200 or not body:
        out["error"] = f"HKEX prefix HTTP {status}"
        return out
    text = body.decode("utf-8", errors="replace")
    m = re.search(r"callback\((\{.*\})\)\s*;?\s*$", text, re.S)
    if not m:
        out["error"] = "prefix 回應不是 JSONP"
        return out
    data = json.loads(m.group(1))
    infos = data.get("stockInfo") or []
    codes = {padded, raw.lstrip("0") or "0", raw.zfill(4), raw.zfill(5)}
    match = next((s for s in infos if str(s.get("code")) in codes), None)
    if not match:
        # prefer exact equity name if many warrant codes share prefix
        match = next((s for s in infos if str(s.get("code")) in (padded, raw.zfill(5))), None)
    if not match:
        out["error"] = f"找不到代號 {padded}"
        out["raw"] = infos[:5]
        return out
    out.update(ok=True, stockId=match.get("stockId"), name=match.get("name"), code=match.get("code"))
    return out


def title_search(stock_id: int, from_date="20240101", to_date=None, row=100) -> dict:
    import datetime as dt
    to_date = to_date or dt.date.today().strftime("%Y%m%d")
    url = (f"https://www1.hkexnews.hk/search/titleSearchServlet.do?sortDir=0&sortByOptions=DateTime"
           f"&category=0&market=SEHK&stockId={stock_id}&documentType=-1&fromDate={from_date}&toDate={to_date}"
           f"&title=&searchType=0&t1code=-2&t2Gcode=-2&t2code=-2&rowRange={row}&lang=EN")
    status, data = httputil.get_json(url, timeout=30)
    out = {"ok": False, "url": url, "http_status": status}
    if status != 200 or not data:
        out["error"] = f"HKEX titleSearch HTTP {status}"
        return out
    res = data.get("result")
    if isinstance(res, str):
        items = json.loads(res)
    else:
        items = res or []
    base = "https://www1.hkexnews.hk"
    cleaned = []
    for it in items:
        link = it.get("FILE_LINK") or ""
        cleaned.append({
            "datetime": it.get("DATE_TIME"),
            "title": _strip_html(it.get("TITLE") or it.get("SHORT_TEXT") or ""),
            "file_link": urljoin(base, link) if link else None,
            "news_id": it.get("NEWS_ID"),
            "stock_code": it.get("STOCK_CODE"),
            "stock_name": it.get("STOCK_NAME"),
            "file_info": it.get("FILE_INFO"),
        })
    out.update(ok=True, items=cleaned, recordCnt=data.get("recordCnt"))
    return out


def _strip_html(s: str) -> str:
    s = s.replace("&#x2f;", "/").replace("&amp;", "&").replace("&#x27;", "'")
    return re.sub(r"<br\s*/?>", "\n", s, flags=re.I)


def is_results_filing(title: str) -> bool:
    t = (title or "").upper()
    keys = ["ANNUAL RESULTS", "INTERIM RESULTS", "FINAL RESULTS", "QUARTERLY RESULTS",
            "PROFIT WARNING", "PROFIT ALERT", "POSITIVE PROFIT", "OPERATIONAL UPDATE",
            "ANNUAL REPORT", "INTERIM REPORT"]
    return any(k in t for k in keys)


def parse_hk_datetime(s: str) -> Optional[str]:
    """'09/11/2026 18:16' -> 2026-11-09 (HK date order DD/MM/YYYY)."""
    import datetime as dt
    if not s:
        return None
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", s.strip())
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return dt.date(y, mo, d).isoformat()
    except ValueError:
        return None

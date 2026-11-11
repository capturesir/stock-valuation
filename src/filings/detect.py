"""Compare latest exchange filings vs last valuation state.json."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ..fetch import hkex, sec


def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def check_us(ticker: str, cik: str, state: dict) -> dict:
    sub = sec.submissions(cik)
    out = {"market": "US", "ticker": ticker, "cik": cik, "source": sub.get("url"),
           "source_ok": sub.get("ok"), "rerun_needed": False}
    if not sub.get("ok"):
        out.update(error=sub.get("error"), blocked=True,
                   note="若 EDGAR submissions 失敗，無法判斷是否有新財報；請人手檢查。")
        return out
    latest = sec.latest_results_filing(sub)
    out["latest_filing"] = latest
    last = (state or {}).get("last_results_filing") or {}
    last_key = (last.get("form"), last.get("filingDate"), last.get("reportDate"), last.get("accession"))
    new_key = (latest.get("form"), latest.get("filingDate"), latest.get("reportDate"), latest.get("accession")) if latest else None
    if latest and (not last.get("filingDate") or (latest.get("filingDate") or "") > (last.get("filingDate") or "") or new_key != last_key):
        # first run with empty state: still "rerun needed" if we have no valuation yet, else only if newer
        if last.get("filingDate") and (latest.get("filingDate") or "") > last["filingDate"]:
            out["rerun_needed"] = True
            out["reason"] = f"發現較新的 {latest.get('form')}（呈交日 {latest.get('filingDate')}，報告期 {latest.get('reportDate')}），晚於上次估值所用 {last.get('form')} {last.get('filingDate')}"
        elif last.get("filingDate") and new_key != last_key and (latest.get("filingDate") or "") >= last.get("filingDate"):
            out["rerun_needed"] = True
            out["reason"] = "最新業績檔與 state.json 記錄不一致"
        elif not last.get("filingDate"):
            out["rerun_needed"] = False
            out["reason"] = "尚無上次估值檔案日；這是首次或 state 為空。最新檔如下，不自動視為『須重跑』。"
        else:
            out["reason"] = "最新業績檔不新於上次估值"
    else:
        out["reason"] = "沒有更新的年報／季報／業績 8-K（item 2.02）"
    # HTML archive often blocked
    if latest and latest.get("accession") and latest.get("primary"):
        out["archive_url"] = sec.archive_url(cik, latest["accession"], latest["primary"])
        out["html_fetch_note"] = "www.sec.gov/Archives 常回 403；偵測以 data.sec.gov/submissions 為準。"
    return out


def check_hk(ticker: str, state: dict) -> dict:
    sid = hkex.stock_id(ticker)
    out = {"market": "HK", "ticker": ticker, "source_ok": sid.get("ok"), "rerun_needed": False}
    if not sid.get("ok"):
        out.update(error=sid.get("error"), blocked=True, note="披露易代號查詢失敗，無法判斷是否有新公告。")
        return out
    search = hkex.title_search(sid["stockId"])
    out["hkex_search_url"] = search.get("url")
    out["stockId"] = sid["stockId"]
    out["stock_name"] = sid.get("name")
    if not search.get("ok"):
        out.update(error=search.get("error"), blocked=True, source_ok=False)
        return out
    results = [it for it in search.get("items") or [] if hkex.is_results_filing(it.get("title") or "")]
    out["latest_results_filings"] = results[:8]
    latest = results[0] if results else None
    out["latest_filing"] = latest
    last = (state or {}).get("last_results_filing") or {}
    last_date = last.get("filingDate") or last.get("date")
    new_date = hkex.parse_hk_datetime((latest or {}).get("datetime") or "") if latest else None
    if latest and last_date and new_date and new_date > last_date:
        out["rerun_needed"] = True
        out["reason"] = f"發現較新的業績／盈警／年報或中期（{latest.get('title')}，{new_date}），晚於上次估值所用 {last_date}"
    elif latest and not last_date:
        out["rerun_needed"] = False
        out["reason"] = "尚無上次估值檔案日；這是首次或 state 為空。最新相關公告如下。"
    elif not latest:
        out["reason"] = "搜尋窗口內沒有年報／中期／業績／盈警類公告"
    else:
        out["reason"] = "最新相關公告不新於上次估值"
    out["source"] = search.get("url")
    out["source_ok"] = True
    return out

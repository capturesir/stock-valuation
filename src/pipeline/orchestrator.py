"""Fetch → (LLM or rules) → engine → verify → Excel / markdown."""
from __future__ import annotations

import csv
import datetime as dt
import json
import traceback
from pathlib import Path
from typing import Optional

from ..llm.client import LLMClient
from ..llm import steps as llm_steps
from ..fetch import yahoo, rf as rfmod, sec, hkex
from ..filings import detect
from . import name_specs, generic_spec
from .model_builder import build_and_run
from ..export.workbook import build_workbook
from ..export.report_md import write_report

ROOT = Path(__file__).resolve().parents[2]


def load_config(path: Optional[Path] = None) -> dict:
    import yaml
    path = path or (ROOT / "config.yaml")
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def run_ticker(ticker: str, cfg: Optional[dict] = None, use_llm: bool = True,
               valuation_date: str = "2026-11-11") -> dict:
    cfg = cfg or load_config()
    ticker = _norm(ticker)
    out_dir = ROOT / "out" / f"{valuation_date}_{ticker}"
    case_dir = ROOT / "cases" / ticker
    out_dir.mkdir(parents=True, exist_ok=True)
    case_dir.mkdir(parents=True, exist_ok=True)

    log = {"ticker": ticker, "valuation_date": valuation_date, "steps": []}
    snap = {"ticker": ticker, "data_gaps": [], "valuation_date": valuation_date}

    # prices
    ch = yahoo.chart_quote(ticker)
    (case_dir / "yahoo_chart.json").write_text(json.dumps(ch, ensure_ascii=False, indent=1), encoding="utf-8")
    if not ch.get("ok"):
        raise RuntimeError(f"無法取得 {ticker} 市價：{ch.get('error')}")
    last = ch["last"]
    snap["price"] = float(last["close"])
    snap["price_date"] = last["date"]
    snap["price_url"] = f"https://finance.yahoo.com/quote/{ticker}"

    # fundamentals
    fund = yahoo.fundamentals(ticker)
    (case_dir / "yahoo_fundamentals.json").write_text(json.dumps(fund, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    info = fund.get("info") or {}
    snap["company_name"] = info.get("longName") or info.get("shortName")
    snap["beta_reg"] = info.get("beta")
    snap["currency_yahoo"] = info.get("currency") or (ch.get("meta") or {}).get("currency")

    # Rf
    if ticker.endswith(".HK"):
        rfd = rfmod.hk_10y(cfg)
    else:
        rfd = rfmod.ust_10y()
    (case_dir / "rf.json").write_text(json.dumps(rfd, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if not rfd.get("ok"):
        raise RuntimeError(f"無法取得無風險利率：{rfd.get('error')}")
    snap["rf"] = rfd

    # IB consensus from Yahoo only (no invented brokers)
    apt = fund.get("analyst_price_targets") if isinstance(fund.get("analyst_price_targets"), dict) else {}
    n = info.get("numberOfAnalystOpinions")
    mean = info.get("targetMeanPrice") or (apt or {}).get("mean")
    snap["ib_compare"] = {
        "consensus": {
            "source": "Yahoo Finance Analysis / info targets",
            "url": f"https://finance.yahoo.com/quote/{ticker}/analysis/",
            "data_date": snap["price_date"],
            "mean": mean, "high": info.get("targetHighPrice") or (apt or {}).get("high"),
            "low": info.get("targetLowPrice") or (apt or {}).get("low"),
            "median": info.get("targetMedianPrice") or (apt or {}).get("median"),
            "n_analysts": n, "currency": snap.get("currency_yahoo"),
        },
        "named_brokers": [],
        "data_gaps": [],
    }
    if not mean or not n:
        snap["ib_compare"]["data_gaps"].append("Yahoo 無可用的分析師目標價或人數為 0；不填具名投行目標價。")
        # don't leave a fake 0
        if not mean:
            snap["ib_compare"]["consensus"]["mean"] = None
    else:
        snap["ib_compare"]["data_gaps"].append("具名券商報告原文未取得，named_brokers 留空。")

    # Filings metadata
    if ticker.endswith(".HK"):
        st = {}
        chk = detect.check_hk(ticker, st)
        (case_dir / "filings_check.json").write_text(json.dumps(chk, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        snap["filings_check"] = chk
        if chk.get("latest_filing"):
            snap["latest_filing"] = chk["latest_filing"]
    else:
        cik = sec.cik_from_ticker(ticker)
        chk = detect.check_us(ticker, cik, {}) if cik else {"error": "無 CIK"}
        (case_dir / "filings_check.json").write_text(json.dumps(chk, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        snap["filings_check"] = chk
        if cik:
            # try HTML of latest 10-Q
            latest = chk.get("latest_filing") or {}
            if latest.get("accession") and latest.get("primary"):
                url = sec.archive_url(cik, latest["accession"], latest["primary"])
                html = sec.try_fetch_html(url)
                (case_dir / "edgar_html_attempt.json").write_text(json.dumps(html, indent=1), encoding="utf-8")
                if not html.get("ok"):
                    snap["edgar_html_blocked"] = True
                    snap["data_gaps"].append(html.get("error") or "EDGAR HTML 無法下載")

    # LLM optional extract / assume / ib — never required
    client = LLMClient(cfg)
    llm_status = {"available": client.available(), "base_url": client.base_url or None,
                  "model": client.model or None, "used": False, "errors": []}
    if use_llm and client.available():
        # We still run rules for the engine path; LLM output is stored for audit if it succeeds.
        try:
            brief = json.dumps({"ticker": ticker, "company": snap.get("company_name"),
                                "yahoo_info_slim": {k: info.get(k) for k in
                                                    ["longName", "industry", "sector", "country", "fullTimeEmployees"]}},
                               ensure_ascii=False)
            # Do not send entire financial statements if huge; send dates + note
            r1 = llm_steps.extract_financials(client, brief + "\n（完整報表已由腳本擷取；若來源不足請返回 data_gaps。）")
            if not r1.ok:
                llm_status["errors"].append(r1.error)
            else:
                llm_status["extract_financials"] = r1.data
                llm_status["used"] = True
        except Exception as e:
            llm_status["errors"].append(str(e))
    else:
        if use_llm and not client.available():
            llm_status["errors"].append("未設定 LLM_BASE_URL／LLM_MODEL，或雲端未提供 LLM_API_KEY；改用規則後備。")
    snap["assumption_source"] = "llm" if llm_status.get("used") else "rules_fallback"
    snap["llm_status"] = llm_status

    # Build spec
    if ticker == "NVDA":
        spec = name_specs.spec_nvda(snap)
    elif ticker == "1830.HK":
        spec = name_specs.spec_1830(snap)
    else:
        # No hand-verified spec: build one from the fetched statements so a new
        # ticker can be valued straight away. Numbers still come from sources,
        # never from the model's memory; see generic_spec.py.
        try:
            spec = generic_spec.build_generic_spec(snap, fund, cfg, use_llm=use_llm, client=client)
        except generic_spec.GenericSpecError as exc:
            raise RuntimeError(
                f"{ticker} 無法自動建立模型：{exc}"
                "（已取得市價與財報，但關鍵項目不足；請補來源或改用內建規格 NVDA／1830.HK。）"
            ) from exc

    spec["assumption_source"] = snap["assumption_source"]
    spec["llm_status"] = llm_status
    (case_dir / "spec_audit.json").write_text(json.dumps(_safe(spec), ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    result = build_and_run(spec, out_dir=str(out_dir))
    outj = result["json"]
    # write assumptions csv
    _assumptions_csv(outj, out_dir / f"{valuation_date}_{ticker}_assumptions.csv")
    # sources json
    (out_dir / f"{valuation_date}_{ticker}_sources.json").write_text(
        json.dumps({"price": snap["price"], "price_date": snap["price_date"], "rf": rfd,
                    "filings": snap.get("filings_check"), "ib": snap.get("ib_compare"),
                    "yahoo_urls": fund.get("urls")}, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8")
    xlsx = out_dir / f"{valuation_date}_{ticker}_FCFF.xlsx"
    build_workbook(outj, str(xlsx))
    md = out_dir / f"{valuation_date}_{ticker}_valuation.md"
    write_report(outj, str(md))
    # state.json for filing reruns
    last = (snap.get("filings_check") or {}).get("latest_filing") or {}
    filing_date = last.get("filingDate") or hkex.parse_hk_datetime(last.get("datetime") or "") if last else None
    state = {
        "ticker": ticker,
        "last_valuation_date": valuation_date,
        "last_results_filing": {
            "form": last.get("form") or last.get("title"),
            "filingDate": filing_date or last.get("filingDate"),
            "reportDate": last.get("reportDate"),
            "accession": last.get("accession"),
            "title": last.get("title"),
            "file_link": last.get("file_link"),
        },
        "price": snap["price"],
        "price_date": snap["price_date"],
        "fair_value_per_share": outj["fair_value_per_share"],
    }
    (case_dir / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "run_meta.json").write_text(json.dumps({
        "verify_pass": result["verify_pass"], "verify_errors": result["verify_errors"],
        "assumption_source": snap["assumption_source"], "llm_status": llm_status,
        "xlsx": str(xlsx), "md": str(md), "json": result["path"],
        "fair_value_per_share": outj["fair_value_per_share"],
        "wacc": outj["discount"]["wacc"]["value"], "g": outj["terminal"]["g"],
        "price": snap["price"], "price_date": snap["price_date"],
        "company": outj["asset"],
    }, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    result["xlsx"] = str(xlsx)
    result["md"] = str(md)
    result["out_dir"] = str(out_dir)
    result["snap"] = snap
    return result


def check_filings(ticker: str, cfg: Optional[dict] = None) -> dict:
    ticker = _norm(ticker)
    case_dir = ROOT / "cases" / ticker
    state = {}
    sp = case_dir / "state.json"
    if sp.exists():
        state = json.loads(sp.read_text(encoding="utf-8"))
    if ticker.endswith(".HK"):
        out = detect.check_hk(ticker, state)
    else:
        cik = sec.cik_from_ticker(ticker)
        if not cik:
            return {"ticker": ticker, "error": "未知 CIK，無法查 SEC", "rerun_needed": False}
        out = detect.check_us(ticker, cik, state)
    out["ticker"] = ticker
    out["state_path"] = str(sp) if sp.exists() else None
    return out


def _norm(ticker: str) -> str:
    t = ticker.strip().upper()
    if t.endswith(".HK") and t[:-3].isdigit():
        t = t[:-3].zfill(4) + ".HK"
    elif t.isdigit() and len(t) <= 4:
        t = t.zfill(4) + ".HK"
    return t


def _safe(spec: dict) -> dict:
    # drop unserializable callables; D() dicts are fine
    return spec


def _assumptions_csv(d: dict, path: Path):
    rows = [["item", "value", "basis", "source_name", "url", "data_date"]]
    for k in ["rf", "erp", "crp", "beta_adopted", "kd_pre_tax", "tax_rate", "wacc"]:
        x = d["discount"][k]
        rows.append([k, x.get("value"), x.get("basis"), x.get("source_name"), x.get("url"), x.get("data_date")])
    rows.append(["terminal_g", d["terminal"]["g"], d["terminal"].get("g_rationale"), "模型假設", "", d.get("valuation_date")])
    rows.append(["shares", d["shares"], str(d.get("shares_source")), "", "", ""])
    path.write_text("", encoding="utf-8")
    with path.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(rows)

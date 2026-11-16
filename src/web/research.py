"""Research data for the web UI: statement review, history, AI plan, ETA.

Everything here is derived from files the pipeline already wrote
(cases/<ticker>/yahoo_fundamentals.json, out/<date>_<ticker>/*.json,
out/_run_history.json). Nothing is estimated or invented: when a figure or a
duration is not on disk, the caller gets an explicit reason instead of a
plausible-looking number.

Adjustment storage lives in pipeline/adjustments.py because a valuation run has
to read it; this module is the read side the browser uses.
"""
from __future__ import annotations

import statistics
from typing import Optional

from . import services
from ..pipeline import adjustments as adj


# ---------------------------------------------------------------------------
# R04–R05: every collected line item, ordered by importance
# ---------------------------------------------------------------------------

def statement_rows(ticker: str, period: str = "annual") -> dict:
    """Return every collected statement line item, per statement and year."""
    ticker = adj.norm_ticker(ticker)
    fund = adj.load_fundamentals(ticker)
    if not fund:
        return {
            "ok": False,
            "ticker": ticker,
            "error": ("尚未收集此代號的財報（請先按一次「估值」或「檢查財報」，"
                      "系統會把 Yahoo 財報存到 cases/）。"),
            "statements": [],
            "count": 0,
        }
    info = fund.get("info") or {}
    currency = info.get("currency") or "—"
    period = "quarterly" if period == "quarterly" else "annual"

    out = []
    for d in adj.STATEMENT_DEFS:
        block = fund.get(d[period]) or {}
        years = sorted(block.keys(), reverse=True)  # newest first
        if not years:
            continue
        items: dict[str, dict] = {}
        for y in years:
            for name, value in (block.get(y) or {}).items():
                if value is None or isinstance(value, (str, bool)):
                    continue
                if not isinstance(value, (int, float)):
                    continue
                items.setdefault(name, {"item": name, "values": {}})["values"][y] = float(value)

        rows = []
        for name, it in items.items():
            vals = it["values"]
            latest = vals.get(years[0])
            kind = adj.kind_of(name, latest)
            scale = adj.scale_for(kind)
            rows.append({
                "item": name,
                "label": adj.label(name),
                "kind": kind,
                "scale": scale,
                "unit": {"amount": f"百萬 {currency}", "shares": "百萬股",
                         "ratio": "比率", "per_share": f"{currency}／股"}[kind],
                "values": {y: (v / scale if scale != 1 else v) for y, v in vals.items()},
                "latest": (latest / scale if scale != 1 and latest is not None else latest),
                "model_input": adj.model_input_for(d["key"], name),
            })

        # R05: important = big money first, but a ratio is never ranked against
        # an amount. Importance is a percentile inside (statement, kind).
        for kind in {r["kind"] for r in rows}:
            ranked = sorted([r for r in rows if r["kind"] == kind],
                            key=lambda r: abs(r["latest"] or 0), reverse=True)
            n = max(1, len(ranked))
            for i, r in enumerate(ranked):
                r["importance"] = round(1.0 - i / n, 4)
                r["rank"] = i + 1
                r["rank_of"] = n

        def _order(r):
            if r["kind"] == "amount":
                return (0, -abs(r["latest"] or 0))
            return (1, {"shares": 0, "per_share": 1, "ratio": 2}.get(r["kind"], 3),
                    -abs(r["latest"] or 0))

        rows.sort(key=_order)
        out.append({
            "key": d["key"],
            "label": d["label"],
            "years": years,
            "rows": rows,
            "count": len(rows),
        })

    total = sum(s["count"] for s in out)
    return {
        "ok": True,
        "ticker": ticker,
        "currency": currency,
        "period": period,
        "statements": out,
        "count": total,
        "note": ("逐項列出 Yahoo 財報全部科目（空白科目不顯示）。金額以百萬"
                 f"{currency}顯示；比率與每股數字不與金額混合排序。"),
    }


# ---------------------------------------------------------------------------
# R17–R20: valuation history
# ---------------------------------------------------------------------------

def load_calc(ticker: str, valuation_date: Optional[str] = None) -> Optional[dict]:
    """The calc JSON for one valuation (explicit date first, else the latest)."""
    from pathlib import Path

    ticker = adj.norm_ticker(ticker)
    if valuation_date:
        try:
            rec = services.resolve_workbook(ticker, valuation_date)
        except (FileNotFoundError, ValueError):
            return None
        return adj.read_json(Path(rec["json"])) if rec.get("json") else None
    last = services.latest_valuation(ticker)
    return adj.read_json(Path(last["json"])) if last and last.get("json") else None


def valuation_history(ticker: str) -> dict:
    """Every valuation we know about: what was thought then, and the price then.

    Two sources are merged:
    - out/history/<TICKER>.json, one record per run (keeps same-day re-runs);
    - out/<date>_<ticker>/, which holds the latest run of each date and is the
      only source for valuations made before the run log existed.
    Points are de-duplicated by (date, fair value, price) so the overlap does
    not double-count anything.
    """
    from ..pipeline import runlog

    ticker = adj.norm_ticker(ticker)
    points: list[dict] = []
    seen: set[tuple] = set()

    def add(date, fair, price, *, source, run_at=None, currency=None, verify=None,
            assumption_source=None, wacc=None, g=None, price_date=None,
            json_path=None, run_id=None):
        key = (date, round(float(fair), 4), round(float(price), 4))
        if key in seen:
            return
        seen.add(key)
        points.append({
            "date": date,
            "price_date": price_date,
            "fair_value": float(fair),
            "price": float(price),
            "gap": float(fair) - float(price),
            "gap_pct": (float(fair) / float(price) - 1.0) if price else None,
            "verify_pass": verify,
            "assumption_source": assumption_source,
            "wacc": wacc,
            "g": g,
            "currency": currency,
            "json": json_path,
            "run_at": run_at,
            "run_id": run_id,
            "source": source,
        })

    for r in runlog.read(ticker):
        fair, price = r.get("fair_value"), r.get("price")
        if fair is None or price is None:
            continue
        add(r.get("valuation_date"), fair, price, source="runlog",
            run_at=r.get("run_at"), currency=r.get("currency"),
            verify=r.get("verify_pass"), assumption_source=r.get("assumption_source"),
            wacc=r.get("wacc"), g=r.get("g"), price_date=r.get("price_date"),
            json_path=None, run_id=r.get("run_id"))

    for v in services.find_valuations(ticker):
        if v.get("fair_value_per_share") is None or v.get("price") is None:
            continue
        calc = load_calc(ticker, v["valuation_date"]) or {}
        add(v["valuation_date"], v["fair_value_per_share"], v["price"], source="folder",
            run_at=None, currency=calc.get("currency"),
            verify=v.get("verify_pass"), assumption_source=v.get("assumption_source"),
            wacc=v.get("wacc"), g=v.get("g"), price_date=v.get("price_date"),
            json_path=v.get("json"), run_id=None)

    points.sort(key=lambda p: (p["date"], p.get("run_at") or ""))
    # R20: several valuations on one day stay visible; the front end offsets them
    # and labels the count instead of hiding any of them.
    by_date: dict[str, int] = {}
    for p in points:
        by_date[p["date"]] = by_date.get(p["date"], 0) + 1
    counter: dict[str, int] = {}
    for p in points:
        idx = counter.get(p["date"], 0)
        counter[p["date"]] = idx + 1
        p["same_day_count"] = by_date[p["date"]]
        p["same_day_index"] = idx
    return {
        "ok": True,
        "ticker": ticker,
        "points": points,
        "count": len(points),
        "dense_dates": sorted(d for d, n in by_date.items() if n > 1),
        "runlog_points": sum(1 for p in points if p["source"] == "runlog"),
        "note": ("每個點是一次估值：當時的理論價與當時的市價。同日重跑會各自保留，"
                 "圖表隨每次估值累積，不會用今天的價格改寫歷史。"),
    }


# ---------------------------------------------------------------------------
# R08–R11: history vs forecast, with reasons
# ---------------------------------------------------------------------------

def _historical_rows(ticker: str, limit: int = 4) -> list[dict]:
    fund = adj.load_fundamentals(ticker) or {}
    is_a = fund.get("is_a") or {}
    cf_a = fund.get("cf_a") or {}
    years = sorted(is_a.keys(), reverse=True)[:limit]
    out = []
    for y in reversed(years):
        inc = is_a.get(y) or {}
        cf = cf_a.get(y) or {}
        rev = inc.get("Total Revenue") or inc.get("Operating Revenue")
        ebit = inc.get("Operating Income") or inc.get("Total Operating Income As Reported")
        if not isinstance(rev, (int, float)):
            continue
        da = cf.get("Depreciation And Amortization")
        capex = cf.get("Capital Expenditure")
        out.append({
            "year": y[:4],
            "period_end": y,
            "revenue": rev / 1e6,
            "ebit": (ebit / 1e6) if isinstance(ebit, (int, float)) else None,
            "ebit_margin": (ebit / rev) if isinstance(ebit, (int, float)) and rev else None,
            "da": (da / 1e6) if isinstance(da, (int, float)) else None,
            "capex": (abs(capex) / 1e6) if isinstance(capex, (int, float)) else None,
            "kind": "actual",
        })
    return out


def forecast_block(ticker: str, calc: Optional[dict] = None) -> dict:
    """Historical actuals and forecast periods side by side, each with its reason."""
    ticker = adj.norm_ticker(ticker)
    calc = calc or load_calc(ticker) or {}
    hist = _historical_rows(ticker)
    base = calc.get("base_year") or {}
    proj = calc.get("projections") or []
    if not proj and not hist:
        return {"ok": False, "error": "尚未有估值結果，無法並列歷史與預測。",
                "rows": [], "reasons": []}

    rows = list(hist)
    if base.get("revenue") is not None:
        rows.append({
            "year": str(base.get("year") or "基準年"),
            "period_end": base.get("year_end"),
            "revenue": base.get("revenue"),
            "ebit": base.get("ebit"),
            "ebit_margin": base.get("ebit_margin"),
            "da": base.get("da"),
            "capex": base.get("capex"),
            "kind": "base",
        })
    reasons = []
    for p in proj:
        rows.append({
            "year": p.get("year"),
            "period_end": None,
            "revenue": p.get("revenue"),
            "growth": p.get("revenue_growth"),
            "growth_base": p.get("revenue_growth_base"),
            "ebit": p.get("ebit"),
            "ebit_margin": p.get("ebit_margin"),
            "da": p.get("da"),
            "capex": p.get("capex"),
            "nwc": p.get("delta_nwc"),
            "fcff": p.get("fcff"),
            "pv": p.get("pv_fcff"),
            "discount_factor": p.get("discount_factor"),
            "kind": "forecast",
        })
        reasons.append({
            "year": p.get("year"),
            "growth": p.get("revenue_growth"),
            "growth_rationale": p.get("growth_rationale") or "",
            "margin_rationale": p.get("margin_rationale") or "",
            "reinvestment_rationale": p.get("reinvestment_rationale") or "",
        })
    term = calc.get("terminal") or {}
    if term:
        reasons.append({
            "year": "終值",
            "growth": term.get("g"),
            "growth_rationale": term.get("g_rationale") or "",
            "margin_rationale": term.get("method_rationale") or "",
            "reinvestment_rationale": (f"RONIC {term.get('ronic')}"
                                       if term.get("ronic") is not None else ""),
        })
    return {
        "ok": True,
        "ticker": ticker,
        "currency": calc.get("currency"),
        "units": calc.get("units"),
        "rows": rows,
        "reasons": reasons,
        "forecast_years": calc.get("forecast_years"),
        "horizon_rationale": calc.get("forecast_horizon_rationale"),
        "applied_adjustments": calc.get("adjustments_applied") or [],
        "note": ("最近四個已公布財年、估值基準年與各預測期放在同一張表；「實際」欄來自"
                 "已公布財報，「預測」欄來自本次模型，理由逐年列出。"),
    }


# ---------------------------------------------------------------------------
# R13–R14: what the AI will actually do, and how much of it
# ---------------------------------------------------------------------------

def ai_plan(ticker: str, valuation_date: Optional[str] = None, use_llm: bool = True,
            model: Optional[str] = None) -> dict:
    """Concrete, count-based preview of the AI steps for this exact ticker."""
    ticker = adj.norm_ticker(ticker)
    review = statement_rows(ticker)
    item_count = review.get("count") or 0
    calc = load_calc(ticker, valuation_date) or {}
    years = len(calc.get("projections") or []) or 11
    has_spec = services.has_name_spec(ticker)
    steps = [
        {
            "key": "ai_extract", "label": "AI 讀取財報摘要並整理資料缺口",
            "requests": 1, "items": item_count,
            "detail": f"1 次請求；輸入為 {item_count} 個已收集財報科目的摘要與公司資料",
            "will_run": bool(use_llm),
            "skip_reason": None if use_llm else "已勾選不使用語言模型",
        },
        {
            "key": "ai_assume", "label": "AI 提出增長與利潤率假設",
            "requests": 1, "items": years * 2,
            "detail": f"1 次請求；{years} 個預測期的營收增長與 EBIT 利潤率（{years} × 2 項）",
            "will_run": bool(use_llm and not has_spec),
            "skip_reason": ("內建人工覆核規格已鎖定假設，不呼叫 AI" if has_spec
                            else None if use_llm else "已勾選不使用語言模型"),
        },
        {
            "key": "ai_ib", "label": "AI 對照投行目標價",
            "requests": 0, "items": 1,
            "detail": "併入上述請求；只整理 Yahoo 已有的目標價，不生成具名券商數字",
            "will_run": bool(use_llm),
            "skip_reason": None if use_llm else "已勾選不使用語言模型",
        },
    ]
    will = [s for s in steps if s["will_run"]]
    return {
        "ok": True,
        "ticker": ticker,
        "use_llm": bool(use_llm),
        "model": model,
        "has_name_spec": has_spec,
        "steps": steps,
        "totals": {
            "requests": sum(s["requests"] for s in will),
            "items": sum(s["items"] for s in will),
            "skipped": len(steps) - len(will),
        },
        "note": ("數量隨此代號實際收集到的財報科目與預測期數變動；"
                 "略過的步驟不會顯示為將執行。"),
    }


def eta_estimate(use_llm: Optional[bool] = None, ticker: Optional[str] = None) -> dict:
    """Rough remaining time from the last 10 completed runs on this machine."""
    from . import jobs as jobmod

    runs = [r for r in jobmod.load_history() if r.get("status") == "done" and r.get("duration")]
    if use_llm is not None:
        runs = [r for r in runs if bool(r.get("use_llm", True)) == bool(use_llm)]
    if ticker:
        same = [r for r in runs if r.get("ticker") == ticker]
        if len(same) >= 2:
            runs = same
    if not runs:
        return {"sample_size": 0, "median_seconds": None, "source": "none",
                "note": "資料不足：還沒有完成的估值紀錄可參考，無法估時。"}
    secs = [float(r["duration"]) for r in runs[-10:]]
    median = statistics.median(secs)
    return {
        "sample_size": len(secs),
        "median_seconds": round(median, 1),
        "min_seconds": round(min(secs), 1),
        "max_seconds": round(max(secs), 1),
        "source": "history" if len(secs) >= 3 else "history_thin",
        "note": (f"以最近 {len(secs)} 次完成紀錄的中位數 {round(median)} 秒粗估"
                 if len(secs) >= 3 else
                 f"僅 {len(secs)} 次紀錄，估時僅供參考"),
    }


# ---------------------------------------------------------------------------
# Combined payload used by the workbook response and the extra Excel sheets
# ---------------------------------------------------------------------------

def research_payload(ticker: str, calc: Optional[dict] = None,
                     valuation_date: Optional[str] = None) -> dict:
    ticker = adj.norm_ticker(ticker)
    calc = calc or load_calc(ticker, valuation_date) or {}
    return {
        "ticker": ticker,
        "review": statement_rows(ticker),
        "adjustments": adj.load_adjustments(ticker),
        "applied": calc.get("adjustments_applied") or [],
        "forecast": forecast_block(ticker, calc),
        "history": valuation_history(ticker),
        "usage": calc.get("llm_usage") or {},
        "eta": eta_estimate(None, None),
    }

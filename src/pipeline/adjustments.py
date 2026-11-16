"""Statement adjustments: the audit trail that feeds the next valuation.

R06/R07 of the v1.1 spec: a user may revise a collected financial-statement
figure, but the original value stays readable, a reason is mandatory, and the
effect on the model is stated instead of implied.

This lives in pipeline/ (not web/) because the valuation run has to read the
adopted adjustments before it builds the model. Nothing here writes outside
cases/<ticker>/adjustments.json.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]

# Yahoo names the statements is_a / bs_a / cf_a (annual) and is_q / bs_q / cf_q
# (quarterly). The valuation consumes annual figures, so review defaults to the
# same period length the model used.
STATEMENT_DEFS = [
    {"key": "is", "label": "損益表", "annual": "is_a", "quarterly": "is_q"},
    {"key": "bs", "label": "資產負債表", "annual": "bs_a", "quarterly": "bs_q"},
    {"key": "cf", "label": "現金流量表", "annual": "cf_a", "quarterly": "cf_q"},
]
STATEMENT_KEYS = {d["key"] for d in STATEMENT_DEFS}

# Which raw line item becomes which model input. The first name present in the
# data wins; this mirrors what name_specs / generic_spec read by hand.
MODEL_INPUTS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "revenue": ("is", ("Total Revenue", "Operating Revenue"), "營收（預測起點）"),
    "ebit": ("is", ("Operating Income", "Total Operating Income As Reported"),
             "EBIT（利潤率起點）"),
    "da": ("cf", ("Depreciation And Amortization", "Depreciation Amortization Depletion",
                  "Depreciation"), "D&A（占營收比率）"),
    "capex": ("cf", ("Capital Expenditure", "Purchase Of PPE", "Net PPE Purchase And Sale"),
              "Capex（占營收比率）"),
    "shares": ("bs", ("Ordinary Shares Number", "Share Issued"), "攤薄股數"),
}

LABELS = {
    "Total Revenue": "總營收", "Operating Revenue": "營業收入",
    "Cost Of Revenue": "營業成本", "Gross Profit": "毛利",
    "Operating Expense": "營業費用", "Operating Income": "營業利益",
    "Total Operating Income As Reported": "營業利益（公司報告值）",
    "Research And Development": "研發費用",
    "Selling General And Administration": "銷售及管理費用",
    "Net Income": "淨利", "Net Income Common Stockholders": "歸屬普通股股東淨利",
    "Diluted EPS": "攤薄每股盈利", "Basic EPS": "基本每股盈利",
    "EBITDA": "EBITDA", "EBIT": "EBIT", "Normalized EBITDA": "正常化 EBITDA",
    "Reconciled Depreciation": "折舊（調節後）", "Reconciled Cost Of Revenue": "營業成本（調節後）",
    "Interest Expense": "利息費用", "Interest Income": "利息收入",
    "Tax Provision": "所得稅費用", "Pretax Income": "稅前利潤",
    "Tax Rate For Calcs": "計算用稅率",
    "Diluted Average Shares": "攤薄加權平均股數", "Basic Average Shares": "基本加權平均股數",
    "Total Assets": "總資產", "Total Liabilities Net Minority Interest": "總負債",
    "Total Equity Gross Minority Interest": "總權益", "Stockholders Equity": "股東權益",
    "Total Debt": "總債務", "Net Debt": "淨債務",
    "Cash And Cash Equivalents": "現金及等價物",
    "Cash Cash Equivalents And Short Term Investments": "現金及短期投資",
    "Inventory": "存貨", "Accounts Receivable": "應收帳款", "Accounts Payable": "應付帳款",
    "Working Capital": "營運資金", "Net PPE": "固定資產淨值", "Goodwill": "商譽",
    "Ordinary Shares Number": "已發行普通股股數", "Share Issued": "已發行股數",
    "Operating Cash Flow": "營業現金流",
    "Cash Flow From Continuing Operating Activities": "營業現金流（持續經營）",
    "Free Cash Flow": "自由現金流", "Capital Expenditure": "資本開支",
    "Purchase Of PPE": "購置固定資產", "Depreciation And Amortization": "折舊及攤銷",
    "Depreciation Amortization Depletion": "折舊及攤銷",
    "Change In Working Capital": "營運資金變動", "Change In Receivables": "應收帳款變動",
    "Change In Inventory": "存貨變動",
    "Change In Payables And Accrued Expense": "應付及應計費用變動",
    "Repurchase Of Capital Stock": "回購股份", "Cash Dividends Paid": "派付股息",
    "Stock Based Compensation": "股份支付",
    "Investing Cash Flow": "投資現金流", "Financing Cash Flow": "融資現金流",
    "End Cash Position": "期末現金",
}

RATIO_HINTS = ("Rate", "Margin", "Ratio", "Growth", "Yield", "Per Share", "EPS")
SHARE_HINTS = ("Shares", "Share Issued")


def humanize(name: str) -> str:
    """Total Revenue -> 'Total revenue'; the raw name stays visible for auditing."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    s = s.replace(" And ", " & ").replace(" Of ", " of ")
    return re.sub(r"\s+", " ", s).strip()


def label(name: str) -> str:
    zh = LABELS.get(name)
    return f"{zh}（{humanize(name)}）" if zh else humanize(name)


def kind_of(name: str, value: Any) -> str:
    if any(h in name for h in SHARE_HINTS):
        return "shares"
    if "Per Share" in name or name.endswith("EPS"):
        return "per_share"
    if any(h in name for h in RATIO_HINTS):
        return "ratio"
    if isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value) < 10 and value != 0:
        # A sub-10 figure with no other marker is a ratio or a per-share amount,
        # not a million-currency amount; never rank it against money.
        return "ratio"
    return "amount"


def scale_for(kind: str) -> float:
    return 1.0 if kind in ("ratio", "per_share") else 1e6


def case_dir(ticker: str) -> Path:
    return ROOT / "cases" / norm_ticker(ticker)


def norm_ticker(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    if t.endswith(".HK") and t[:-3].isdigit():
        t = t[:-3].zfill(4) + ".HK"
    elif t.isdigit() and len(t) <= 4:
        t = t.zfill(4) + ".HK"
    return t


def read_json(path: Path) -> Optional[dict]:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def load_fundamentals(ticker: str) -> Optional[dict]:
    return read_json(case_dir(ticker) / "yahoo_fundamentals.json")


def _path(ticker: str) -> Path:
    return case_dir(ticker) / "adjustments.json"


def load_adjustments(ticker: str) -> list[dict]:
    data = read_json(_path(ticker))
    return data if isinstance(data, list) else []


def _write(ticker: str, items: list[dict]) -> None:
    p = _path(ticker)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def find_item(ticker: str, statement: str, item: str, fy: str,
              period: str = "annual") -> Optional[float]:
    fund = load_fundamentals(ticker) or {}
    d = next((s for s in STATEMENT_DEFS if s["key"] == statement), None)
    if not d:
        return None
    block = (fund.get(d["quarterly" if period == "quarterly" else "annual"]) or {}).get(fy) or {}
    v = block.get(item)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def model_input_for(statement: str, item: str) -> Optional[str]:
    for key, (stmt, names, _desc) in MODEL_INPUTS.items():
        if stmt == statement and item in names:
            return key
    return None


def add_adjustment(ticker: str, payload: dict) -> dict:
    """Record a change to one statement line, with the reason that justifies it.

    R07 forbids adopting a change without a reason, so a blank reason is an
    error rather than a silently-defaulted string.
    """
    ticker = norm_ticker(ticker)
    statement = str(payload.get("statement") or "").strip()
    item = str(payload.get("item") or "").strip()
    fy = str(payload.get("fy") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    if statement not in STATEMENT_KEYS:
        raise ValueError("科目類別不正確（應為 is／bs／cf）。")
    if not item or not fy:
        raise ValueError("請提供科目與財報期。")
    raw_value = payload.get("adjusted")
    if raw_value is None or raw_value == "":
        raise ValueError("修改後金額必須是數字。")
    try:
        adjusted = float(raw_value)
    except (TypeError, ValueError):
        raise ValueError("修改後金額必須是數字。")
    if len(reason) < 2:
        raise ValueError("請填寫修改原因（至少 2 個字），否則不能正式採用。")

    original = find_item(ticker, statement, item, fy)
    if original is None:
        raise ValueError(f"找不到 {fy} 的科目「{item}」，無法確認原始值。")
    if abs(adjusted - original) < 1e-9:
        raise ValueError("修改後金額與原始值相同，無需記錄。")

    model_key = model_input_for(statement, item)
    desc = next((d for s, n, d in MODEL_INPUTS.values() if s == statement and item in n), None)
    record = {
        "id": uuid.uuid4().hex[:12],
        "ticker": ticker,
        "statement": statement,
        "item": item,
        "label": label(item),
        "fy": fy,
        "original": original,
        "adjusted": adjusted,
        "delta": adjusted - original,
        "reason": reason,
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "model_input": model_key,
        "affects_model": model_key is not None,
        "note": (f"下次估值的{desc}將按此調整（比例套用，見「套用方式」）"
                 if model_key else
                 "僅留痕：這個科目不是本模型的直接輸入，估值數字不會因此改變。"),
    }
    items = load_adjustments(ticker)
    items.append(record)
    _write(ticker, items)
    return record


def delete_adjustment(ticker: str, adj_id: str) -> bool:
    ticker = norm_ticker(ticker)
    items = load_adjustments(ticker)
    left = [a for a in items if a.get("id") != adj_id]
    if len(left) == len(items):
        return False
    _write(ticker, left)
    return True


def latest_adjustments(ticker: str) -> list[dict]:
    """One adopted adjustment per (statement, item, fy); later records win."""
    seen: dict[tuple, dict] = {}
    for a in load_adjustments(ticker):
        seen[(a.get("statement"), a.get("item"), a.get("fy"))] = a
    return list(seen.values())


REVENUE_KEYS = ("prev_revenue_for_first", "first_revenue", "ytd_actual_revenue",
                "full_year_after_first", "static_base_revenue", "full_year_base_revenue")
STUB_REVENUE_KEYS = ("fy_full_revenue", "h1_actual_revenue", "fy2026_revenue")


def apply_to_spec(spec: dict, ticker: str) -> tuple[dict, list[dict]]:
    """Apply adopted adjustments to the model inputs of a spec copy.

    Only the five drivers the engine actually consumes are touched. Scaling is
    used instead of absolute replacement because the engine derives every
    forecast year from ratios anchored on the base year: a scale keeps those
    ratios intact, makes the effect explainable, and is reversible by deleting
    the adjustment.
    """
    adjs = [a for a in latest_adjustments(ticker) if a.get("affects_model")]
    if not adjs:
        return spec, []

    spec = json.loads(json.dumps(spec, default=str))  # deep, serialisable copy
    applied = []
    for a in adjs:
        key = a.get("model_input")
        try:
            original, adjusted = float(a["original"]), float(a["adjusted"])
        except (KeyError, TypeError, ValueError):
            continue
        if original == 0:
            continue
        k = adjusted / original
        if abs(k - 1) < 1e-12:
            continue
        how = "未套用（找不到對應的模型欄位）"
        if key == "revenue":
            for f in REVENUE_KEYS:
                if isinstance(spec.get(f), (int, float)):
                    spec[f] = spec[f] * k
            stub = spec.get("stub_fields") or {}
            for f in STUB_REVENUE_KEYS:
                if isinstance(stub.get(f), (int, float)):
                    stub[f] = stub[f] * k
            if isinstance(spec.get("explicit_revenues"), list):
                spec["explicit_revenues"] = [
                    v * k if isinstance(v, (int, float)) else v
                    for v in spec["explicit_revenues"]
                ]
            by = spec.get("base_year") or {}
            for f in ("revenue", "h1_fy2027_revenue", "h1_fy2026_revenue"):
                if isinstance(by.get(f), (int, float)):
                    by[f] = by[f] * k
            how = f"所有營收起點 ×{k:.6f}"
        elif key == "ebit":
            spec["margins"] = [m * k for m in (spec.get("margins") or [])]
            by = spec.get("base_year") or {}
            for f in ("ebit", "h1_fy2027_ebit", "profit_alert_h1_fy2027_pat_mid"):
                if isinstance(by.get(f), (int, float)):
                    by[f] = by[f] * k
            if isinstance(by.get("revenue"), (int, float)) and by["revenue"]:
                if isinstance(by.get("ebit"), (int, float)):
                    by["ebit_margin"] = by["ebit"] / by["revenue"]
            how = f"各年 EBIT 利潤率 ×{k:.6f}"
        elif key == "da":
            spec["da_pcts"] = [p * k for p in (spec.get("da_pcts") or [])]
            by = spec.get("base_year") or {}
            if isinstance(by.get("da"), (int, float)):
                by["da"] = by["da"] * k
            how = f"各年 D&A／營收比率 ×{k:.6f}"
        elif key == "capex":
            spec["capex_pcts"] = [p * k for p in (spec.get("capex_pcts") or [])]
            by = spec.get("base_year") or {}
            if isinstance(by.get("capex"), (int, float)):
                by["capex"] = by["capex"] * k
            how = f"各年 Capex／營收比率 ×{k:.6f}"
        elif key == "shares":
            if isinstance(spec.get("shares_m"), (int, float)):
                spec["shares_m"] = spec["shares_m"] * k
            how = f"攤薄股數 ×{k:.6f}"
        applied.append({
            "id": a.get("id"), "item": a.get("item"), "label": a.get("label"),
            "fy": a.get("fy"), "original": original, "adjusted": adjusted,
            "reason": a.get("reason"), "created_at": a.get("created_at"),
            "model_input": key, "effect": how,
        })
    spec["adjustments_applied"] = applied
    return spec, applied

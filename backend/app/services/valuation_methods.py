"""Backend multi-method valuation orchestration (2.0.0-beta.1, D5).

Combines the latest FCFF run (untouched, bit-for-bit identical to v1) with:
- Financial-sector models (DDM, Residual Income, Justified P/B-ROE) + D5 warning for banks/insurers
- Reverse DCF (market-implied 5-year growth rate)
- Relative Valuation (own 5-year historical P/E, P/B, EV/Sales multiple bands)
- Industry-aware Composite Fair Value & Football Field chart data.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.sv_valuation import (
    MethodResultRange,
    MultipleInput,
    build_composite,
    ddm_two_stage,
    pb_roe_justified,
    relative_valuation,
    residual_income,
    solve_implied_growth,
)

from ..models import CorporateAction, PriceBar, Run
from . import statements

DEFAULT_RF = 0.042
DEFAULT_ERP = 0.048
DEFAULT_BETA_BANK = 1.10
DEFAULT_BETA_INSURANCE = 0.90
DEFAULT_TERM_G = 0.025


def _dec(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _all_year_labels(std: dict) -> list[str]:
    return [y["label"] for y in (std.get("years") or [])]


def _latest_std_val(std: dict, code: str, years: list[str]) -> Optional[float]:
    """Return the first non-null value among the requested ``years`` labels (respecting TTM column offset)."""
    row = next((r for r in (std.get("rows") or []) if r["code"] == code), None)
    if not row:
        return None
    idx_map = {lbl: i for i, lbl in enumerate(_all_year_labels(std))}
    cells = row.get("cells") or []
    for y in years:
        i = idx_map.get(y)
        if i is not None and i < len(cells) and cells[i] and cells[i].get("value") is not None:
            return _dec(cells[i]["value"])
    return None


def _std_series(std: dict, code: str, years: list[str]) -> dict[str, float]:
    """Map each requested fiscal-year label to its exact cell value (never shifted by TTM column 0)."""
    row = next((r for r in (std.get("rows") or []) if r["code"] == code), None)
    if not row:
        return {}
    idx_map = {lbl: i for i, lbl in enumerate(_all_year_labels(std))}
    cells = row.get("cells") or []
    out: dict[str, float] = {}
    for y in years:
        i = idx_map.get(y)
        if i is not None and i < len(cells) and cells[i] and cells[i].get("value") is not None:
            v = _dec(cells[i]["value"])
            if v is not None:
                out[y] = v
    return out


def _year_end_prices(db: Session, ticker: str, year_ends: dict[str, str]) -> dict[str, float]:
    """Split-adjusted closing price near each fiscal year end (same share basis as split-adjusted EPS/shares)."""
    if not year_ends:
        return {}
    bars = db.execute(
        select(PriceBar.date, PriceBar.close).where(PriceBar.ticker == ticker).order_by(PriceBar.date)
    ).all()
    if not bars:
        return {}
    splits = db.execute(
        select(CorporateAction.date, CorporateAction.value)
        .where(CorporateAction.ticker == ticker, CorporateAction.kind == "split")
        .order_by(CorporateAction.date)
    ).all()
    out: dict[str, float] = {}
    for label, end_iso in year_ends.items():
        best_date = None
        best_close = None
        for d, close in bars:
            ds = d.isoformat()
            if ds <= end_iso:
                best_date = ds
                best_close = float(close)
            else:
                break
        if best_close is not None and best_close > 0 and best_date is not None:
            # Divide unadjusted historical price by cumulative split ratio of all splits strictly AFTER best_date
            cum_split = 1.0
            for sp_date, sp_val in splits:
                if sp_date.isoformat() > best_date and sp_val and float(sp_val) > 0:
                    cum_split *= float(sp_val)
            out[label] = best_close / cum_split
    return out


def _quantile_band(vals: list[float]) -> Optional[tuple[float, float, float]]:
    clean = sorted(v for v in vals if 0.5 <= v <= 150.0)
    if len(clean) < 2:
        return None
    mid = statistics.median(clean)
    lo = clean[max(0, len(clean) // 4)]
    hi = clean[min(len(clean) - 1, (3 * len(clean)) // 4)]
    if lo == hi:
        lo, hi = mid * 0.85, mid * 1.15
    return lo, mid, hi


def compute_stock_methods(
    db: Session,
    ticker: str,
    *,
    custom_weights: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    """Compute multi-method valuation, Reverse DCF, D5 financial warnings, and Football Field."""
    run = db.scalar(select(Run).where(Run.ticker == ticker, Run.status == "done").order_by(Run.run_at.desc()).limit(1))
    calc: Optional[dict] = None
    if run and run.calc_json_path and Path(run.calc_json_path).is_file():
        try:
            calc = json.loads(Path(run.calc_json_path).read_text(encoding="utf-8"))
        except Exception:
            calc = None

    filer = statements.filer_for(db, ticker)
    std: Optional[dict] = None
    template = "general"
    if filer is not None and filer.synced_at is not None:
        try:
            std = statements.standard_statements(db, filer, view="latest", years=6, ticker=ticker, period="annual")
            template = std.get("template") or "general"
        except Exception:
            std = None

    latest_bar = db.scalar(select(PriceBar.close).where(PriceBar.ticker == ticker).order_by(PriceBar.date.desc()).limit(1))
    price = (run.price if (run and run.price) else None) or (_dec(latest_bar))
    currency = (run.trading_ccy if run else None) or (std.get("currency") if std else None)

    methods: list[MethodResultRange] = []

    # 1. Existing FCFF DCF (exact numbers from latest run — bit-for-bit identical to v1)
    if run is not None:
        sc = {m.scenario: float(m.value) for m in run.methods if m.method == "dcf" and m.value is not None}
        base_dcf = sc.get("base") or (float(run.composite_value) if run.composite_value is not None else None)
        if base_dcf is not None and base_dcf > 0:
            bear_dcf = sc.get("bear", base_dcf * 0.8)
            bull_dcf = sc.get("bull", base_dcf * 1.2)
            methods.append(MethodResultRange(
                method="dcf",
                bear=min(bear_dcf, base_dcf, bull_dcf),
                base=base_dcf,
                bull=max(bear_dcf, base_dcf, bull_dcf),
                assumptions={
                    "run_id": run.id, "valuation_date": run.valuation_date,
                    "wacc": run.wacc, "terminal_g": run.terminal_g,
                    "verify_pass": run.verify_pass,
                },
            ))

    # 2. Financial-sector models (D5: DDM, Residual Income, Justified P/B-ROE)
    if std and template in ("bank", "insurance"):
        fy_objs = [y for y in (std.get("years") or []) if y.get("kind") == "annual"]
        years = [y["label"] for y in fy_objs]
        eq = _latest_std_val(std, "common_equity", years) or _latest_std_val(std, "equity_parent", years) or _latest_std_val(std, "total_equity", years)
        sh = _latest_std_val(std, "shares_diluted_weighted", years) or _latest_std_val(std, "shares_outstanding", years)
        ni = _latest_std_val(std, "net_income_common", years) or _latest_std_val(std, "net_income_parent", years) or _latest_std_val(std, "net_income", years)
        divs = _latest_std_val(std, "dividends_paid", years)

        # Average 3-year annual ROE from standardized ratios (respecting TTM column offset)
        roe_vals = []
        idx_map = {lbl: i for i, lbl in enumerate(_all_year_labels(std))}
        for rr in (std.get("ratios") or {}).get("rows") or []:
            if rr["code"] in ("roe", "roce"):
                cells = rr.get("cells") or []
                for y in years[:3]:
                    i = idx_map.get(y)
                    if i is not None and i < len(cells) and cells[i] and cells[i].get("value") is not None:
                        v = _dec(cells[i]["value"])
                        if v is not None and -0.3 < v < 0.5:
                            roe_vals.append(v)
                if roe_vals:
                    break
        roe = (sum(roe_vals) / len(roe_vals)) if roe_vals else ((ni / eq) if (ni and eq and eq > 0) else None)
        bvps = (eq / sh) if (eq and sh and sh > 0) else None
        dps = (abs(divs) / sh) if (divs and sh and sh > 0) else None
        payout = max(0.05, min(0.85, abs(divs) / ni)) if (divs and ni and ni > 0) else 0.35

        beta = DEFAULT_BETA_BANK if template == "bank" else DEFAULT_BETA_INSURANCE
        ke = DEFAULT_RF + beta * DEFAULT_ERP
        g_short = min(0.08, max(0.02, (roe or 0.10) * (1.0 - payout) * 0.5))

        if bvps and roe is not None:
            ri_res = residual_income(bvps_0=bvps, roe=roe, ke=ke, payout=payout)
            if ri_res:
                methods.append(ri_res)
            # Sustainable ROE for perpetual Justified P/B-ROE fades excess ROE halfway toward Ke (capped at +6pp)
            sust_roe = ke + 0.5 * min(max(roe - ke, -0.06), 0.06) if roe > ke else roe
            pb_res = pb_roe_justified(bvps_0=bvps, roe=sust_roe, ke=ke, g_term=DEFAULT_TERM_G)
            if pb_res:
                methods.append(pb_res)
        if dps and dps > 0:
            ddm_res = ddm_two_stage(dps_0=dps, ke=ke, g_short=g_short, g_term=DEFAULT_TERM_G)
            if ddm_res:
                methods.append(ddm_res)

    # 3. Relative Valuation (own 5-year historical P/E, P/B, EV/Sales bands from accumulated prices + statements)
    if std:
        fy_objs = [y for y in (std.get("years") or []) if y.get("kind") == "annual"][:5]
        years = [y["label"] for y in fy_objs]
        year_ends = {y["label"]: y["end"] for y in fy_objs}
        y_prices = _year_end_prices(db, ticker, year_ends)
        eps_s = _std_series(std, "eps_diluted", years)
        eq_s = _std_series(std, "equity_parent", years)
        sh_s = _std_series(std, "shares_diluted_weighted", years)
        rev_s = _std_series(std, "revenue", years)
        cash_s = _std_series(std, "cash", years)
        debt_s = _std_series(std, "debt_noncurrent", years)

        pe_hist, pb_hist, evs_hist = [], [], []
        for y in years:
            px = y_prices.get(y)
            if not px:
                continue
            if eps_s.get(y, 0) > 0:
                pe_hist.append(px / eps_s[y])
            if eq_s.get(y, 0) > 0 and sh_s.get(y, 0) > 0:
                pb_hist.append(px / (eq_s[y] / sh_s[y]))
            if template == "general" and rev_s.get(y, 0) > 0 and sh_s.get(y, 0) > 0:
                ev = px * sh_s[y] + debt_s.get(y, 0.0) - cash_s.get(y, 0.0)
                if ev > 0:
                    evs_hist.append(ev / rev_s[y])

        mult_inputs: list[MultipleInput] = []
        latest_eps = _latest_std_val(std, "eps_diluted", years)
        latest_eq = _latest_std_val(std, "equity_parent", years)
        latest_sh = _latest_std_val(std, "shares_diluted_weighted", years)
        latest_rev = _latest_std_val(std, "revenue", years)
        latest_net_debt = (_latest_std_val(std, "debt_noncurrent", years) or 0.0) - (_latest_std_val(std, "cash", years) or 0.0)

        pe_band = _quantile_band(pe_hist)
        if pe_band and latest_eps and latest_eps > 0:
            mult_inputs.append(MultipleInput("pe", latest_eps, *pe_band))
        pb_band = _quantile_band(pb_hist)
        if pb_band and latest_eq and latest_sh and latest_sh > 0:
            mult_inputs.append(MultipleInput("pb", latest_eq / latest_sh, *pb_band))
        evs_band = _quantile_band(evs_hist)
        if evs_band and latest_rev and latest_sh and latest_sh > 0:
            mult_inputs.append(MultipleInput("ev_sales", latest_rev, *evs_band, net_debt=latest_net_debt, shares=latest_sh))

        rel_res = relative_valuation(mult_inputs)
        if rel_res:
            methods.append(rel_res)

    # 4. Reverse DCF (market-implied 5-year FCF CAGR)
    reverse_dcf = None
    if template == "general" and price and price > 0:
        wacc = (run.wacc if (run and run.wacc) else None) or 0.09
        term_g = (run.terminal_g if (run and run.terminal_g is not None) else None) or DEFAULT_TERM_G
        fcf_0, net_cash, shares, base_g = None, 0.0, None, None
        if calc:
            shares = _dec(calc.get("shares") or calc.get("shares_diluted"))
            net_cash = _dec(calc.get("bridge_total")) or 0.0
            proj = calc.get("projections") or calc.get("base_projection") or calc.get("projection") or []
            if isinstance(proj, list) and proj:
                first_fcff = _dec(proj[0].get("fcff") or proj[0].get("fcf"))
                last_fcff = _dec(proj[-1].get("fcff") or proj[-1].get("fcf"))
                if first_fcff and first_fcff > 0:
                    fcf_0 = first_fcff
                    if last_fcff and last_fcff > 0 and len(proj) > 1:
                        base_g = (last_fcff / first_fcff) ** (1.0 / (len(proj) - 1)) - 1.0
        if (fcf_0 is None or shares is None) and std:
            fy_objs = [y for y in (std.get("years") or []) if y.get("kind") == "annual"]
            years = [y["label"] for y in fy_objs]
            fcf_0 = fcf_0 or _latest_std_val(std, "free_cash_flow", years)
            shares = shares or _latest_std_val(std, "shares_diluted_weighted", years)
            net_cash = (_latest_std_val(std, "cash", years) or 0.0) - (_latest_std_val(std, "debt_noncurrent", years) or 0.0)
        if fcf_0 and fcf_0 > 0 and shares and shares > 0:
            # Handle FX conversion when model_ccy != trading_ccy (e.g., CNY model, HKD price)
            fx = _dec((calc or {}).get("fx_if_any", {}).get("rate")) if calc else None
            price_model = (price / fx) if (fx and fx > 0) else price
            rev_res = solve_implied_growth(
                price=price_model, fcf_0=fcf_0, wacc=wacc, terminal_g=term_g,
                net_cash=net_cash, shares=shares, base_growth=base_g,
            )
            if rev_res:
                reverse_dcf = rev_res.to_dict()

    comp = build_composite(template=template, methods=methods, price=price, custom_weights=custom_weights)
    return {
        "ticker": ticker,
        "currency": currency,
        **comp,
        "methods": [m.to_dict() for m in methods],
        "reverse_dcf": reverse_dcf,
    }

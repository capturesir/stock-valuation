"""Build cfg, run five scenarios, produce calc JSON. Math stays in fcff_engine / method_a."""
from __future__ import annotations

import copy
import datetime as dt
from typing import Any

from ..engine.fcff_engine import D, discount_block, run, build_json, save
from ..engine import method_a as MA
from ..engine.verify_fcff import verify
from . import rules


SCEN_NAMES = ["悲觀", "基準", "樂觀", "靜態", "9%靜態"]


def _make_rows(spec: dict, growths, margins, zero_dnwc=False, capex_pcts=None) -> list[dict]:
    capex_pcts = capex_pcts or spec["capex_pcts"]
    da_pcts = spec["da_pcts"]
    tax = spec["tax_rate"]
    dnwc_k = spec["dnwc_pct"]
    years = spec["year_labels"]
    discs = spec["discount_periods"]
    periods = spec["period_lengths"]
    prev = spec["prev_revenue_for_first"]
    rows = []
    for i, y in enumerate(years):
        g = growths[i]
        length = periods[i]
        if spec.get("explicit_revenues") and spec["explicit_revenues"][i] is not None and growths is spec.get("_base_growths_flag"):
            # only used if we later set a flag; default path uses growth
            pass
        if i == 0 and spec.get("first_revenue") is not None and abs(g - spec["growths"][0]) < 1e-15 and not zero_dnwc and capex_pcts == spec["capex_pcts"] and margins == spec["margins"]:
            rev = spec["first_revenue"]
        elif i == 0 and spec.get("first_revenue") is not None and not zero_dnwc and capex_pcts == spec["capex_pcts"] and margins[i] == spec["margins"][0] and abs(g - spec["growths"][0]) < 1e-12:
            rev = spec["first_revenue"]
        elif i == 0 and spec.get("first_revenue") is not None and zero_dnwc:
            # static: if first year is a stub with locked revenue, keep stub revenue only if spec says so
            if spec.get("static_keeps_first_revenue"):
                rev = spec["first_revenue"]
                g = spec["growths"][0]  # overwritten below to 0 except when stub locked — handled by caller
            else:
                rev = prev * (1 + g) if g != 0 or prev else (spec.get("first_revenue") or prev)
        else:
            if i == 0:
                if spec.get("first_revenue") is not None and not zero_dnwc:
                    # bear/bull change first-year growth vs locked explicit revenue
                    if abs(g - spec["growths"][0]) > 1e-12:
                        # rebase first revenue from prev
                        rev = prev * (1 + g) if prev else spec["first_revenue"]
                    else:
                        rev = spec["first_revenue"]
                else:
                    rev = (spec.get("first_revenue") if (spec.get("first_revenue") is not None and not zero_dnwc) else prev * (1 + g))
            else:
                rev = prev * (1 + g)
        # clean first-year static
        if zero_dnwc:
            if i == 0 and spec.get("static_keeps_first_revenue") and spec.get("first_revenue") is not None:
                rev = spec["first_revenue"]
            elif i == 0:
                rev = spec.get("static_base_revenue") or spec.get("full_year_base_revenue") or prev
            else:
                rev = prev  # g should be 0
        da_p = da_pcts[i] if i < len(da_pcts) else da_pcts[-1]
        cpx_p = capex_pcts[i] if i < len(capex_pcts) else capex_pcts[-1]
        cap = rev * cpx_p
        da = rev * da_p
        if zero_dnwc:
            dnwc = 0.0
        else:
            if i == 0 and spec.get("first_delta_nwc") is not None and abs(g - spec["growths"][0]) < 1e-12 and margins == spec["margins"] and capex_pcts == spec["capex_pcts"]:
                dnwc = spec["first_delta_nwc"]
            else:
                dnwc = dnwc_k * (rev - prev)
        row = {
            "year": y,
            "period_length_years": length,
            "revenue_growth": 0.0 if (zero_dnwc and not (i == 0 and spec.get("static_keeps_first_revenue"))) else g,
            "revenue_growth_base": spec["growth_base_notes"][i] if i < len(spec.get("growth_base_notes") or []) else "對比上一期",
            "revenue": rev,
            "ebit_margin": margins[i],
            "tax_rate": tax,
            "da": da,
            "da_pct_revenue": da_p,
            "capex": cap,
            "capex_pct_revenue": cpx_p,
            "delta_nwc": dnwc,
            "delta_nwc_rule": "營收持平 → ΔNWC＝0（靜態情境）" if zero_dnwc else spec.get("dnwc_rule") or f"ΔNWC = {dnwc_k:.1%} × ΔRevenue",
            "discount_period": discs[i],
            "growth_rationale": (spec.get("growth_rationales") or [""] * n_safe(spec))[i] if i < len(spec.get("growth_rationales") or []) else "",
            "margin_rationale": (spec.get("margin_rationales") or [""] * 20)[i] if i < len(spec.get("margin_rationales") or []) else "",
            "reinvestment_rationale": (spec.get("reinvestment_rationales") or [""] * 20)[i] if i < len(spec.get("reinvestment_rationales") or []) else "",
            "sources": spec.get("row_sources") or [],
        }
        if i == 0 and spec.get("stub_fields"):
            row.update(spec["stub_fields"])
        rows.append(row)
        prev = rev if not (i == 0 and spec.get("stub_fields") and spec["stub_fields"].get("fy_full_revenue")) else spec["stub_fields"]["fy_full_revenue"]
        # After a stub, subsequent growth is vs full year revenue
        if i == 0 and row.get("fy2026_revenue"):
            prev = row["fy2026_revenue"]
        elif i == 0 and spec.get("full_year_after_first") is not None and not zero_dnwc:
            # when first row is stub, chain next year from full FY
            if not (zero_dnwc):
                prev = spec["full_year_after_first"]
                if abs(g - spec["growths"][0]) > 1e-12 or margins != spec["margins"]:
                    # bear/bull: rebuild full year = ytd + stub rev
                    if spec.get("ytd_actual_revenue") is not None:
                        prev = spec["ytd_actual_revenue"] + rev
        elif i == 0 and spec.get("ytd_actual_revenue") is not None and spec.get("first_is_stub"):
            prev = spec["ytd_actual_revenue"] + rev
    return rows


def n_safe(spec):
    return len(spec["year_labels"])


def _with_nwc_rate(spec: dict) -> dict:
    """Persist the engine ΔNWC rate on base_year so Excel export does not invent a default."""
    by = dict(spec.get("base_year") or {})
    if by.get("nwc_rate") is None and spec.get("dnwc_pct") is not None:
        by["nwc_rate"] = spec["dnwc_pct"]
    return by


def build_and_run(spec: dict, out_dir: str) -> dict:
    """spec is a fully documented input dict (see orchestrator)."""
    ticker = spec["ticker"]
    P, SH = spec["price"], spec["shares_m"]
    tax = spec["tax_rate"]
    D_fin = spec["debt_m"]
    E_mkt = P * SH * spec.get("fx_to_model_for_market_cap", 1.0)
    # register industry if needed
    if ticker not in MA.IND:
        raise SystemExit(f"{ticker} 未在 method_a.IND 登記。請先以 Damodaran 出處 register_ticker。")
    MA.check_g(spec["terminal_g"], spec["rf"]["value"], ticker)
    beta_ref, beta_ado, beta_info = MA.beta(
        ticker, D_fin, E_mkt, tax, spec.get("beta_reg"), spec.get("beta_reg_url") or "", spec.get("beta_reg_date") or ""
    )
    disc, cap = discount_block(
        rf=spec["rf"], erp=MA.erp(), crp=spec["crp"],
        beta_ref=beta_ref, beta_adopted=beta_ado,
        kd_pre=spec["kd_pre"], tax=spec["tax_d"],
        E=D(E_mkt, spec["E_basis"], spec.get("price_source") or "", spec.get("price_url") or "", spec["price_date"]),
        Dv=D(D_fin, spec["D_basis"], spec.get("D_source") or "", spec.get("D_url") or "", spec.get("D_date") or ""),
    )
    # attach first_is_stub convenience
    if spec.get("first_is_stub"):
        spec = dict(spec)
        spec.setdefault("full_year_after_first", spec.get("full_year_after_first"))

    rows = _make_rows(spec, spec["growths"], spec["margins"])
    cfg = {
        "asset": spec["asset"], "ticker": ticker, "market": spec["market"], "currency": spec["currency"],
        "units": spec["units"], "valuation_date": spec["valuation_date"], "price": P,
        "price_date": spec["price_date"], "price_source_url": spec["price_url"],
        "convention": spec["convention"], "forecast_years": spec["forecast_years_label"],
        "horizon_rationale": spec["horizon_rationale"],
        "discount": disc, "capital_structure": cap,
        "base_year": _with_nwc_rate(spec), "rows": rows, "bridge": spec["bridge"],
        "terminal_in": {
            "method": spec.get("tv_method") or "value_driver",
            "g": spec["terminal_g"], "ronic": spec["ronic"],
            "g_rationale": spec["g_rationale"], "method_rationale": spec["method_rationale"],
        },
        "shares": SH, "fx_rate": spec.get("fx_rate") or 1.0,
        "shares_source": spec["shares_source"],
        "fx_if_any": spec.get("fx_if_any") or {"pair": None, "rate": 1.0, "applied": "不適用"},
        "corrections": spec.get("corrections") or [],
    }

    def fair_custom(growths, margins, g, zero_dnwc=False, capex_pcts=None, shares=None, wacc=None):
        c = copy.deepcopy(cfg)
        # For bear/bull, don't use locked first_revenue unless growth unchanged
        sp = dict(spec)
        if growths != spec["growths"]:
            # keep first_revenue only if first year growth unchanged
            pass
        c["rows"] = _make_rows(sp, growths, margins, zero_dnwc=zero_dnwc, capex_pcts=capex_pcts)
        if shares is not None:
            c["shares"] = shares
        tin = dict(c["terminal_in"]); tin["g"] = g
        c["terminal_in"] = tin
        return run(c, wacc=wacc)[5], c

    g_bear = rules.scenario_growths(spec["growths"], spec.get("bear_g_delta", -0.03), 4, skip_stub=spec.get("first_is_stub", False))
    g_bull = rules.scenario_growths(spec["growths"], spec.get("bull_g_delta", 0.03), 4, skip_stub=spec.get("first_is_stub", False))
    m_bear = rules.scenario_margins(spec["margins"], spec.get("bear_m_delta", -0.02), 4, skip_stub=spec.get("first_is_stub", False))
    m_bull = rules.scenario_margins(spec["margins"], spec.get("bull_m_delta", 0.02), 4, skip_stub=spec.get("first_is_stub", False))
    g_term_bear = spec.get("bear_terminal_g") or max(min(spec["terminal_g"] - 0.01, spec["rf"]["value"]), 0.0)
    g_term_bull = spec.get("bull_terminal_g") or min(spec["terminal_g"] + 0.005, spec["rf"]["value"])

    fv_bear, _ = fair_custom(g_bear, m_bear, g_term_bear)
    fv_bull, _ = fair_custom(g_bull, m_bull, g_term_bull)

    # static: g=0, terminal 0, delta nwc 0; optionally keep stub revenue
    g0 = [0.0] * len(spec["growths"])
    sp_static = dict(spec)
    fv_static, _ = fair_custom(g0, spec["margins"], 0.0, zero_dnwc=True)

    floor_pct = spec["capex_floor_pct"]
    floor_cpx = [floor_pct] * len(spec["growths"])
    sh_floor = spec["shares_floor_m"]
    fv_9_base, _ = fair_custom(g0, spec["margins"], 0.0, zero_dnwc=True, capex_pcts=floor_cpx, shares=SH, wacc=0.09)
    fv_9, _ = fair_custom(g0, spec["margins"], 0.0, zero_dnwc=True, capex_pcts=floor_cpx, shares=sh_floor, wacc=0.09)
    floor_note = None
    if fv_9 is not None and fv_static is not None and fv_9 > fv_static and spec.get("generic_spec"):
        # Heavy-capex names: the static scenario keeps the historical capex path
        # while the 9% floor uses the sustainable ratio, so the "floor" can print
        # above the static case. Say so instead of letting the reader guess.
        floor_note = (
            f"9%靜態（{fv_9:.2f}）高於靜態（{fv_static:.2f}）：靜態情境沿用歷史資本開支路徑，"
            "9%地板用可持續比例，兩者的 capex 假設不同，不是計算錯誤。"
        )

    wacc_v = disc["wacc"]["value"]
    # sensitivity grid around wacc and g
    def _grid(center, span, n=5):
        step = span / (n // 2)
        return [round(center + step * (i - n // 2), 5) for i in range(n)]
    sens_w = spec.get("sens_waccs") or _grid(wacc_v, 0.04, 5)
    sens_g = spec.get("sens_gs") or sorted(set([max(0, spec["terminal_g"] + d) for d in (-0.01, -0.005, 0, 0.005, 0.01) if spec["terminal_g"] + d <= spec["rf"]["value"] + 1e-12]))
    if len(sens_g) < 3:
        sens_g = [0.0, min(spec["terminal_g"], spec["rf"]["value"]), spec["rf"]["value"]]

    revisions = spec.get("revisions") or [{
        "date": spec["valuation_date"], "item": "理論價（首次估值）", "old": None, "new": None,
        "reason": "以本系統首次估值；五情境；方法 A WACC；不寫入關注名單或訊號",
    }]
    extra = dict(spec.get("extra") or {})
    extra.setdefault("coverage_note", "本次僅估值；不改 watchlist／signals／pending_removal")
    extra.setdefault("beta_method_a_detail", beta_info)
    extra.setdefault("method_a", {"adopted": MA.ADOPTED, "rule": "coverage/wacc_review.md §採用"})
    extra.setdefault("assumption_source", spec.get("assumption_source") or "rules_fallback")
    extra.setdefault("not_investment_advice", True)
    extra.setdefault("shares_floor_detail", {
        **(spec.get("shares_floor_detail") or {}),
        "static_9pct_fv_base_shares": fv_9_base,
        "static_9pct_fv_floor_shares": fv_9,
    })
    extra.setdefault("capex_floor_detail", spec.get("capex_floor_detail") or {})
    extra.setdefault("data_gaps", spec.get("data_gaps") or [])
    extra.setdefault("ib_compare", spec.get("ib_compare") or {})
    extra.setdefault("growth_table", None)  # filled after
    extra.setdefault("cfo_fcff_crosscheck", spec.get("cfo_fcff_crosscheck"))
    extra.setdefault("llm_status", spec.get("llm_status") or {})

    scenarios = [
        {"name": "悲觀", "fair_value": fv_bear,
         "changes": f"首 {4} 個完整預測年增長 {spec.get('bear_g_delta', -0.03):+.0%}、EBIT 利潤率 {spec.get('bear_m_delta', -0.02):+.0%}；終值 g={g_term_bear:.2%}",
         "growths": g_bear, "margins": m_bear, "terminal_g": g_term_bear},
        {"name": "基準", "fair_value": None, "changes": "主模型（規則或 LLM 假設＋方法 A WACC）",
         "growths": spec["growths"], "margins": spec["margins"], "terminal_g": spec["terminal_g"]},
        {"name": "樂觀", "fair_value": fv_bull,
         "changes": f"首 {4} 個完整預測年增長 {spec.get('bull_g_delta', 0.03):+.0%}、EBIT 利潤率 {spec.get('bull_m_delta', 0.02):+.0%}；終值 g={g_term_bull:.2%}",
         "growths": g_bull, "margins": m_bull, "terminal_g": g_term_bull},
        {"name": "靜態", "fair_value": fv_static,
         "changes": "預測期營收增長＝0、終值 g＝0；WACC／利潤率／再投資比率／橋接同基準；ΔNWC＝0",
         "growths": g0, "margins": spec["margins"], "terminal_g": 0.0},
        {"name": "9%靜態", "fair_value": fv_9,
         "changes": (f"估值地板：增長＝0、終值 g＝0、折現固定 9%；Capex＝{floor_pct:.2%}×營收；"
                     f"股數＝{sh_floor:.3f}m（基準 {SH:.3f}m；原股數對照 9%靜態={fv_9_base:.4f}）；ΔNWC＝0"),
         "growths": g0, "margins": spec["margins"], "terminal_g": 0.0,
         "capex_pcts": floor_cpx, "shares": sh_floor, "wacc": 0.09},
    ]

    out = build_json(cfg, sens_w, sens_g, scenarios=scenarios, revisions=revisions, extra=extra)
    if floor_note:
        out.setdefault("data_gaps", []).append(floor_note)
    # A negative or non-finite theoretical value means the model does not fit the
    # company (loss-making, over-levered, or a bad base year). verify_fcff only
    # checks arithmetic, so flag it explicitly instead of shipping it silently.
    if out["fair_value_per_share"] is None or out["fair_value_per_share"] <= 0:
        warning = (
            f"理論價為負或無效（{out['fair_value_per_share']}），代表 FCFF 五情境模型不適用於此公司"
            "（例如仍大幅虧損、資本結構極端或基準年異常）；此結果不可用作估值參考。"
        )
        out.setdefault("data_gaps", []).insert(0, warning)
        out["result_warning"] = warning

    for s in out["scenarios"]:
        if s["name"] == "基準":
            s["fair_value"] = out["fair_value_per_share"]
    for r in out["revisions"]:
        if str(r.get("item", "")).startswith("理論價"):
            r["new"] = round(out["fair_value_per_share"], 4)
    out = MA.fix_revisions(out, date=spec["valuation_date"])
    # growth table from actual scenario paths
    out["growth_table"] = _growth_table(out, scenarios, spec)
    path = save(out, date=spec["valuation_date"], out_dir=out_dir)
    d, fair, errs = verify(path)
    return {"path": path, "json": out, "verify_pass": not errs, "verify_errors": errs, "beta_info": beta_info}


def _growth_table(out: dict, scenarios: list, spec: dict) -> dict:
    years = [p["year"] for p in out["projections"]]
    def col(name):
        sc = next(s for s in scenarios if s["name"] == name)
        return list(sc["growths"])
    consensus = spec.get("consensus_growths") or [None] * len(years)
    # pad
    def pad(xs):
        xs = list(xs)
        while len(xs) < len(years):
            xs.append(None)
        return xs[:len(years)]
    table = {
        "years": years,
        "本框架-悲觀": pad(col("悲觀")),
        "本框架-基準": pad(col("基準")),
        "本框架-樂觀": pad(col("樂觀")),
        "本框架-靜態": [0.0] * len(years),
        "本框架-9%靜態": [0.0] * len(years),
        "一致預期營收增長": pad(consensus),
        "終值g": {
            "悲觀": next(s["terminal_g"] for s in scenarios if s["name"]=="悲觀"),
            "基準": out["terminal"]["g"],
            "樂觀": next(s["terminal_g"] for s in scenarios if s["name"]=="樂觀"),
            "靜態": 0.0, "9%靜態": 0.0,
        },
        "基準EBIT利潤率": [p["ebit_margin"] for p in out["projections"]],
        "notes": spec.get("growth_table_notes") or [],
        "sources": spec.get("growth_table_sources") or [],
    }
    return table

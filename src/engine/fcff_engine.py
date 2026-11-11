"""Shared FCFF DCF engine (2026-10-01 standard).

Conventions (apply to every asset):
  * All projections, EV and bridge items are in the MODEL currency (USD millions); per-share fair value is then
    converted into the trading currency: fair_value_per_share = equity_value / shares * fx_rate (fx_rate = 1 for USD lines).
  * Valuation date 2026-09-30. Latest balance sheet = 2026-06-30, so the first projection row is the H2-2026 stub
    (1 Jul - 31 Dec 2026, full half-year FCFF, because cash generated after 6/30 is not in the balance sheet).
  * Mid-period discounting measured from the valuation date: stub mid-point 2026-10-01 -> t = 0.00;
    FY2027 mid-point 2027-07-01 -> t = 0.75; FY(2027+k) -> t = 0.75 + k.  DF = 1 / (1 + WACC) ** t.
  * FCFF = NOPAT + D&A - Capex - dNWC, NOPAT = EBIT * (1 - tax), EBIT = Revenue * EBIT margin.
  * Terminal value (mid-year consistent, corrected 2026-10-01 07:55):
        method "value_driver": FCFF_{N+1} = NOPAT_N * (1+g) * (1 - g/RONIC)
        method "fcff_growth" : FCFF_{N+1} = FCFF_N * (1+g)
        TV = FCFF_{N+1} / (WACC - g) is the value, at the final forecast year's mid-point t_N, of flows arriving at
        t_N+1, t_N+2, ... (same mid-year timing as the explicit years), so PV_TV = TV / (1+WACC) ** t_N.
        (Equivalent to TV_end-year = TV * (1+WACC)**0.5 discounted at t_N + 0.5.)
  * WACC = We*Ke + Wd*Kd*(1-t); Ke = Rf + Beta*(ERP + CRP); We = E/(E+D), E = price*diluted shares (in model ccy).
"""
import copy, json, os

def D(value, basis, source_name="", url="", data_date=""):
    return {"value": value, "basis": basis, "source_name": source_name, "url": url, "data_date": data_date}

def discount_block(rf, erp, crp, beta_ref, beta_adopted, kd_pre, tax, E, Dv):
    """rf..: dicts made with D(). E, Dv: dicts with value in model-ccy millions."""
    ke = rf["value"] + beta_adopted["value"] * (erp["value"] + crp["value"])
    kd_after = kd_pre["value"] * (1 - tax["value"])
    we = E["value"] / (E["value"] + Dv["value"]); wd = 1 - we
    wacc = we * ke + wd * kd_after
    return {
        "rf": rf, "erp": erp, "crp": crp, "beta_ref": beta_ref, "beta_adopted": beta_adopted,
        "ke": D(ke, "計算：ke = rf + beta_adopted × (erp + crp)", "模型計算"),
        "kd_pre_tax": kd_pre, "tax_rate": tax,
        "kd_after_tax": D(kd_after, "計算：kd_pre_tax × (1 − tax_rate)", "模型計算"),
        "weight_e": D(we, f"計算：E/(E+D)；E = {E['basis']}", E.get("source_name", ""), E.get("url", ""), E.get("data_date", "")),
        "weight_d": D(wd, f"計算：D/(E+D)；D = {Dv['basis']}", Dv.get("source_name", ""), Dv.get("url", ""), Dv.get("data_date", "")),
        "wacc": D(wacc, "計算：weight_e × ke + weight_d × kd_after_tax", "模型計算"),
    }, {"E_model_ccy_m": E["value"], "D_model_ccy_m": Dv["value"]}

def run(cfg, wacc=None, g=None):
    """cfg['rows']: list of dicts with year, revenue, ebit_margin, tax_rate, da, capex, delta_nwc, discount_period
    (+ rationale text). Returns (projections, terminal, sum_pv, ev, equity, fair)."""
    wacc = cfg["discount"]["wacc"]["value"] if wacc is None else wacc
    g = cfg["terminal_in"]["g"] if g is None else g
    proj = []
    for r in cfg["rows"]:
        ebit = r["revenue"] * r["ebit_margin"]
        nopat = ebit * (1 - r["tax_rate"])
        fcff = nopat + r["da"] - r["capex"] - r["delta_nwc"]
        df = 1 / (1 + wacc) ** r["discount_period"]
        row = dict(r); row.update(ebit=ebit, nopat=nopat, fcff=fcff, discount_factor=df, pv_fcff=fcff * df)
        proj.append(row)
    last = proj[-1]
    t_tv = last["discount_period"]  # mid-year consistent: TV valued at t_N (see docstring)
    if cfg["terminal_in"]["method"] == "value_driver":
        ronic = cfg["terminal_in"]["ronic"]
        fcff_next = last["nopat"] * (1 + g) * (1 - g / ronic)
    else:
        fcff_next = last["fcff"] * (1 + g)
    tv = fcff_next / (wacc - g)
    pv_tv = tv / (1 + wacc) ** t_tv
    sum_pv = sum(p["pv_fcff"] for p in proj)
    ev = sum_pv + pv_tv
    bridge_total = sum(b["value"] if b["sign"] == "+" else -b["value"] for b in cfg["bridge"])
    equity = ev + bridge_total
    fair = equity / cfg["shares"] * cfg["fx_rate"]
    term = {"method": cfg["terminal_in"]["method"], "g": g, "ronic": cfg["terminal_in"].get("ronic"),
            "discount_period_tv": t_tv, "fcff_next": fcff_next, "terminal_value": tv, "pv_terminal": pv_tv,
            "tv_share_of_ev": pv_tv / ev,
            "tv_timing_note": "TV = fcff_next ÷ (WACC − g) 為最後預測年年中 t_N 時點的價值（其後現金流同樣按年中發生）；pv_terminal = TV ÷ (1+WACC)^t_N；等同『年末 TV × (1+WACC)^0.5 再按 t_N+0.5 折現』"}
    return proj, term, sum_pv, ev, equity, fair

COMMON_CORRECTIONS = [
    {"item": "終值折現期（年中一致性）", "requested": "年中折現（題目要求明示慣例）；原版以 t_N+0.5 折現 Gordon 終值",
     "adopted": "終值按 t_N 折現（= 年末 TV × (1+WACC)^0.5 再按 t_N+0.5 折現）",
     "reason": "明確預測期採年中折現時，Gordon 公式得出的是 t_N 時點對其後『年中』現金流的價值；若仍按 t_N+0.5 折現，等於永續期改用年末慣例，約低估終值 (1+WACC)^0.5−1 ≈ 4.5–5.5%"},
    {"item": "營運資金定義（避免重覆計現金）", "requested": "ΔNWC",
     "adopted": "ΔNWC 只計非現金營運資金（應收＋存貨−應付等），不含現金、短期投資、有息負債及應付股息；現金及投資只在橋接加回",
     "reason": "避免現金同時出現在 NWC 與橋接而重覆計算"},
    {"item": "WACC 權重與橋接一致", "requested": "WACC 包含債務，EV 再減債務",
     "adopted": "WACC 權重：E = 9/30 市值（攤薄股數 × 收市價），D = 橋接中扣減的融資性債務（借款＋按準則屬債務的租賃）帳面值；其他類債務項（棄置準備、應付股息、非控制權益、稅項等）只在橋接扣減，不入 WACC 權重",
     "reason": "WACC 權重應反映融資來源的目標／市值結構；經營性準備及應付股息沒有獨立的市場資金成本，放入權重會扭曲 WACC；債務帳面值作市值近似（未有全部債券市價）"},
    {"item": "現金全數視為超額現金", "requested": "加回 excess cash",
     "adopted": "6/30 現金及短期投資全數加回，未另扣最低營運現金",
     "reason": "ΔNWC 按營收增量計提已涵蓋營運所需資金增長；公司未披露最低營運現金；此處理略偏樂觀，影響於報告註明"},
    {"item": "非控制權益、聯營／合營", "requested": "扣非控制權益、加聯營／合營",
     "adopted": "FCFF 按 100% 合併口徑，非控制權益按帳面值扣減；聯營／合營及金融投資按帳面值加回，其損益不計入 EBIT",
     "reason": "合併 FCFF 包含少數股東份額，必須扣除；聯營收益不在營業利益內，加回帳面值避免遺漏或重覆；帳面值是市值的近似（無公開市價）"},
]

def build_json(cfg, sens_waccs, sens_gs, scenarios, revisions, extra=None):
    proj, term, sum_pv, ev, equity, fair = run(cfg)
    corrections = COMMON_CORRECTIONS + cfg.get("corrections", [])
    revisions = [dict(r, reason=r["reason"] + "；另含終值年中一致性修正（終值按 t_N 折現，見 spec_deviations）") if str(r["item"]).startswith("理論價") else r for r in revisions]
    revisions = list(revisions) + [{"date": "2026-10-01", "item": "方法修正：" + c["item"], "old": c["requested"], "new": c["adopted"], "reason": c["reason"]} for c in corrections]
    term.update({"g_rationale": cfg["terminal_in"]["g_rationale"], "method_rationale": cfg["terminal_in"]["method_rationale"]})
    matrix = [[run(cfg, w, gg)[5] for gg in sens_gs] for w in sens_waccs]
    out = {k: cfg[k] for k in ["asset", "ticker", "market", "currency", "units", "valuation_date", "price", "price_date",
                               "price_source_url"]}
    out.update({
        "method": "DCFF",
        "method_note": "FCFF DCF（公司自由現金流）→ EV → 股權橋接 → 每股攤薄理論價",
        "discounting_convention": cfg["convention"],
        "forecast_years": cfg["forecast_years"], "forecast_horizon_rationale": cfg["horizon_rationale"],
        "discount": cfg["discount"], "capital_structure": cfg["capital_structure"],
        "base_year": cfg["base_year"],
        "projections": [{k: p.get(k) for k in ["year", "period_length_years", "revenue_growth", "revenue_growth_base", "h1_actual_revenue", "fy2026_revenue", "revenue",
                         "ebit_margin", "ebit", "tax_rate", "nopat", "da", "da_pct_revenue", "capex", "capex_pct_revenue",
                         "delta_nwc", "delta_nwc_rule", "fcff", "discount_period", "discount_factor", "pv_fcff",
                         "drivers", "growth_rationale", "margin_rationale", "reinvestment_rationale", "sources"]} for p in proj],
        "terminal": term, "sum_pv_fcff": sum_pv, "enterprise_value": ev,
        "bridge": cfg["bridge"], "bridge_total": sum(b["value"] if b["sign"] == "+" else -b["value"] for b in cfg["bridge"]),
        "equity_value": equity, "fx_if_any": dict(cfg["fx_if_any"], per_share_fx=cfg["fx_rate"], per_share_fx_note="fair_value_per_share = equity_value ÷ shares × per_share_fx"), "shares": cfg["shares"], "shares_source": cfg["shares_source"],
        "fair_value_per_share": fair,
        "gap_vs_price": {"fair_minus_price_over_price": fair / cfg["price"] - 1, "price_over_fair_minus_1": cfg["price"] / fair - 1,
                         "price_over_fair_ratio": cfg["price"] / fair, "flag_over_2x": cfg["price"] > 2 * fair},
        "sensitivity": {"wacc_list": sens_waccs, "g_list": sens_gs, "matrix": matrix,
                        "note": "每格 = 以該 WACC 及 g 重算之每股理論價（交易貨幣），其餘輸入不變"},
        "scenarios": scenarios, "revisions": revisions, "spec_deviations": corrections,
    })
    if extra: out.update(extra)
    return out

def save(out, date="2026-10-01", out_dir=None):
    d = out_dir or os.environ.get("VALUATION_CALC_DIR") or "/workspace/invest/models/calc"
    os.makedirs(d, exist_ok=True)
    dated = os.path.join(d, f"{date}_{out['ticker']}.json")
    latest = os.path.join(d, f"{out['ticker']}_latest.json")
    for fn in (dated, latest):
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return dated

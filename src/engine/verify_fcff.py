"""Independent verifier for calc/*_latest.json (FCFF DCF, 2026-10-01 standard).
Recomputes every derived number from the JSON's own inputs WITHOUT importing the engine:
  discount block (ke, kd_after, weights, wacc) -> per-year EBIT/NOPAT/FCFF/DF/PV -> terminal -> EV -> bridge -> equity
  -> fair value per share (and sensitivity matrix, dated-copy identity, CNOOC revenue drivers).
Usage: python verify_fcff.py [ticker ...]   (default: all *_latest.json).  Exit code 1 on any mismatch."""
import json, glob, os, sys, math
CALC = os.environ.get("VALUATION_CALC_DIR") or os.path.join(os.environ.get("INVEST_BASE", "/workspace/invest"), "models", "calc")
REL = 1e-9

def close(a, b, rel=REL, abs_=1e-6):
    return math.isclose(a, b, rel_tol=rel, abs_tol=abs_)

def recompute(d, wacc=None, g=None):
    disc = d["discount"]; v = lambda k: disc[k]["value"]
    W = v("wacc") if wacc is None else wacc
    t = d["terminal"]; g = t["g"] if g is None else g
    out = []
    for p in d["projections"]:
        ebit = p["revenue"] * p["ebit_margin"]; nopat = ebit * (1 - p["tax_rate"])
        fcff = nopat + p["da"] - p["capex"] - p["delta_nwc"]
        df = 1 / (1 + W) ** p["discount_period"]
        out.append(dict(ebit=ebit, nopat=nopat, fcff=fcff, df=df, pv=fcff * df))
    last = out[-1]
    if t["method"] == "value_driver":
        fn = last["nopat"] * (1 + g) * (1 - g / t["ronic"])
    else:
        fn = last["fcff"] * (1 + g)
    tv = fn / (W - g); pv_tv = tv / (1 + W) ** t["discount_period_tv"]
    sum_pv = sum(o["pv"] for o in out); ev = sum_pv + pv_tv
    br = sum(b["value"] if b["sign"] == "+" else -b["value"] for b in d["bridge"])
    eq = ev + br
    fair = eq / d["shares"] * d["fx_if_any"]["per_share_fx"]
    return out, fn, tv, pv_tv, sum_pv, ev, br, eq, fair

def verify(path):
    d = json.load(open(path, encoding="utf-8")); errs = []
    def chk(name, got, exp, rel=REL, abs_=1e-6):
        if not close(got, exp, rel, abs_): errs.append(f"{name}: json={got} recomputed={exp}")
    disc = d["discount"]; v = lambda k: disc[k]["value"]
    for k in ["rf","erp","crp","beta_ref","beta_adopted","ke","kd_pre_tax","tax_rate","kd_after_tax","weight_e","weight_d","wacc"]:
        for f in ["value","basis","source_name","url","data_date"]:
            if f not in disc[k]: errs.append(f"discount.{k} missing {f}")
    chk("ke", v("ke"), v("rf") + v("beta_adopted") * (v("erp") + v("crp")))
    chk("kd_after_tax", v("kd_after_tax"), v("kd_pre_tax") * (1 - v("tax_rate")))
    E, Dm = d["capital_structure"]["E_model_ccy_m"], d["capital_structure"]["D_model_ccy_m"]
    chk("weight_e", v("weight_e"), E / (E + Dm)); chk("weight_d", v("weight_d"), Dm / (E + Dm))
    chk("wacc", v("wacc"), v("weight_e") * v("ke") + v("weight_d") * v("kd_after_tax"))
    out, fn, tv, pv_tv, sum_pv, ev, br, eq, fair = recompute(d)
    for p, o in zip(d["projections"], out):
        y = p["year"]
        for k, kk in [("ebit","ebit"),("nopat","nopat"),("fcff","fcff"),("discount_factor","df"),("pv_fcff","pv")]:
            chk(f"{y}.{k}", p[k], o[kk])
        chk(f"{y}.fcff identity", p["fcff"], p["nopat"] + p["da"] - p["capex"] - p["delta_nwc"])
        if p.get("da_pct_revenue") is not None: chk(f"{y}.da_pct", p["da_pct_revenue"], p["da"] / p["revenue"])
    for a, b in zip(d["projections"][1:], d["projections"][:-1]):
        stub = b.get("period_length_years") == 0.5 or (b.get("fy2026_revenue") is not None and b.get("h1_actual_revenue") is not None)   # coverage-expansion stubs: 0.25/0.5/1.0 years
        prev = b["fy2026_revenue"] if stub else b["revenue"]
        if stub:
            chk("fy2026_revenue", b["fy2026_revenue"], b["h1_actual_revenue"] + b["revenue"])
        chk(f"{a['year']}.revenue_growth", a["revenue_growth"], a["revenue"] / prev - 1, 1e-6, 1e-6)
    t = d["terminal"]
    chk("fcff_next", t["fcff_next"], fn); chk("terminal_value", t["terminal_value"], tv); chk("pv_terminal", t["pv_terminal"], pv_tv)
    chk("tv_share", t["tv_share_of_ev"], pv_tv / ev); chk("sum_pv_fcff", d["sum_pv_fcff"], sum_pv)
    chk("enterprise_value", d["enterprise_value"], ev); chk("bridge_total", d["bridge_total"], br)
    chk("equity_value", d["equity_value"], eq)
    chk("fair_value_per_share", d["fair_value_per_share"], fair)
    if round(d["fair_value_per_share"], 2) != round(fair, 2): errs.append("fair value differs at 2dp")
    s = d["sensitivity"]
    for i, w in enumerate(s["wacc_list"]):
        for j, gg in enumerate(s["g_list"]):
            chk(f"sens[{i}][{j}]", s["matrix"][i][j], recompute(d, w, gg)[-1], 1e-9, 1e-6)
    # CNOOC: revenue & margin rebuilt from physical drivers
    u = d.get("unit_economics_calibration")
    if u:
        for p in d["projections"]:
            dr = p["drivers"]; vol = dr["sales_volume_mmboe"]; real = dr["brent_usd"] * u["realised_to_brent"]
            rev = vol * (u["liquids_share"] * real + u["gas_mcf_per_boe"] * u["gas_price_usd_mcf"])
            levy = vol * u["liquids_share"] * u["domestic_share"] * u["levy_k"] * max(real - 65, 0)
            ebit = rev - vol * dr["cash_cost_usd_boe"] - rev * u["other_tax_pct_rev"] - levy - vol * dr["dda_usd_boe"]
            chk(f"{p['year']}.driver_revenue", p["revenue"], rev); chk(f"{p['year']}.driver_ebit", p["ebit"], ebit)
            chk(f"{p['year']}.driver_da", p["da"], vol * dr["dda_usd_boe"])
    # MU: revenue rebuilt from DRAM / NAND(+other) bit-growth x ASP drivers, with continuity between years
    if d["projections"] and "dram_revenue" in (d["projections"][0].get("drivers") or {}):
        prev = None
        for p in d["projections"]:
            dr = p["drivers"]
            if prev is not None:
                chk(f"{p['year']}.dram_prev_continuity", dr["dram_revenue_prev"], prev["dram_revenue"])
                chk(f"{p['year']}.nand_prev_continuity", dr["nand_other_revenue_prev"], prev["nand_other_revenue"])
            chk(f"{p['year']}.dram_revenue", dr["dram_revenue"], dr["dram_revenue_prev"] * (1 + dr["dram_bit_growth"]) * (1 + dr["dram_asp_change"]))
            chk(f"{p['year']}.nand_revenue", dr["nand_other_revenue"], dr["nand_other_revenue_prev"] * (1 + dr["nand_bit_growth"]) * (1 + dr["nand_asp_change"]))
            chk(f"{p['year']}.revenue_from_drivers", p["revenue"], dr["dram_revenue"] + dr["nand_other_revenue"])
            prev = dr
        b = d["base_year"]["fy2026_split_used"]
        chk("FY2026 split", b["dram"] + b["nand_and_other"], d["base_year"]["revenue"])
        chk("first year growth", d["projections"][0]["revenue_growth"], d["projections"][0]["revenue"] / d["base_year"]["revenue"] - 1)
    # dated copy identical (same folder as the file, then CALC)
    folder = os.path.dirname(os.path.abspath(path))
    dated_all = sorted(glob.glob(os.path.join(folder, f"20??-??-??_{d['ticker']}.json")))
    if not dated_all:
        dated_all = sorted(glob.glob(os.path.join(CALC, f"20??-??-??_{d['ticker']}.json")))
    if not dated_all or open(dated_all[-1], "rb").read() != open(path, "rb").read():
        errs.append("dated copy differs from _latest")
    return d, fair, errs

if __name__ == "__main__":
    want = sys.argv[1:]
    paths = sorted(glob.glob(f"{CALC}/*_latest.json"))
    if want:
        files=[]; tick=[]
        for w in want:
            if w.endswith(".json") and os.path.exists(w):
                files.append(w)
            else:
                tick.append(w)
        if files and not tick:
            paths = files
        elif tick:
            paths = [p for p in paths if os.path.basename(p).replace("_latest.json", "") in tick] + files
    bad = 0
    for p in paths:
        d, fair, errs = verify(p)
        ok = not errs; bad += (not ok)
        print(f"{d['ticker']:8s} {'PASS' if ok else 'FAIL'}  fair(json)={d['fair_value_per_share']:.4f} recomputed={fair:.4f} {d['currency']}  "
              f"WACC={d['discount']['wacc']['value']:.4%} g={d['terminal']['g']:.2%} EV={d['enterprise_value']:,.0f} USDm  "
              f"price={d['price']} ({d['price_date']}) price/fair={d['price']/fair:.3f}{'  >2x FLAG' if d['price'] > 2*fair else ''}")
        for e in errs: print("    ", e)
    print("ALL PASS" if not bad else f"{bad} FAILED"); sys.exit(1 if bad else 0)

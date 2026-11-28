"""2D sensitivity matrices (WACC × terminal g, WACC × EBIT margin) and seeded Monte Carlo valuation simulator."""
from __future__ import annotations

import math
import random
from typing import Any, Optional


def _f(val: Any, default: Optional[float] = None) -> Optional[float]:
    if val is None:
        return default
    try:
        v = float(val)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _extract_model_params(calc: dict[str, Any]) -> Optional[dict[str, Any]]:
    proj = calc.get("projections") or calc.get("base_projection") or calc.get("projection") or []
    if not isinstance(proj, list) or not proj:
        return None
    wacc = _f(((calc.get("discount") or {}).get("wacc") or {}).get("value"))
    term_g = _f((calc.get("terminal") or {}).get("g"), 0.025)
    ronic = _f((calc.get("terminal") or {}).get("ronic"))
    shares = _f(calc.get("shares") or calc.get("shares_diluted"))
    if not shares or shares <= 0 or not wacc or wacc <= 0.01:
        return None

    # Compute bridge total = equity_value - enterprise_value (exact match to engine)
    ev = _f(calc.get("enterprise_value"))
    eq = _f(calc.get("equity_value"))
    if ev is not None and eq is not None:
        bridge_total = eq - ev
    else:
        bridge_total = _f(calc.get("bridge_total"), 0.0) or 0.0

    fx = _f((calc.get("fx_if_any") or {}).get("per_share_fx") or (calc.get("fx_if_any") or {}).get("rate"), 1.0) or 1.0
    price = _f(calc.get("price"))
    base_rev = _f((calc.get("base_year") or {}).get("revenue"))

    years_data = []
    prev_rev = base_rev
    for idx, p in enumerate(proj):
        rev = _f(p.get("revenue"))
        ebit = _f(p.get("ebit"))
        nopat = _f(p.get("nopat"))
        da = _f(p.get("da"), 0.0) or 0.0
        capex = _f(p.get("capex"), 0.0) or 0.0
        dnwc = _f(p.get("dnwc"), 0.0) or 0.0
        fcff = _f(p.get("fcff"))
        t = _f(p.get("discount_period"), idx + 0.5)
        if rev is None or rev <= 0:
            continue
        if prev_rev and prev_rev > 0:
            g_y = rev / prev_rev - 1.0
        else:
            g_y = _f(p.get("rev_growth"), 0.05) or 0.05
        ebit_m = (ebit / rev) if (ebit is not None) else (_f(p.get("ebit_margin"), 0.15) or 0.15)
        tax_r = (1.0 - nopat / ebit) if (ebit and abs(ebit) > 1e-6 and nopat is not None) else 0.21
        tax_r = max(0.0, min(0.45, tax_r))
        # Net reinvestment as % of revenue (capex + dnwc - da)
        net_reinv_pct = ((capex + dnwc - da) / rev) if fcff is None else ((nopat - fcff) / rev if nopat is not None else (capex + dnwc - da) / rev)
        years_data.append({
            "rev": rev,
            "g": g_y,
            "margin": ebit_m,
            "tax": tax_r,
            "net_reinv_pct": net_reinv_pct,
            "t": t if t is not None else (idx + 0.5),
        })
        prev_rev = rev

    if not years_data:
        return None

    return {
        "base_rev": base_rev or (years_data[0]["rev"] / max(1.0 + years_data[0]["g"], 0.1)),
        "years": years_data,
        "wacc": wacc,
        "terminal_g": term_g if term_g is not None else 0.025,
        "ronic": ronic if (ronic and ronic > 0) else wacc,
        "bridge_total": bridge_total,
        "shares": shares,
        "fx": fx,
        "price": price,
        "tv_t": _f((calc.get("terminal") or {}).get("discount_period_tv")) or years_data[-1]["t"],
        "currency": (calc.get("fx_if_any") or {}).get("pair", "").split("/")[-1] if "/" in str((calc.get("fx_if_any") or {}).get("pair") or "") else (calc.get("currency") or ""),
    }


def _eval_dcf(
    params: dict[str, Any],
    *,
    wacc: float,
    terminal_g: float,
    growth_shift: float = 0.0,
    margin_shift: float = 0.0,
) -> Optional[float]:
    if wacc <= terminal_g + 0.005:
        return None
    rev = params["base_rev"]
    pv_sum = 0.0
    last_nopat = 0.0
    for idx, y in enumerate(params["years"]):
        if growth_shift == 0.0:
            rev = y["rev"]
        else:
            # Taper growth perturbation linearly over forecast horizon
            w = max(0.25, 1.0 - idx * 0.08)
            g_adj = max(-0.5, y["g"] + growth_shift * w)
            rev = rev * (1.0 + g_adj)
        m_adj = max(-0.3, min(0.85, y["margin"] + margin_shift))
        nopat = rev * m_adj * (1.0 - y["tax"])
        fcff = nopat - rev * y["net_reinv_pct"]
        pv_sum += fcff / ((1.0 + wacc) ** y["t"])
        last_nopat = nopat

    ronic = max(params["ronic"], wacc * 0.85, 0.03)
    nopat_next = last_nopat * (1.0 + terminal_g)
    reinv_rate = min(0.95, max(0.0, terminal_g / ronic))
    fcff_next = nopat_next * (1.0 - reinv_rate)
    tv = fcff_next / (wacc - terminal_g)
    pv_tv = tv / ((1.0 + wacc) ** params["tv_t"])
    ev = pv_sum + pv_tv
    eq = ev + params["bridge_total"]
    fv = (eq / params["shares"]) * params["fx"]
    return fv if math.isfinite(fv) else None


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p
    lo = int(math.floor(k))
    hi = min(len(sorted_vals) - 1, lo + 1)
    frac = k - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def build_sensitivity_and_monte_carlo(
    calc: dict[str, Any],
    *,
    trials: int = 2000,
    seed: int = 42,
) -> Optional[dict[str, Any]]:
    """Build 2D sensitivity heatmaps (WACC × terminal g, WACC × EBIT margin) and a seeded Monte Carlo simulation."""
    params = _extract_model_params(calc)
    if params is None:
        return None

    w0 = params["wacc"]
    g0 = params["terminal_g"]
    price = params["price"]

    wacc_steps = [round(max(0.035, w0 + d), 4) for d in (-0.015, -0.0075, 0.0, 0.0075, 0.015)]
    g_steps = [round(max(0.0, g0 + d), 4) for d in (-0.01, -0.005, 0.0, 0.005, 0.01)]
    m_steps = [-0.03, -0.015, 0.0, 0.015, 0.03]

    def _cell(fv: Optional[float]) -> dict[str, Optional[float]]:
        if fv is None:
            return {"fair_value": None, "upside": None}
        up = ((fv / price) - 1.0) if (price and price > 0) else None
        return {"fair_value": round(fv, 2), "upside": round(up, 4) if up is not None else None}

    wacc_vs_g = {
        "row_label": "wacc",
        "col_label": "terminal_g",
        "rows": wacc_steps,
        "cols": g_steps,
        "base_row": 2,
        "base_col": 2,
        "cells": [[_cell(_eval_dcf(params, wacc=w, terminal_g=g)) for g in g_steps] for w in wacc_steps],
    }
    wacc_vs_margin = {
        "row_label": "wacc",
        "col_label": "margin_delta",
        "rows": wacc_steps,
        "cols": m_steps,
        "base_row": 2,
        "base_col": 2,
        "cells": [[_cell(_eval_dcf(params, wacc=w, terminal_g=g0, margin_shift=dm)) for dm in m_steps] for w in wacc_steps],
    }

    # Seeded Monte Carlo simulation
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(max(200, trials)):
        w_s = max(0.04, min(0.22, rng.gauss(w0, 0.010)))
        g_s = max(0.0, min(w_s - 0.015, rng.gauss(g0, 0.005)))
        dg = max(-0.12, min(0.12, rng.gauss(0.0, 0.030)))
        dm = max(-0.08, min(0.08, rng.gauss(0.0, 0.020)))
        fv = _eval_dcf(params, wacc=w_s, terminal_g=g_s, growth_shift=dg, margin_shift=dm)
        if fv is not None and fv > 0:
            samples.append(fv)

    samples.sort()
    n_ok = len(samples)
    if n_ok == 0:
        mc: dict[str, Any] = {"trials": trials, "valid_trials": 0, "histogram": []}
    else:
        mean = sum(samples) / n_ok
        var = sum((x - mean) ** 2 for x in samples) / n_ok
        p10 = _percentile(samples, 0.10)
        p25 = _percentile(samples, 0.25)
        p50 = _percentile(samples, 0.50)
        p75 = _percentile(samples, 0.75)
        p90 = _percentile(samples, 0.90)
        prob_above = (sum(1 for x in samples if x > price) / n_ok) if (price and price > 0) else None
        prob_mos20 = (sum(1 for x in samples if x >= price * 1.25) / n_ok) if (price and price > 0) else None

        # 12-bin histogram trimmed to [P02, P98] for crisp chart readability
        lo_b = _percentile(samples, 0.02)
        hi_b = _percentile(samples, 0.98)
        if hi_b <= lo_b:
            hi_b = lo_b + 1.0
        n_bins = 12
        step = (hi_b - lo_b) / n_bins
        counts = [0] * n_bins
        for x in samples:
            idx = min(n_bins - 1, max(0, int((x - lo_b) / step)))
            counts[idx] += 1
        histogram = [
            {"lo": round(lo_b + i * step, 2), "hi": round(lo_b + (i + 1) * step, 2), "count": counts[i]}
            for i in range(n_bins)
        ]
        mc = {
            "trials": trials,
            "valid_trials": n_ok,
            "mean": round(mean, 2),
            "std": round(math.sqrt(var), 2),
            "p10": round(p10, 2),
            "p25": round(p25, 2),
            "p50": round(p50, 2),
            "p75": round(p75, 2),
            "p90": round(p90, 2),
            "prob_above_price": round(prob_above, 4) if prob_above is not None else None,
            "prob_margin_of_safety_20": round(prob_mos20, 4) if prob_mos20 is not None else None,
            "histogram": histogram,
        }

    return {
        "currency": params["currency"],
        "price": round(price, 2) if price is not None else None,
        "base_wacc": round(w0, 4),
        "base_terminal_g": round(g0, 4),
        "wacc_vs_g": wacc_vs_g,
        "wacc_vs_margin": wacc_vs_margin,
        "monte_carlo": mc,
    }

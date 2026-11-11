"""Rules-based assumption fallback (no LLM). Follows the handbook."""
from __future__ import annotations

import datetime as dt
from typing import Optional


def fade_to_g(start_growths: list[float], terminal_g: float, n: int) -> list[float]:
    """Use provided growths for the first len(start) years, then linear fade to terminal_g."""
    out = list(start_growths[:n])
    if len(out) >= n:
        return out[:n]
    if not out:
        # no consensus: caller should pass last reported growth as start
        out = [terminal_g]
    last = out[-1]
    remain = n - len(out)
    for i in range(1, remain + 1):
        out.append(last + (terminal_g - last) * i / remain)
    return out[:n]


def fade_levels(start: float, end: float, n: int) -> list[float]:
    if n <= 1:
        return [start]
    return [start + (end - start) * i / (n - 1) for i in range(n)]


def capex_floor(ppe_dep_pcts: list[float], capex_pcts: list[float]) -> dict:
    avg_ppe = sum(ppe_dep_pcts) / len(ppe_dep_pcts) if ppe_dep_pcts else None
    avg_cpx = sum(capex_pcts) / len(capex_pcts) if capex_pcts else None
    vals = [v for v in (avg_ppe, avg_cpx) if v is not None]
    floor = max(vals) if vals else None
    return {
        "avg_ppe_dep_pct": avg_ppe,
        "avg_capex_pct": avg_cpx,
        "floor_pct": floor,
        "n_ppe_years": len(ppe_dep_pcts),
        "n_capex_years": len(capex_pcts),
        "note": "9%靜態地板 = max(近 3–5 年（若不足則註明）平均 PPE 折舊%, 平均現金 Capex%)；IFRS 16 只用 PPE 折舊",
    }


def shares_floor(current_m: float, history_m: list[float], years: int = 10, dilution: float = 0.01) -> dict:
    """Conservative (higher) share count. Do not extrapolate buyback-driven declines."""
    hist_max = max(history_m) if history_m else current_m
    grown = current_m * ((1 + dilution) ** years)
    floor = max(current_m, hist_max, grown)
    return {
        "base_shares_m": current_m,
        "history_max_m": hist_max,
        "grown_m": grown,
        "floor_shares_m": floor,
        "method": f"取 max(當前 {current_m:.3f}m, 歷史可見最高 {hist_max:.3f}m, 當前×(1+{dilution:.0%})^{years}={grown:.3f}m)；不把回購導致的股數下降外推為地板",
    }


def scenario_growths(base: list[float], delta: float, first_n: int = 4, skip_stub: bool = False) -> list[float]:
    out = []
    applied = 0
    for i, g in enumerate(base):
        is_stub_year = skip_stub and i == 0
        if (not is_stub_year) and applied < first_n:
            out.append(g + delta)
            applied += 1
        else:
            out.append(g)
    return out


def scenario_margins(base: list[float], delta: float, first_n: int = 4, skip_stub: bool = False) -> list[float]:
    out = []
    applied = 0
    for i, m in enumerate(base):
        is_stub_year = skip_stub and i == 0
        if (not is_stub_year) and applied < first_n:
            out.append(max(m + delta, 0.0))
            applied += 1
        else:
            out.append(m)
    return out


def midyear_t(val: dt.date, cash_mid: dt.date) -> float:
    days = (cash_mid - val).days
    t = days / 365.25
    return max(t, 0.0)


def add_months(d: dt.date, months: int) -> dt.date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    # clamp day
    for day in (d.day, 30, 29, 28):
        try:
            return dt.date(y, m, day)
        except ValueError:
            continue
    return dt.date(y, m, 28)

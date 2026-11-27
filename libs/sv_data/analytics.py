"""Financial statement analytics: DuPont decomposition, earnings quality (accruals & cash conversion), and peer comparison (roadmap §6)."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Mapping, Optional, Sequence

from .standard import Standardized


def _num(std: Standardized, code: str, period: str) -> Optional[float]:
    v = std.get(code, period)
    return float(v.value) if v is not None else None


def _avg_bs(std: Standardized, code: str, period: str, prev_period: Optional[str]) -> tuple[Optional[float], str]:
    cur = _num(std, code, period)
    if cur is None:
        return None, "missing"
    if prev_period:
        prv = _num(std, code, prev_period)
        if prv is not None:
            return (cur + prv) / 2.0, "average"
    return cur, "ending"


def _div(num: Optional[float], den: Optional[float]) -> Optional[float]:
    if num is None or den is None or abs(den) < 1e-12:
        return None
    return num / den


def _r4(v: Optional[float]) -> Optional[float]:
    return round(v, 4) if v is not None else None


def dupont_analysis(std: Standardized, years: int = 10) -> list[dict[str, Any]]:
    """Compute 3-factor and 5-factor DuPont ROE decomposition per fiscal year (newest first).

    3-factor: ROE = Net Margin × Asset Turnover × Equity Multiplier
    5-factor: ROE = Tax Burden × Interest Burden × EBIT Margin × Asset Turnover × Equity Multiplier
    """
    fys = list(std.fiscal_years)
    rows: list[dict[str, Any]] = []
    for idx in range(len(fys) - 1, -1, -1):
        if len(rows) >= years:
            break
        y = fys[idx]
        prev = fys[idx - 1].label if idx > 0 else None
        rev = _num(std, "revenue", y.label)
        ebit = _num(std, "operating_income", y.label)
        ebt = _num(std, "pretax_income", y.label)
        ni = _num(std, "net_income_parent", y.label)
        if ni is None:
            ni = _num(std, "net_income", y.label)
        assets, a_basis = _avg_bs(std, "total_assets", y.label, prev)
        equity, e_basis = _avg_bs(std, "equity_parent", y.label, prev)
        if equity is None:
            equity, e_basis = _avg_bs(std, "total_equity", y.label, prev)

        net_margin = _div(ni, rev)
        asset_turnover = _div(rev, assets)
        equity_multiplier = _div(assets, equity) if (equity is not None and equity > 0) else None
        roe = _div(ni, equity) if (equity is not None and equity > 0) else None

        tax_burden = _div(ni, ebt)
        interest_burden = _div(ebt, ebit)
        ebit_margin = _div(ebit, rev)

        rows.append({
            "period": y.label,
            "period_end": y.end.isoformat(),
            "avg_basis": "average" if (a_basis == "average" and e_basis == "average") else "ending",
            "roe": _r4(roe),
            "net_margin": _r4(net_margin),
            "asset_turnover": _r4(asset_turnover),
            "equity_multiplier": _r4(equity_multiplier),
            "tax_burden": _r4(tax_burden),
            "interest_burden": _r4(interest_burden),
            "ebit_margin": _r4(ebit_margin),
        })
    return rows


def earnings_quality(std: Standardized, years: int = 10) -> list[dict[str, Any]]:
    """Compute earnings quality indicators per fiscal year (newest first):
    - CFO / Net Income (cash backing of earnings)
    - FCF / Net Income (free cash flow conversion)
    - Sloan Accrual Ratio = (Net Income − CFO) / Avg Total Assets (lower is better; > 10% flags aggressive accruals)
    """
    fys = list(std.fiscal_years)
    rows: list[dict[str, Any]] = []
    for idx in range(len(fys) - 1, -1, -1):
        if len(rows) >= years:
            break
        y = fys[idx]
        prev = fys[idx - 1].label if idx > 0 else None
        ni = _num(std, "net_income", y.label)
        if ni is None:
            ni = _num(std, "net_income_parent", y.label)
        cfo = _num(std, "cfo", y.label)
        fcf = _num(std, "free_cash_flow", y.label)
        assets, _ = _avg_bs(std, "total_assets", y.label, prev)

        cfo_ni = _div(cfo, ni) if (ni is not None and ni > 0) else None
        fcf_ni = _div(fcf, ni) if (ni is not None and ni > 0) else None
        sloan = _div((ni - cfo) if (ni is not None and cfo is not None) else None, assets)

        if ni is None or cfo is None:
            flag = "insufficient_data"
        elif ni <= 0:
            flag = "negative_earnings"
        elif (cfo_ni is not None and cfo_ni >= 1.0) and (sloan is None or sloan <= 0.05):
            flag = "strong"
        elif (cfo_ni is not None and cfo_ni >= 0.7) and (sloan is None or sloan <= 0.10):
            flag = "moderate"
        else:
            flag = "weak"

        rows.append({
            "period": y.label,
            "period_end": y.end.isoformat(),
            "cfo_to_net_income": _r4(cfo_ni),
            "fcf_to_net_income": _r4(fcf_ni),
            "sloan_accrual_ratio": _r4(sloan),
            "quality_flag": flag,
        })
    return rows


@dataclass(frozen=True)
class PeerEntry:
    ticker: str
    name: Optional[str]
    currency: Optional[str]
    template: str
    period: Optional[str]
    grade: Optional[str]
    metrics: Mapping[str, Optional[float]]


PEER_METRIC_KEYS = (
    "gross_margin",
    "operating_margin",
    "net_margin",
    "roe",
    "roic",
    "debt_to_equity",
    "cfo_to_net_income",
    "sloan_accrual_ratio",
    "revenue_growth_yoy",
)


def peer_comparison(entries: Sequence[PeerEntry]) -> dict[str, Any]:
    """Build side-by-side peer financial comparison table + peer medians across ratio metrics."""
    medians: dict[str, Optional[float]] = {}
    for k in PEER_METRIC_KEYS:
        vals = [float(e.metrics[k]) for e in entries if e.metrics.get(k) is not None]
        medians[k] = round(median(vals), 4) if vals else None

    return {
        "metrics": list(PEER_METRIC_KEYS),
        "companies": [
            {
                "ticker": e.ticker,
                "name": e.name,
                "currency": e.currency,
                "template": e.template,
                "period": e.period,
                "grade": e.grade,
                "values": {k: (_r4(e.metrics.get(k))) for k in PEER_METRIC_KEYS},
            }
            for e in entries
        ],
        "medians": medians,
    }

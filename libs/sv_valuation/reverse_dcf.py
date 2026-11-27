"""Reverse DCF: solve the 5-year FCF/revenue CAGR implied by the current market price."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ReverseDcfResult:
    implied_growth: float               # annual 5-year FCF growth rate implied by market price
    base_growth: Optional[float]        # model's assumed 5-year growth for comparison
    gap_pp: Optional[float]             # implied_growth - base_growth (percentage points as decimal)
    assessment: str                     # market_more_optimistic | aligned | market_more_conservative
    price: float
    wacc: float
    terminal_g: float
    years: int = 5

    def to_dict(self) -> dict:
        return {
            "implied_growth": round(self.implied_growth, 4),
            "base_growth": round(self.base_growth, 4) if self.base_growth is not None else None,
            "gap_pp": round(self.gap_pp, 4) if self.gap_pp is not None else None,
            "assessment": self.assessment,
            "price": round(self.price, 4),
            "wacc": round(self.wacc, 4),
            "terminal_g": round(self.terminal_g, 4),
            "years": self.years,
        }


def dcf_per_share(
    *,
    fcf_0: float,
    growth: float,
    wacc: float,
    terminal_g: float,
    net_cash: float,
    shares: float,
    years: int = 5,
) -> float:
    """Standard 2-stage FCFF per-share value for a constant explicit growth rate."""
    pv = 0.0
    fcf = fcf_0
    for t in range(1, years + 1):
        fcf *= (1.0 + growth)
        pv += fcf / ((1.0 + wacc) ** t)
    tv = fcf * (1.0 + terminal_g) / (wacc - terminal_g)
    ev = pv + tv / ((1.0 + wacc) ** years)
    return (ev + net_cash) / shares


def solve_implied_growth(
    *,
    price: float,
    fcf_0: float,
    wacc: float,
    terminal_g: float,
    net_cash: float,
    shares: float,
    base_growth: Optional[float] = None,
    years: int = 5,
    lo: float = -0.35,
    hi: float = 0.80,
) -> Optional[ReverseDcfResult]:
    """Solve via bisection for the 5-year annual FCF CAGR such that DCF(g) == market price."""
    if price <= 0 or fcf_0 <= 0 or shares <= 0 or wacc <= terminal_g + 0.005:
        return None

    def f(g: float) -> float:
        return dcf_per_share(
            fcf_0=fcf_0, growth=g, wacc=wacc, terminal_g=terminal_g,
            net_cash=net_cash, shares=shares, years=years,
        ) - price

    f_lo, f_hi = f(lo), f(hi)
    if f_lo > 0 and f_hi > 0:
        g_star = lo
    elif f_lo < 0 and f_hi < 0:
        g_star = hi
    else:
        a, b = lo, hi
        for _ in range(60):
            mid = 0.5 * (a + b)
            val = f(mid)
            if abs(val) < 1e-6:
                a = b = mid
                break
            if (f_lo <= 0 and val < 0) or (f_lo >= 0 and val > 0):
                a, f_lo = mid, val
            else:
                b = mid
        g_star = 0.5 * (a + b)

    gap = (g_star - base_growth) if base_growth is not None else None
    if gap is None:
        assessment = "aligned"
    elif gap > 0.025:
        assessment = "market_more_optimistic"
    elif gap < -0.025:
        assessment = "market_more_conservative"
    else:
        assessment = "aligned"

    return ReverseDcfResult(
        implied_growth=g_star,
        base_growth=base_growth,
        gap_pp=gap,
        assessment=assessment,
        price=price,
        wacc=wacc,
        terminal_g=terminal_g,
        years=years,
    )

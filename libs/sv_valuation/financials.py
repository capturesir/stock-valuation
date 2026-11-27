"""Financial-sector equity valuation models (D5): Two-Stage DDM, Residual Income (RI), Justified P/B-ROE."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class MethodResultRange:
    method: str                          # ddm | ri | pb_roe | relative | dcf
    bear: float
    base: float
    bull: float
    assumptions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "bear": round(self.bear, 4),
            "base": round(self.base, 4),
            "bull": round(self.bull, 4),
            "assumptions": self.assumptions,
        }


def _ddm_value(dps_0: float, ke: float, g_short: float, g_term: float, years: int = 5) -> Optional[float]:
    if dps_0 <= 0 or ke <= g_term or ke <= 0.01:
        return None
    pv = 0.0
    d = dps_0
    for t in range(1, years + 1):
        # Smooth linear fade from g_short in year 1 toward g_term in year `years`
        frac = (t - 1) / max(1, years - 1) if years > 1 else 0.0
        g_t = g_short * (1.0 - frac) + g_term * frac
        d *= (1.0 + g_t)
        pv += d / ((1.0 + ke) ** t)
    d_term = d * (1.0 + g_term)
    tv = d_term / (ke - g_term)
    return pv + tv / ((1.0 + ke) ** years)


def ddm_two_stage(
    *,
    dps_0: float,
    ke: float,
    g_short: float = 0.05,
    g_term: float = 0.025,
    years: int = 5,
) -> Optional[MethodResultRange]:
    """Two-stage Dividend Discount Model with linear growth fade to perpetuity."""
    if dps_0 <= 0 or ke <= g_term + 0.005:
        return None
    g_s = max(-0.05, min(0.20, g_short))
    g_t = max(0.0, min(ke - 0.01, g_term))

    base = _ddm_value(dps_0, ke, g_s, g_t, years)
    bear = _ddm_value(dps_0, ke + 0.01, max(-0.05, g_s - 0.02), max(0.0, g_t - 0.005), years)
    bull = _ddm_value(dps_0, max(g_t + 0.015, ke - 0.01), min(0.22, g_s + 0.02), min(ke - 0.01, g_t + 0.005), years)
    if base is None:
        return None
    lo = min(v for v in (bear, base, bull) if v is not None)
    hi = max(v for v in (bear, base, bull) if v is not None)
    return MethodResultRange(
        method="ddm",
        bear=lo,
        base=base,
        bull=hi,
        assumptions={"dps_0": round(dps_0, 4), "ke": round(ke, 4), "g_short": round(g_s, 4), "g_term": round(g_t, 4), "years": years},
    )


def _ri_value(
    bvps_0: float,
    roe: float,
    ke: float,
    payout: float,
    persistence: float = 0.70,
    years: int = 5,
) -> Optional[float]:
    """Clean-surplus Residual Income model with Ohlson persistence factor ω ∈ [0, 1]."""
    if bvps_0 <= 0 or ke <= 0.01:
        return None
    p = max(0.0, min(0.95, payout))
    w = max(0.0, min(0.95, persistence))
    b = bvps_0
    pv_ri = 0.0
    last_ri = 0.0
    for t in range(1, years + 1):
        ri_t = (roe - ke) * b
        pv_ri += ri_t / ((1.0 + ke) ** t)
        earnings_t = roe * b
        b = max(0.0, b + earnings_t * (1.0 - p))
        last_ri = ri_t
    # Terminal value of persisting residual income: RI_T * ω / (1 + Ke - ω)
    denom = 1.0 + ke - w
    tv = (last_ri * w / denom) if denom > 0.01 else 0.0
    val = bvps_0 + pv_ri + tv / ((1.0 + ke) ** years)
    return max(0.0, val)


def residual_income(
    *,
    bvps_0: float,
    roe: float,
    ke: float,
    payout: float = 0.35,
    persistence: float = 0.70,
    years: int = 5,
) -> Optional[MethodResultRange]:
    """Residual Income (RI) valuation for banks, insurers, and capital-intensive financials."""
    if bvps_0 <= 0 or ke <= 0.01:
        return None
    roe_c = max(-0.20, min(0.40, roe))
    base = _ri_value(bvps_0, roe_c, ke, payout, persistence, years)
    bear = _ri_value(bvps_0, roe_c - 0.015, ke + 0.01, payout, max(0.3, persistence - 0.15), years)
    bull = _ri_value(bvps_0, roe_c + 0.015, max(0.04, ke - 0.01), payout, min(0.9, persistence + 0.10), years)
    if base is None or base <= 0:
        return None
    lo = min(v for v in (bear, base, bull) if v is not None and v > 0)
    hi = max(v for v in (bear, base, bull) if v is not None and v > 0)
    return MethodResultRange(
        method="ri",
        bear=lo,
        base=base,
        bull=hi,
        assumptions={
            "bvps_0": round(bvps_0, 4), "roe": round(roe_c, 4), "ke": round(ke, 4),
            "payout": round(payout, 4), "persistence": round(persistence, 4), "years": years,
        },
    )


def pb_roe_justified(
    *,
    bvps_0: float,
    roe: float,
    ke: float,
    g_term: float = 0.025,
) -> Optional[MethodResultRange]:
    """Justified P/B = (ROE − g) / (Ke − g); Fair Value = BVPS × Justified P/B."""
    if bvps_0 <= 0 or ke <= g_term + 0.005:
        return None
    roe_c = max(0.0, min(0.40, roe))
    g_t = max(0.0, min(ke - 0.01, g_term))

    def _calc(r: float, k: float, g: float) -> Optional[float]:
        if k <= g + 0.005:
            return None
        pb = (r - g) / (k - g)
        return bvps_0 * pb if pb > 0 else None

    base = _calc(roe_c, ke, g_t)
    bear = _calc(max(0.0, roe_c - 0.015), ke + 0.0075, max(0.0, g_t - 0.005))
    bull = _calc(roe_c + 0.015, max(g_t + 0.015, ke - 0.0075), min(ke - 0.01, g_t + 0.005))
    if base is None:
        return None
    vals = [v for v in (bear, base, bull) if v is not None]
    return MethodResultRange(
        method="pb_roe",
        bear=min(vals),
        base=base,
        bull=max(vals),
        assumptions={
            "bvps_0": round(bvps_0, 4), "roe": round(roe_c, 4), "ke": round(ke, 4),
            "g_term": round(g_t, 4), "justified_pb": round((roe_c - g_t) / (ke - g_t), 4),
        },
    )

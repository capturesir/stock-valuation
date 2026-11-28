"""Relative valuation (P/E, P/B, EV/EBITDA, EV/Sales) against peer or historical multiple ranges."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .financials import MethodResultRange


@dataclass(frozen=True)
class MultipleInput:
    metric: str                 # pe | pb | ev_ebitda | ev_sales
    company_value: float        # per-share (eps / bvps) for pe/pb, or total (ebitda / revenue) for EV multiples
    low_multiple: float
    mid_multiple: float
    high_multiple: float
    net_debt: float = 0.0       # used for EV multiples: Equity = EV - net_debt
    shares: float = 1.0         # used for EV multiples: per_share = Equity / shares


def _implied(m: MultipleInput, mult: float) -> Optional[float]:
    if m.company_value <= 0 or mult <= 0:
        return None
    if m.metric in ("pe", "pb", "peer_pe", "peer_pb"):
        return m.company_value * mult
    if m.shares <= 0:
        return None
    eq = m.company_value * mult - m.net_debt
    return eq / m.shares if eq > 0 else None


def relative_valuation(multiples: list[MultipleInput]) -> Optional[MethodResultRange]:
    """Combine valid relative valuation multiples into a bear/base/bull per-share range."""
    breakdown: dict[str, dict] = {}
    lows, mids, highs = [], [], []
    for m in multiples:
        lo = _implied(m, m.low_multiple)
        mid = _implied(m, m.mid_multiple)
        hi = _implied(m, m.high_multiple)
        if mid is None or mid <= 0:
            continue
        vals = [v for v in (lo, mid, hi) if v is not None and v > 0]
        b_lo, b_hi = min(vals), max(vals)
        lows.append(b_lo)
        mids.append(mid)
        highs.append(b_hi)
        breakdown[m.metric] = {
            "bear": round(b_lo, 4),
            "base": round(mid, 4),
            "bull": round(b_hi, 4),
            "multiples": [round(m.low_multiple, 2), round(m.mid_multiple, 2), round(m.high_multiple, 2)],
        }
    if not mids:
        return None
    return MethodResultRange(
        method="relative",
        bear=sum(lows) / len(lows),
        base=sum(mids) / len(mids),
        bull=sum(highs) / len(highs),
        assumptions={"breakdown": breakdown},
    )

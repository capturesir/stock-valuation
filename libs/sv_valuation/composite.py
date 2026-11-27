"""Industry-aware composite valuation, financial-sector warning (D5), and Football Field chart data."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .financials import MethodResultRange


@dataclass(frozen=True)
class MethodBand:
    method: str
    low: float
    mid: float
    high: float
    weight: float
    primary: bool
    warning_code: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "low": round(self.low, 4),
            "mid": round(self.mid, 4),
            "high": round(self.high, 4),
            "weight": round(self.weight, 4),
            "primary": self.primary,
            "warning_code": self.warning_code,
        }


def default_weights(template: str, available_methods: set[str]) -> dict[str, float]:
    """Default weights by industry template (D5):
    - ``bank`` / ``insurance``: RI + DDM + P/B-ROE are primary; FCFF DCF is retained with weight 0 + warning.
    - ``general``: DCF primary (65%) + Relative (35%), re-normalized to available methods.
    """
    if template in ("bank", "insurance"):
        base_map = {"ri": 0.45, "ddm": 0.35, "pb_roe": 0.20}
    else:
        base_map = {"dcf": 0.65, "relative": 0.35}

    active = {m: w for m, w in base_map.items() if m in available_methods and w > 0}
    if not active and "dcf" in available_methods:
        return {"dcf": 1.0}
    total = sum(active.values())
    if total <= 0:
        return {}
    return {m: round(w / total, 4) for m, w in active.items()}


def build_composite(
    *,
    template: str,
    methods: list[MethodResultRange],
    price: Optional[float] = None,
    custom_weights: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    """Compute composite fair value, method weights, D5 financial warnings, and Football Field bars."""
    is_financial = template in ("bank", "insurance")
    available = {m.method for m in methods if m.base > 0}
    if custom_weights:
        cleaned = {k: max(0.0, float(v)) for k, v in custom_weights.items() if k in available}
        s = sum(cleaned.values())
        weights = {k: round(v / s, 4) for k, v in cleaned.items() if v > 0} if s > 0 else default_weights(template, available)
    else:
        weights = default_weights(template, available)

    composite = 0.0
    for m in methods:
        w = weights.get(m.method, 0.0)
        if w > 0:
            composite += m.base * w
    composite_val = round(composite, 4) if weights else None

    bars: list[MethodBand] = []
    mids_primary: list[float] = []
    for m in methods:
        w = weights.get(m.method, 0.0)
        warn = "financial_fcff_warning" if (is_financial and m.method == "dcf") else None
        bars.append(MethodBand(
            method=m.method, low=m.bear, mid=m.base, high=m.bull,
            weight=w, primary=w > 0, warning_code=warn,
        ))
        if w > 0 and m.base > 0:
            mids_primary.append(m.base)

    divergence_warning = False
    if len(mids_primary) >= 2:
        spread = (max(mids_primary) - min(mids_primary)) / max(min(mids_primary), 1e-9)
        divergence_warning = spread > 0.35

    warnings: list[str] = []
    if is_financial:
        warnings.append("financial_fcff_warning")
    if divergence_warning:
        warnings.append("methods_divergence_warning")

    return {
        "template": template,
        "is_financial": is_financial,
        "composite_value": composite_val,
        "weights": weights,
        "price": round(price, 4) if price is not None else None,
        "warnings": warnings,
        "football_field": [b.to_dict() for b in bars],
    }

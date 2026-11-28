"""Point-in-time valuation backtest scorecard (Rank IC, hit rate, undervalued vs overvalued spread)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional, Sequence


@dataclass(frozen=True)
class BacktestPoint:
    ticker: str
    as_of_date: str
    fair_value: float
    price: float
    fwd_price: float
    fwd_date: str
    source: str = "original_statements"  # original_statements | archived_run

    @property
    def upside(self) -> float:
        return (self.fair_value / self.price) - 1.0

    @property
    def fwd_return(self) -> float:
        return (self.fwd_price / self.price) - 1.0


def _ranks(vals: Sequence[float]) -> list[float]:
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    out = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg_r = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = avg_r
        i = j + 1
    return out


def spearman_ic(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < 3 or len(ys) != n:
        return None
    rx = _ranks(xs)
    ry = _ranks(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    denom = math.sqrt(vx * vy)
    return round(cov / denom, 4) if denom > 1e-12 else None


def compute_backtest_scorecard(
    points: Sequence[BacktestPoint],
    *,
    horizon_days: int = 365,
    undervalued_threshold: float = 0.10,
    overvalued_threshold: float = -0.10,
) -> dict[str, Any]:
    """Aggregate point-in-time valuation observations into a calibration scorecard."""
    valid = [p for p in points if p.price > 0 and p.fair_value > 0 and p.fwd_price > 0]
    if not valid:
        return {
            "horizon_days": horizon_days,
            "sample_count": 0,
            "tickers_tested": 0,
            "rank_ic": None,
            "hit_rate": None,
            "undervalued_avg_return": None,
            "fair_avg_return": None,
            "overvalued_avg_return": None,
            "spread_under_minus_over": None,
            "buckets": {"undervalued": 0, "fair": 0, "overvalued": 0},
            "observations": [],
        }

    upsides = [p.upside for p in valid]
    rets = [p.fwd_return for p in valid]
    ic = spearman_ic(upsides, rets)

    under, fair, over = [], [], []
    obs_rows: list[dict[str, Any]] = []
    hits = 0
    directional = 0
    avg_ret = sum(rets) / len(rets)

    for p in valid:
        up = p.upside
        ret = p.fwd_return
        if up >= undervalued_threshold:
            bucket = "undervalued"
            under.append(ret)
            directional += 1
            if ret >= avg_ret:
                hits += 1
        elif up <= overvalued_threshold:
            bucket = "overvalued"
            over.append(ret)
            directional += 1
            if ret <= avg_ret:
                hits += 1
        else:
            bucket = "fair"
            fair.append(ret)

        obs_rows.append({
            "ticker": p.ticker,
            "as_of_date": p.as_of_date,
            "fair_value": round(p.fair_value, 2),
            "price": round(p.price, 2),
            "upside": round(up, 4),
            "fwd_date": p.fwd_date,
            "fwd_price": round(p.fwd_price, 2),
            "fwd_return": round(ret, 4),
            "bucket": bucket,
            "source": p.source,
        })

    under_avg = (sum(under) / len(under)) if under else None
    fair_avg = (sum(fair) / len(fair)) if fair else None
    over_avg = (sum(over) / len(over)) if over else None
    spread = (under_avg - over_avg) if (under_avg is not None and over_avg is not None) else None
    hit_rate = (hits / directional) if directional > 0 else None

    obs_rows.sort(key=lambda r: (r["as_of_date"], r["ticker"]), reverse=True)
    return {
        "horizon_days": horizon_days,
        "sample_count": len(valid),
        "tickers_tested": len({p.ticker for p in valid}),
        "rank_ic": ic,
        "hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
        "undervalued_avg_return": round(under_avg, 4) if under_avg is not None else None,
        "fair_avg_return": round(fair_avg, 4) if fair_avg is not None else None,
        "overvalued_avg_return": round(over_avg, 4) if over_avg is not None else None,
        "spread_under_minus_over": round(spread, 4) if spread is not None else None,
        "buckets": {"undervalued": len(under), "fair": len(fair), "overvalued": len(over)},
        "observations": obs_rows[:60],
    }

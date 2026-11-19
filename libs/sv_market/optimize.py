"""Portfolio optimizer (roadmap §5.3): long-only weights on a 1 % grid within per-asset bounds.

Method — deterministic for a given seed and data:
1. Random sampling: ``samples`` weight vectors drawn uniformly from the bounded simplex (Dirichlet, then
   bounds applied and rounded to whole percent by the largest-remainder rule, so every candidate sums to
   exactly 100 % and respects every bound).
2. Scoring: each candidate's portfolio value series (buy and hold, or rebalanced, see portfolio.py) gives
   annualised return, volatility, Sharpe and maximum drawdown — the same conventions as metrics.py.
   A single objective is used as is; a composite first standardises each metric across the candidates
   (z-score; drawdown counts as better when shallower) and then weights them.
3. Refinement: the best few candidates are polished with SLSQP on continuous weights (same bounds,
   weights sum to 1, the same frozen standardisation), then rounded back to the 1 % grid and re-scored.
4. Output: the best weights, their metrics and score breakdown, the top candidates, an efficient frontier
   (highest return per volatility bucket among the candidates) and, if asked, an out-of-sample check:
   optimise on the first part of the period, then hold those weights through the rest.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from .metrics import PERIODS

OBJECTIVES = ("sharpe", "max_drawdown", "return")
CHUNK = 2000                    # candidates scored per batch (memory: days × CHUNK floats)
REFINE_TOP = 5
FRONTIER_BINS = 40


@dataclass
class Result:
    weights: dict[str, int]                         # whole percent, sums to 100
    metrics: dict[str, float]
    score: float
    breakdown: dict[str, dict]                      # per objective: value, z, weight, contribution
    top: list[dict]                                 # best candidates: weights, metrics, score
    frontier: list[dict]                            # volatility, return, sharpe (efficient set)
    cloud: list[dict]                               # a thinned sample of all candidates, for the chart
    assets: list[dict]                              # each asset alone
    samples: int
    seed: int
    refined: bool
    out_of_sample: Optional[dict] = None
    notes: list[str] = field(default_factory=list)


def _period_index(dates: Sequence[dt.date], how: str) -> np.ndarray:
    if how == "monthly":
        key = [(d.year, d.month) for d in dates]
    elif how == "quarterly":
        key = [(d.year, (d.month - 1) // 3) for d in dates]
    elif how == "yearly":
        key = [d.year for d in dates]
    else:
        return np.zeros(len(dates), dtype=int)
    out, k = np.zeros(len(dates), dtype=int), 0
    for i in range(1, len(dates)):
        if key[i] != key[i - 1]:
            k += 1
        out[i] = k
    return out


def values_matrix(dates: Sequence[dt.date], prices: np.ndarray, W: np.ndarray, rebalance: str) -> np.ndarray:
    """Portfolio values (days × candidates) for weight columns W (assets × candidates), start value 1.

    Same convention as portfolio.values: holdings bought at the first close; with rebalancing they are reset
    to the target weights at the close of the first day of each new period. Between two reset days a and b
    (both inclusive) the value is V(a) × Σ w·P(t)/P(a); the reset itself does not change the value."""
    n = prices.shape[0]
    if rebalance == "none":
        return (prices / prices[0]) @ W
    seg = _period_index(dates, rebalance)
    starts = [0] + [i for i in range(1, n) if seg[i] != seg[i - 1]]
    out = np.empty((n, W.shape[1]))
    level = np.ones(W.shape[1])
    for k, a in enumerate(starts):
        b = starts[k + 1] if k + 1 < len(starts) else n - 1
        v = (prices[a:b + 1] / prices[a]) @ W * level
        out[a:b + 1] = v
        level = v[-1]
    return out


def _metrics(dates: Sequence[dt.date], V: np.ndarray, rf: float, freq: str) -> dict[str, np.ndarray]:
    days = (dates[-1] - dates[0]).days
    ann = (V[-1] / V[0]) ** (365.25 / days) - 1
    pos = _sample_positions(dates, freq)
    S = V[pos]
    R = S[1:] / S[:-1] - 1
    p = PERIODS[freq]
    rf_p = (1 + rf) ** (1 / p) - 1
    ex = R - rf_p
    sd = R.std(axis=0, ddof=1)
    sd_ex = ex.std(axis=0, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        sharpe = np.where(sd_ex > 0, ex.mean(axis=0) * p / (sd_ex * math.sqrt(p)), np.nan)
    peak = np.maximum.accumulate(V, axis=0)
    mdd = (V / peak - 1).min(axis=0)
    return {"return": ann, "volatility": sd * math.sqrt(p), "sharpe": sharpe, "max_drawdown": mdd}


def _sample_positions(dates: Sequence[dt.date], freq: str) -> np.ndarray:
    if freq == "daily":
        return np.arange(len(dates))
    key = (lambda d: d.isocalendar()[:2]) if freq == "weekly" else (lambda d: (d.year, d.month))
    pos = [0]
    for i in range(1, len(dates)):
        if i + 1 == len(dates) or key(dates[i + 1]) != key(dates[i]):
            pos.append(i)
    return np.array(sorted(set(pos)))


def round_to_grid(w: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """Whole percents summing to 100 within [lo, hi] (largest-remainder rounding, then bound repair)."""
    x = np.clip(w * 100, lo, hi)
    base = np.floor(x).astype(int)
    base = np.clip(base, lo, hi)
    short = 100 - base.sum()
    order = np.argsort(-(x - base))
    i = 0
    while short != 0 and i < 10 * len(w):
        j = order[i % len(w)]
        if short > 0 and base[j] < hi[j]:
            base[j] += 1
            short -= 1
        elif short < 0 and base[j] > lo[j]:
            base[j] -= 1
            short += 1
        i += 1
    return base


def sample_weights(n: int, lo: np.ndarray, hi: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """n × assets integer percents, uniform over the bounded simplex, each row sums to 100."""
    k = len(lo)
    free = 100 - lo.sum()
    room = hi - lo
    D = rng.dirichlet(np.ones(k), size=n) * free
    # push anything above its room onto the others, a few passes
    for _ in range(k):
        over = np.maximum(D - room, 0)
        D = np.minimum(D, room)
        spill = over.sum(axis=1, keepdims=True)
        if not spill.any():
            break
        space = room - D
        share = np.where(space.sum(axis=1, keepdims=True) > 0, space / np.maximum(space.sum(axis=1, keepdims=True), 1e-12), 0)
        D = D + share * spill
    return np.array([round_to_grid((lo + d) / 100, lo, hi) for d in D])


def optimise(dates: Sequence[dt.date], tickers: Sequence[str], prices: np.ndarray, *, lo: Sequence[int], hi: Sequence[int],
             objective: dict[str, float], rebalance: str = "none", rf: float = 0.0, freq: str = "daily",
             samples: int = 20000, seed: int = 42, top: int = 10, refine: bool = True) -> Result:
    lo_a, hi_a = np.array(lo, dtype=int), np.array(hi, dtype=int)
    if lo_a.sum() > 100 or hi_a.sum() < 100 or np.any(lo_a > hi_a) or np.any(lo_a < 0) or np.any(hi_a > 100):
        raise ValueError("bounds cannot be met: minimums must sum to at most 100 %, maximums to at least 100 %")
    weights = {k: float(v) for k, v in objective.items() if k in OBJECTIVES and v > 0}
    if not weights:
        raise ValueError(f"choose at least one objective among {OBJECTIVES}")
    total_w = sum(weights.values())
    weights = {k: v / total_w for k, v in weights.items()}
    rng = np.random.default_rng(seed)
    cand = sample_weights(samples, lo_a, hi_a, rng)
    cand = np.unique(cand, axis=0)                 # identical grid points scored once
    mets: dict[str, list] = {k: [] for k in ("return", "volatility", "sharpe", "max_drawdown")}
    for i in range(0, len(cand), CHUNK):
        V = values_matrix(dates, prices, cand[i:i + CHUNK].T / 100.0, rebalance)
        for k, v in _metrics(dates, V, rf, freq).items():
            mets[k].append(v)
    M = {k: np.concatenate(v) for k, v in mets.items()}
    # standardisation frozen on the sampled population (used again for refinement)
    stats = {k: (float(np.nanmean(M[k])), float(np.nanstd(M[k])) or 1.0) for k in OBJECTIVES}

    def score_of(m: dict[str, np.ndarray]) -> np.ndarray:
        if len(weights) == 1:                      # a single objective: its own value (drawdown: shallower is better)
            (k,) = weights
            return np.nan_to_num(m[k], nan=-np.inf)
        s = 0
        for k, w in weights.items():
            mu, sd = stats[k]
            s = s + w * np.nan_to_num((m[k] - mu) / sd, nan=-1e9)
        return s

    sc = score_of(M)
    order = np.argsort(-sc, kind="stable")
    best_w, best_s, refined = cand[order[0]], float(sc[order[0]]), False
    if refine:
        from scipy.optimize import minimize

        def f(x: np.ndarray) -> float:
            V = values_matrix(dates, prices, x.reshape(-1, 1), rebalance)
            return -float(score_of(_metrics(dates, V, rf, freq))[0])

        bounds = list(zip(lo_a / 100.0, hi_a / 100.0))
        cons = ({"type": "eq", "fun": lambda x: x.sum() - 1.0},)
        for j in order[:REFINE_TOP]:
            res = minimize(f, cand[j] / 100.0, method="SLSQP", bounds=bounds, constraints=cons,
                           options={"maxiter": 200, "ftol": 1e-10})
            g = round_to_grid(np.asarray(res.x), lo_a, hi_a)
            V = values_matrix(dates, prices, g.reshape(-1, 1) / 100.0, rebalance)
            s = float(score_of(_metrics(dates, V, rf, freq))[0])
            if s > best_s + 1e-12:
                best_w, best_s, refined = g, s, True
    Vb = values_matrix(dates, prices, best_w.reshape(-1, 1) / 100.0, rebalance)
    mb = {k: float(v[0]) for k, v in _metrics(dates, Vb, rf, freq).items()}
    breakdown = {}
    for k, w in weights.items():
        mu, sd = stats[k]
        z = (mb[k] - mu) / sd
        breakdown[k] = {"value": mb[k], "z": z, "weight": w, "contribution": w * z if len(weights) > 1 else mb[k]}
    def pack(row: np.ndarray, i: Optional[int] = None) -> dict:
        m = mb if i is None else {k: float(M[k][i]) for k in M}
        return {"weights": {t: int(x) for t, x in zip(tickers, row)}, "metrics": m}
    top_rows = [dict(pack(cand[i], i), score=float(sc[i])) for i in order[:top]]
    # efficient frontier: highest return per volatility bucket
    vol, ret = M["volatility"], M["return"]
    edges = np.linspace(np.nanmin(vol), np.nanmax(vol), FRONTIER_BINS + 1)
    frontier, best_so_far = [], -np.inf
    for a, b in zip(edges[:-1], edges[1:]):
        m = (vol >= a) & (vol <= b)
        if m.any():
            i = int(np.nanargmax(np.where(m, ret, -np.inf)))
            if ret[i] > best_so_far:
                best_so_far = ret[i]
                frontier.append({"volatility": float(vol[i]), "return": float(ret[i]), "sharpe": float(M["sharpe"][i])})
    thin = rng.choice(len(cand), size=min(1500, len(cand)), replace=False)
    cloud = [{"volatility": float(vol[i]), "return": float(ret[i])} for i in sorted(thin)]
    assets = []
    for j, t in enumerate(tickers):
        e = np.zeros((len(tickers), 1))
        e[j] = 1.0
        Va = values_matrix(dates, prices, e, "none")
        assets.append({"ticker": t, **{k: float(v[0]) for k, v in _metrics(dates, Va, rf, freq).items()}})
    return Result(weights={t: int(x) for t, x in zip(tickers, best_w)}, metrics=mb, score=best_s, breakdown=breakdown,
                  top=top_rows, frontier=frontier, cloud=cloud, assets=assets, samples=int(len(cand)), seed=seed, refined=refined)


def evaluate(dates: Sequence[dt.date], tickers: Sequence[str], prices: np.ndarray, weights: dict[str, int], rebalance: str,
             rf: float, freq: str) -> dict[str, float]:
    W = np.array([[weights.get(t, 0) / 100.0] for t in tickers])
    V = values_matrix(dates, prices, W, rebalance)
    return {k: float(v[0]) for k, v in _metrics(dates, V, rf, freq).items()}

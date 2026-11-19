"""Optimizer: vectorised values equal portfolio.values, grid and bounds, determinism, objectives, frontier."""
from __future__ import annotations


import numpy as np
import pytest

from libs.sv_market import metrics, portfolio
from libs.sv_market.optimize import evaluate, optimise, round_to_grid, sample_weights, values_matrix
from libs.sv_market.tests.test_metrics import D, walk, weekdays


def market(n=500):
    dates = weekdays(D(2023, 1, 2), n)
    p = np.array([walk(n=n, seed=s, drift=d, vol=v) for s, d, v in ((1, 0.0006, 0.02), (2, 0.0003, 0.01), (3, 0.0001, 0.004))]).T
    return dates, ["HI", "MID", "LO"], p


@pytest.mark.parametrize("how", ["none", "monthly", "quarterly", "yearly"])
def test_vectorised_values_equal_portfolio_values(how):
    dates, tick, p = market()
    rng = np.random.default_rng(0)
    W = rng.dirichlet(np.ones(3), size=25).T
    V = values_matrix(dates, p, W, how)
    for j in range(W.shape[1]):
        ref = portfolio.values(dates, {t: p[:, i] for i, t in enumerate(tick)}, dict(zip(tick, W[:, j])), how)
        assert np.allclose(V[:, j], ref, rtol=1e-12)


def test_metrics_agree_with_the_metrics_module():
    dates, tick, p = market()
    w = {"HI": 50, "MID": 30, "LO": 20}
    m = evaluate(dates, tick, p, w, "none", 0.03, "daily")
    ref = metrics.summary(dates, portfolio.values(dates, {t: p[:, i] for i, t in enumerate(tick)}, w), rf=0.03)
    assert m["sharpe"] == pytest.approx(ref["sharpe"]) and m["max_drawdown"] == pytest.approx(ref["max_drawdown"])
    assert m["return"] == pytest.approx(ref["annualised_return"]) and m["volatility"] == pytest.approx(ref["volatility"])


def test_samples_are_whole_percents_within_bounds():
    lo, hi = np.array([0, 10, 5]), np.array([20, 80, 100])
    W = sample_weights(3000, lo, hi, np.random.default_rng(1))
    assert (W.sum(axis=1) == 100).all() and (W >= lo).all() and (W <= hi).all()
    assert W.dtype.kind == "i" and W[:, 0].max() == 20 and W[:, 1].min() == 10
    assert list(round_to_grid(np.array([0.333, 0.333, 0.334]), np.zeros(3, int), np.full(3, 100))) in ([33, 33, 34], [33, 34, 33], [34, 33, 33])


def test_same_inputs_give_the_same_result_and_bounds_hold():
    dates, tick, p = market()
    kw = dict(lo=[0, 0, 0], hi=[20, 100, 100], objective={"sharpe": 1}, samples=3000, seed=7)
    a = optimise(dates, tick, p, **kw)
    b = optimise(dates, tick, p, **kw)
    assert a.weights == b.weights and a.score == b.score and a.top == b.top
    assert sum(a.weights.values()) == 100 and a.weights["HI"] <= 20
    assert a.metrics["sharpe"] >= max(t["metrics"]["sharpe"] for t in a.top) - 1e-9   # refinement never makes it worse
    c = optimise(dates, tick, p, **{**kw, "seed": 8})
    assert c.samples > 0                                                               # another seed: still valid


def test_objectives_pull_in_the_expected_direction():
    dates, tick, p = market()
    ret = optimise(dates, tick, p, lo=[0, 0, 0], hi=[100, 100, 100], objective={"return": 1}, samples=2000, seed=1)
    dd = optimise(dates, tick, p, lo=[0, 0, 0], hi=[100, 100, 100], objective={"max_drawdown": 1}, samples=2000, seed=1)
    assert ret.weights["HI"] > dd.weights["HI"] and dd.weights["LO"] > ret.weights["LO"]
    mix = optimise(dates, tick, p, lo=[0, 0, 0], hi=[100, 100, 100], objective={"sharpe": 2, "max_drawdown": 1}, samples=2000, seed=1)
    assert set(mix.breakdown) == {"sharpe", "max_drawdown"} and abs(sum(v["weight"] for v in mix.breakdown.values()) - 1) < 1e-12
    assert mix.frontier == sorted(mix.frontier, key=lambda x: x["volatility"])
    assert all(b["return"] > a["return"] for a, b in zip(mix.frontier, mix.frontier[1:]))   # efficient: return rises with risk


def test_impossible_bounds():
    dates, tick, p = market(100)
    with pytest.raises(ValueError):
        optimise(dates, tick, p, lo=[50, 40, 20], hi=[100, 100, 100], objective={"sharpe": 1}, samples=100)
    with pytest.raises(ValueError):
        optimise(dates, tick, p, lo=[0, 0, 0], hi=[30, 30, 30], objective={"sharpe": 1}, samples=100)

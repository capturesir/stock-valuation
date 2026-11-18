"""Metrics against hand-computed values and an independent implementation (statistics module)."""
from __future__ import annotations

import datetime as dt
import math
import random
import statistics as st

import pytest

from libs.sv_market import metrics, portfolio
from libs.sv_market.series import align, convert, rebase, resample, returns

D = dt.date


def weekdays(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def walk(n=800, seed=7, drift=0.0004, vol=0.012, start=100.0):
    rnd = random.Random(seed)
    v, out = start, []
    for _ in range(n):
        out.append(v)
        v *= 1 + drift + rnd.gauss(0, vol)
    return out


def test_hand_computed_small_series():
    dates = [D(2024, 1, 1), D(2024, 7, 1), D(2025, 1, 1)]
    v = [100.0, 80.0, 121.0]
    assert metrics.total_return(v) == pytest.approx(0.21)
    assert metrics.annualised_return(dates, v) == pytest.approx(1.21 ** (365.25 / 366) - 1)
    dd = metrics.drawdowns(dates, v)
    assert dd["max_drawdown"] == pytest.approx(-0.2) and dd["peak"] == D(2024, 1, 1) and dd["trough"] == D(2024, 7, 1)
    assert dd["recovery"] == D(2025, 1, 1) and dd["longest_days"] == 366 and dd["current"] == 0


def test_against_an_independent_implementation():
    dates = weekdays(D(2022, 1, 3), 800)
    a, b = walk(seed=1), walk(seed=2, drift=0.0002, vol=0.01)
    rf = 0.03
    s = metrics.summary(dates, a, rf=rf)
    r = [a[i] / a[i - 1] - 1 for i in range(1, len(a))]
    rb = [b[i] / b[i - 1] - 1 for i in range(1, len(b))]
    rfp = 1.03 ** (1 / 252) - 1
    ex = [x - rfp for x in r]
    assert s["volatility"] == pytest.approx(st.stdev(r) * math.sqrt(252))
    assert s["sharpe"] == pytest.approx(st.mean(ex) * 252 / (st.stdev(ex) * math.sqrt(252)))
    dd = math.sqrt(sum(min(x, 0) ** 2 for x in ex) / len(ex))
    assert s["sortino"] == pytest.approx(st.mean(ex) * 252 / (dd * math.sqrt(252)))
    rel = metrics.relative(dates, a, b, rf=rf)
    beta = st.covariance(r, rb) / st.variance(rb)
    assert rel["beta"] == pytest.approx(beta)
    assert rel["correlation"] == pytest.approx(st.correlation(r, rb))
    assert rel["alpha"] == pytest.approx((st.mean([x - rfp for x in r]) - beta * st.mean([y - rfp for y in rb])) * 252)
    diff = [x - y for x, y in zip(r, rb)]
    assert rel["tracking_error"] == pytest.approx(st.stdev(diff) * math.sqrt(252))
    assert rel["information_ratio"] == pytest.approx(st.mean(diff) * 252 / (st.stdev(diff) * math.sqrt(252)))
    peak, worst = a[0], 0.0
    for v in a:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    assert s["max_drawdown"] == pytest.approx(worst)
    assert s["calmar"] == pytest.approx(s["annualised_return"] / abs(worst))


def test_monthly_capture_and_months_beating():
    dates = weekdays(D(2023, 1, 2), 520)
    a, b = walk(seed=3, n=520), walk(seed=4, n=520)
    m = metrics.monthly(dates, a, b)
    ma, mb = metrics.month_returns(dates, a), dict(metrics.month_returns(dates, b))
    up = [(x, mb[k]) for k, x in ma if mb[k] > 0]
    geo = lambda xs: math.prod(1 + x for x in xs) ** (1 / len(xs)) - 1        # noqa: E731
    assert m["up_capture"] == pytest.approx(geo([x for x, _ in up]) / geo([y for _, y in up]))
    assert m["months_beating"] == pytest.approx(sum(1 for k, x in ma if x > mb[k]) / len(ma))
    assert m["best_month"] == max(x for _, x in ma)


def test_period_returns():
    dates = weekdays(D(2020, 1, 1), 1600)
    v = [100 * 1.0003 ** i for i in range(1600)]
    p = metrics.period_returns(dates, v)
    end = dates[-1]
    i1y = max(i for i, d in enumerate(dates) if d <= D(end.year - 1, end.month, end.day))
    assert p["1y"] == pytest.approx(v[-1] / v[i1y] - 1)
    i3 = max(i for i, d in enumerate(dates) if d <= D(end.year - 3, end.month, end.day))
    assert p["3y"] == pytest.approx((v[-1] / v[i3]) ** (1 / 3) - 1)
    assert p["5y"] is None or dates[0] <= D(end.year - 5, end.month, end.day)


def test_series_helpers():
    a = [(D(2024, 1, 2), 10.0), (D(2024, 1, 3), 11.0), (D(2024, 1, 4), 12.0)]
    b = [(D(2024, 1, 2), 5.0), (D(2024, 1, 4), 6.0)]                         # closed on the 3rd
    dates, vals = align({"a": a, "b": b})
    assert dates == [D(2024, 1, 2), D(2024, 1, 4)] and vals["a"] == [10.0, 12.0]
    fx = {D(2024, 1, 1): 7.8, D(2024, 1, 4): 7.81}
    assert convert(a, fx) == [(D(2024, 1, 2), 78.0), (D(2024, 1, 3), 11.0 * 7.8), (D(2024, 1, 4), 12.0 * 7.81)]
    assert convert(a, {D(2023, 12, 1): 7.8}) == []                          # no rate within a week: dropped, not guessed
    assert rebase([2.0, 3.0]) == [100.0, 150.0] and returns([2.0, 3.0]) == [0.5]
    md, mv = resample(weekdays(D(2024, 1, 1), 45), list(range(45)), "monthly")
    assert md[0] == D(2024, 1, 1) and md[1] == D(2024, 1, 31) and md[-1] == D(2024, 3, 1)


def test_portfolio_buy_and_hold_and_rebalancing():
    dates = [D(2024, 1, 2), D(2024, 1, 31), D(2024, 2, 1), D(2024, 2, 29)]
    prices = {"x": [100.0, 200.0, 200.0, 200.0], "y": [100.0, 100.0, 100.0, 50.0]}
    hold = portfolio.values(dates, prices, {"x": 0.5, "y": 0.5})
    assert hold == pytest.approx([1.0, 1.5, 1.5, 1.25])
    reb = portfolio.values(dates, prices, {"x": 50, "y": 50}, "monthly")       # weights need not sum to 1
    # reset to 50/50 at the close of 1 Feb (value 1.5): then y halves → 0.75 + 0.375
    assert reb == pytest.approx([1.0, 1.5, 1.5, 1.125])
    with pytest.raises(ValueError):
        portfolio.values(dates, prices, {"x": -1, "y": 2})

"""Portfolio optimizer API (alpha.4 step 4)."""
from __future__ import annotations

import datetime as dt

D = dt.date
API = "/api/v1"


def _market(fake):
    fake.add("UP", start=D(2023, 1, 2), drift=0.0008)
    fake.add("FLAT", start=D(2023, 1, 2), drift=0.0001)
    fake.add("MID", start=D(2023, 1, 2), drift=0.0004)


def body(**kw):
    b = {"assets": [{"ticker": "UP", "max": 40}, {"ticker": "FLAT"}, {"ticker": "MID", "min": 10}],
         "objective": {"return": 1}, "range": "max", "samples": 2000, "seed": 3, "currency": "USD"}
    b.update(kw)
    return b


def test_optimize_respects_bounds_and_is_reproducible(client, fake_market):
    _market(fake_market)
    a = client.post(f"{API}/analytics/optimize", json=body())
    assert a.status_code == 200, a.text
    r = a.json()
    w = r["weights"]
    assert sum(w.values()) == 100 and w["UP"] <= 40 and w["MID"] >= 10
    assert w["UP"] == 40                                   # highest return: as much of the riser as allowed
    assert r["frequency"] == "daily" and r["seed"] == 3 and r["data_version"] and r["system_version"]
    assert len(r["frontier"]) >= 2 and len(r["assets"]) == 3 and r["top"][0]["weights"]
    again = client.post(f"{API}/analytics/optimize", json=body()).json()
    assert (again["weights"], again["score"], again["data_version"]) == (w, r["score"], r["data_version"])


def test_out_of_sample_and_composite(client, fake_market):
    _market(fake_market)
    r = client.post(f"{API}/analytics/optimize", json=body(objective={"sharpe": 2, "max_drawdown": 1}, oos_fraction=0.3,
                                                         rebalance="quarterly")).json()
    o = r["out_of_sample"]
    assert o["in_sample"]["end"] == o["out_of_sample"]["start"] and set(o["out_of_sample"]["equal_weight"]) == {"return", "volatility", "sharpe", "max_drawdown"}
    assert set(r["breakdown"]) == {"sharpe", "max_drawdown"} and r["rebalance"] == "quarterly"


def test_bad_optimize_requests(client, fake_market):
    _market(fake_market)
    r = client.post(f"{API}/analytics/optimize", json=body(assets=[{"ticker": "UP", "min": 60}, {"ticker": "FLAT", "min": 50}]))
    assert r.status_code == 400 and r.json()["detail"]["code"] == "optimize_bounds"
    r = client.post(f"{API}/analytics/optimize", json=body(assets=[{"ticker": "UP"}, {"ticker": "up"}]))
    assert r.status_code == 400 and r.json()["detail"]["code"] == "optimize_duplicate"
    r = client.post(f"{API}/analytics/optimize", json=body(assets=[{"ticker": "UP"}]))
    assert r.status_code == 422
    fake_market.add("NEW1", start=D(2026, 10, 1))
    r = client.post(f"{API}/analytics/optimize", json=body(assets=[{"ticker": "UP"}, {"ticker": "NEW1"}]))
    assert r.status_code == 400 and r.json()["detail"]["code"] == "optimize_too_short"

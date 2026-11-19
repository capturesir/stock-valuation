"""Saved optimisation scenarios recompute identically; a data revision is detected (alpha.4 acceptance)."""
from __future__ import annotations

import datetime as dt

D = dt.date
API = "/api/v1"


def _inputs():
    return {"assets": [{"ticker": "UP", "max": 60}, {"ticker": "FLAT"}, {"ticker": "MID"}], "objective": {"sharpe": 2, "max_drawdown": 1},
            "range": "2y", "samples": 2000, "seed": 11, "oos_fraction": 0.3, "rebalance": "monthly"}


def _market(fake):
    fake.add("UP", start=D(2023, 1, 2), drift=0.0008)
    fake.add("FLAT", start=D(2023, 1, 2), drift=0.0001)
    fake.add("MID", start=D(2023, 1, 2), drift=0.0004)


def test_a_saved_scenario_recomputes_identically(client, member, fake_market):
    _market(fake_market)
    r = client.post(f"{API}/scenarios", json={"name": "Balanced", "inputs": _inputs()})
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["data_version"] == s["result"]["data_version"] and s["inputs"]["currency"] == "USD"
    assert s["window_start"] == s["result"]["window"]["start"] and sum(s["weights"].values()) == 100
    # more data arrives later: the scenario still uses its own window
    fake_market.add("UP", start=D(2023, 1, 2), end=D(2026, 11, 13), drift=0.0008)
    rc = client.post(f"{API}/scenarios/{s['id']}/recompute").json()
    assert rc["identical"] and rc["same_data"] and rc["same_system"] and rc["weight_changes"] == {}
    assert rc["recomputed"]["window"]["start"] == s["window_start"] and rc["recomputed"]["window"]["end"] == s["window_end"]
    assert member.get(f"{API}/scenarios").json() == []                         # private (D10)
    r = member.post(f"{API}/scenarios/{s['id']}/recompute")
    assert r.status_code == 404
    listed = client.get(f"{API}/scenarios").json()
    assert [x["name"] for x in listed] == ["Balanced"] and "result" not in listed[0]
    r = client.delete(f"{API}/scenarios/{s['id']}")
    assert r.status_code == 204


def test_a_revised_price_is_detected_on_recompute(client, fake_market):
    from sqlalchemy import select, update

    from backend.app.db import session
    from backend.app.models import PriceBar

    _market(fake_market)
    s = client.post(f"{API}/scenarios", json={"name": "Check", "inputs": _inputs()}).json()
    with session() as db:                                                        # the source later corrects an old close
        inside = dt.date.fromisoformat(s["window_start"]) + dt.timedelta(days=30)        # a day within the scenario's window
        day = db.scalar(select(PriceBar.date).where(PriceBar.ticker == "MID", PriceBar.date >= inside).order_by(PriceBar.date).limit(1))
        db.execute(update(PriceBar).where(PriceBar.ticker == "MID", PriceBar.date == day).values(close=PriceBar.close * 1.2))
    rc = client.post(f"{API}/scenarios/{s['id']}/recompute").json()
    assert rc["same_data"] is False and rc["original"]["data_version"] != rc["recomputed"]["data_version"]

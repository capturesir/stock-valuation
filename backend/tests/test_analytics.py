"""Comparison chart, portfolios and saved comparisons (alpha.4 step 3)."""
from __future__ import annotations

import datetime as dt
import io

import pytest

D = dt.date
API = "/api/v1"


def test_compare_two_us_stocks(client, fake_market):
    fake_market.add("AAA", start=D(2023, 1, 2), drift=0.0006)
    fake_market.add("BBB", start=D(2023, 1, 2), drift=0.0002)
    r = client.post(f"{API}/analytics/compare", json={"lines": [{"ref": "AAA"}, {"ref": "BBB"}], "benchmark": 1, "range": "1y",
                                                       "currency": "USD", "rf": 0.03})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["frequency"] == "daily" and d["benchmark"] == "l1" and d["currency"] == "USD"
    assert all(ln["values"][0] == 100 for ln in d["lines"])
    assert [x["key"] for x in d["relative"]] == ["l0"]                     # the benchmark has no relative line
    aaa, bbb = d["metrics"]
    assert aaa["beta"] is not None and bbb["beta"] is None and aaa["annualised_return"] > bbb["annualised_return"]
    assert d["periods"][0]["1y"] is not None and d["window"]["limited_by"] is None
    # relative strength = (AAA / BBB) rebased to 100
    a, b, rel = d["lines"][0]["values"], d["lines"][1]["values"], d["relative"][0]["values"]
    assert rel[-1] == pytest.approx(a[-1] / b[-1] * 100, rel=1e-6)


def test_different_exchanges_use_weekly_returns_and_one_currency(client, fake_market):
    fake_market.add("USX", start=D(2024, 1, 1))
    fake_market.add("0001.HK", start=D(2024, 1, 1), base=50.0, currency="HKD", tz="Asia/Hong_Kong")
    fake_market.add("HKDUSD=X", start=D(2024, 1, 1), base=0.128, drift=0.0, currency="USD", quote_type="CURRENCY")
    d = client.post(f"{API}/analytics/compare", json={"lines": [{"ref": "USX"}, {"ref": "0001.HK"}], "benchmark": 0,
                                                       "range": "max", "currency": "USD"}).json()
    assert d["frequency"] == "weekly"
    hk = d["lines"][1]
    assert hk["currency"] == "HKD" and hk["values"][0] == 100              # converted, then rebased


def test_shortest_history_limits_the_window(client, fake_market):
    fake_market.add("OLDCO", start=D(2020, 1, 1))
    fake_market.add("NEWCO", start=D(2025, 6, 2))
    d = client.post(f"{API}/analytics/compare", json={"lines": [{"ref": "OLDCO"}, {"ref": "NEWCO"}], "range": "5y"}).json()
    assert d["window"]["limited_by"] == "NEWCO" and d["window"]["start"] == "2025-06-02"


def test_bad_requests(client, fake_market):
    fake_market.add("AAA", start=D(2024, 1, 1))
    r = client.post(f"{API}/analytics/compare", json={"lines": [{"ref": "AAA"}], "benchmark": 3})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "compare_benchmark"
    r = client.post(f"{API}/analytics/compare", json={"lines": [{"ref": f"T{i}"} for i in range(11)]})
    assert r.status_code == 422
    r = client.post(f"{API}/analytics/compare", json={"lines": [{"kind": "portfolio", "ref": "nope"}]})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "portfolio_not_found"


def test_portfolios_are_private_and_weights_normalised(client, member, fake_market):
    fake_market.add("AAA", start=D(2024, 1, 1), drift=0.001)
    fake_market.add("BBB", start=D(2024, 1, 1), drift=-0.0005)
    r = client.post(f"{API}/portfolios", json={"name": "Mix", "holdings": [{"ticker": "aaa", "weight": 60}, {"ticker": "BBB", "weight": 40}],
                                               "rebalance": "monthly"})
    assert r.status_code == 201
    p = r.json()
    assert p["holdings"] == [{"ticker": "AAA", "weight": 0.6}, {"ticker": "BBB", "weight": 0.4}]
    assert member.get(f"{API}/portfolios").json() == []                    # not visible to another user
    r = member.delete(f"{API}/portfolios/{p['id']}")
    assert r.status_code == 404
    r = member.post(f"{API}/analytics/compare", json={"lines": [{"kind": "portfolio", "ref": p["id"]}]})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "portfolio_not_found"
    d = client.post(f"{API}/analytics/compare", json={"lines": [{"kind": "portfolio", "ref": p["id"]}, {"ref": "AAA"}, {"ref": "BBB"}],
                                                       "benchmark": 1, "range": "max"}).json()
    port, a, b = (ln["values"] for ln in d["lines"])
    assert d["lines"][0]["label"] == "Mix" and min(a[-1], b[-1]) < port[-1] < max(a[-1], b[-1])


def test_saved_comparisons(client, member, fake_market):
    settings = {"lines": [{"ref": "SPY"}, {"ref": "AAPL"}], "benchmark": 0, "range": "5y", "rf": 0.04}
    c = client.post(f"{API}/comparisons", json={"name": "Big tech", "settings": settings}).json()
    assert c["settings"]["range"] == "5y" and c["settings"]["lines"][1]["ref"] == "AAPL"
    assert member.get(f"{API}/comparisons").json() == []
    r = client.put(f"{API}/comparisons/{c['id']}", json={"name": "Renamed", "settings": settings})
    assert r.json()["name"] == "Renamed"
    r = member.put(f"{API}/comparisons/{c['id']}", json={"name": "x", "settings": settings})
    assert r.status_code == 404
    r = client.delete(f"{API}/comparisons/{c['id']}")
    assert r.status_code == 204
    assert client.get(f"{API}/comparisons").json() == []


def test_export(client, fake_market):
    from openpyxl import load_workbook

    fake_market.add("AAA", start=D(2025, 1, 2))
    fake_market.add("BBB", start=D(2025, 1, 2), drift=0.0001)
    body = {"lines": [{"ref": "AAA"}, {"ref": "BBB"}], "benchmark": 1, "range": "max"}
    r = client.post(f"{API}/analytics/compare/export", params={"format": "csv"}, json=body, headers={"Accept-Language": "en"})
    assert r.status_code == 200 and r.content.decode("utf-8-sig").splitlines()[0].startswith("Date,AAA Inc. (AAA),BBB Inc. (BBB)")
    r = client.post(f"{API}/analytics/compare/export", params={"format": "xlsx"}, json=body)
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["指標", "走勢"] and wb["指標"]["A2"].value == "估值工作台"

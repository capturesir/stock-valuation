"""Company profile shown before valuing: found, not found (with suggestions), cached. No network."""
from __future__ import annotations

import pytest


@pytest.fixture()
def fake_yahoo(monkeypatch):
    from backend.app.services import profile as prof

    calls = {"quote": 0}
    known = {"0700.HK": ("Tencent Holdings Limited", "HKD"), "BRK-B": ("Berkshire Hathaway Inc.", "USD")}

    def quote(t):
        calls["quote"] += 1
        if t not in known:
            return None
        return {"meta": {"shortName": known[t][0], "currency": known[t][1], "exchangeName": "TEST"},
                "last": {"close": 500.0, "date": "2026-11-17"}}

    monkeypatch.setattr(prof, "fetch_quote", quote)
    monkeypatch.setattr(prof, "fetch_info", lambda t: {"longName": known[t][0], "sector": "Communication Services",
                                                       "industry": "Internet Content & Information", "country": "China",
                                                       "financialCurrency": "CNY", "marketCap": 3.4e12,
                                                       "longBusinessSummary": "x" * 600} if t in known else {})
    monkeypatch.setattr(prof, "search", lambda q, limit=6: [{"ticker": "0700.HK", "name": "TENCENT", "exchange": "Hong Kong",
                                                             "type": "EQUITY"}] if q.lower() == "tencent" else [])
    return calls


def test_profile_of_a_known_ticker(client, fake_yahoo):
    r = client.get("/api/v1/stocks/700/profile")                       # 700 -> 0700.HK
    assert r.status_code == 200
    p = r.json()
    assert p["ticker"] == "0700.HK" and p["name"] == "Tencent Holdings Limited"
    assert p["currency"] == "HKD" and p["financial_currency"] == "CNY" and p["valued"] is False
    assert len(p["summary"]) <= 481
    client.get("/api/v1/stocks/0700.HK/profile")
    assert fake_yahoo["quote"] == 1                                    # cached


def test_unknown_ticker_answers_404_with_suggestions(client, fake_yahoo):
    r = client.get("/api/v1/stocks/TENCENT/profile", headers={"Accept-Language": "en"})
    assert r.status_code == 404
    d = r.json()["detail"]
    assert d["code"] == "ticker_not_found" and "was not found" in d["error"]
    assert d["suggestions"][0]["ticker"] == "0700.HK"


def test_dot_share_class_suggests_the_dash_form(client, fake_yahoo):
    d = client.get("/api/v1/stocks/BRK.B/profile").json()["detail"]
    assert d["suggestions"][0]["ticker"] == "BRK-B"

"""Backend API tests for DuPont analysis, earnings quality, and peer financial comparison (§6)."""
from __future__ import annotations

from backend.app.services import statements as stmt_svc


def test_statement_analytics_and_peer_compare_api(client, monkeypatch):
    def fake_yahoo(ticker: str):
        if ticker == "0700.HK":
            return {
                "ticker": "0700.HK",
                "annual": {
                    "2023-12-31": {
                        "Total Revenue": 609_000, "Cost Of Revenue": 315_000, "Gross Profit": 294_000,
                        "Operating Income": 160_000, "Pretax Income": 161_000, "Net Income": 115_000,
                        "Total Assets": 1_577_000, "Total Liabilities Net Minority Interest": 703_000,
                        "Stockholders Equity": 808_000, "Total Equity Gross Minority Interest": 874_000,
                        "Operating Cash Flow": 222_000, "Capital Expenditure": -24_000,
                    },
                    "2024-12-31": {
                        "Total Revenue": 660_000, "Cost Of Revenue": 311_000, "Gross Profit": 349_000,
                        "Operating Income": 208_000, "Pretax Income": 241_000, "Net Income": 194_000,
                        "Total Assets": 1_781_000, "Total Liabilities Net Minority Interest": 727_000,
                        "Stockholders Equity": 974_000, "Total Equity Gross Minority Interest": 1_054_000,
                        "Operating Cash Flow": 258_000, "Capital Expenditure": -76_000,
                    },
                },
            }
        if ticker == "9988.HK":
            return {
                "ticker": "9988.HK",
                "annual": {
                    "2024-12-31": {
                        "Total Revenue": 941_000, "Cost Of Revenue": 586_000, "Gross Profit": 355_000,
                        "Operating Income": 113_000, "Pretax Income": 100_000, "Net Income": 80_000,
                        "Total Assets": 1_764_000, "Total Liabilities Net Minority Interest": 660_000,
                        "Stockholders Equity": 1_000_000, "Total Equity Gross Minority Interest": 1_104_000,
                        "Operating Cash Flow": 182_000, "Capital Expenditure": -32_000,
                    }
                },
            }
        return None

    monkeypatch.setattr(stmt_svc, "_yahoo_annual", fake_yahoo)
    monkeypatch.setattr(stmt_svc, "_yahoo_meta", lambda t: {"name": f"Co {t}", "currency": "CNY", "sector": "Tech", "industry": "Internet"})

    assert client.post("/api/v1/admin/statements/sync/0700.HK").status_code == 200
    assert client.post("/api/v1/admin/statements/sync/9988.HK").status_code == 200

    # 1. DuPont + Earnings Quality endpoint
    r = client.get("/api/v1/stocks/0700.HK/statements/analytics")
    assert r.status_code == 200, r.text
    an = r.json()
    assert an["ticker"] == "0700.HK"
    assert len(an["dupont"]) == 2
    assert an["dupont"][0]["period"] == "FY2024"
    assert an["dupont"][0]["avg_basis"] == "average"
    assert an["dupont"][0]["roe"] > 0.2
    assert an["earnings_quality"][0]["quality_flag"] == "strong"
    assert an["earnings_quality"][0]["cfo_to_net_income"] > 1.0

    # 2. Peer financial compare endpoint (including an unsynced ticker)
    cmp = client.get("/api/v1/statements/compare", params={"tickers": "0700.HK,9988.HK,0005.HK"})
    assert cmp.status_code == 200, cmp.text
    body = cmp.json()
    assert [c["ticker"] for c in body["companies"]] == ["0700.HK", "9988.HK"]
    assert body["missing"] == ["0005.HK"]
    assert body["medians"]["gross_margin"] is not None
    assert body["medians"]["roe"] is not None

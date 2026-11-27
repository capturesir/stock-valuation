"""Backend tests for beta.2: D4 unified news interface, T1/T2 suggestions, accept/dismiss, and migration 0011."""
from __future__ import annotations

import datetime as dt

from libs.sv_ai import NewsItem

from backend.app.db import session
from backend.app.models import Filer, Filing, SecTicker
from backend.app.services import ai_tasks


def test_unified_news_t2_generation_and_accept_dismiss_workflow(client, monkeypatch):
    cik = "0001045810"
    now = dt.datetime.now(dt.timezone.utc)
    with session() as db:
        db.add(SecTicker(ticker="NVDA", cik=cik, title="NVIDIA CORP", updated_at=now))
        db.add(Filer(cik=cik, name="NVIDIA CORP", sic="3674", tickers=["NVDA"], synced_at=now))
        db.add(Filing(
            accession="0001045810-26-000088", cik=cik, form="8-K",
            filed=dt.date(2026, 11, 20), report_date=dt.date(2026, 11, 19),
            primary_document="nvda-8k.htm", description="Q3 FY2027 Earnings Release",
        ))

    monkeypatch.setattr(
        ai_tasks, "_yahoo_news",
        lambda ticker, limit=10: [
            NewsItem("yahoo:1", ticker, "yahoo", "NVIDIA Blackwell shipments accelerate", "2026-11-22",
                     "https://finance.yahoo.com/news/nvda-blackwell", "Strong hyperscaler demand."),
        ],
    )

    # 1. Unified news feed merges SEC 8-K + Yahoo headline with clickable URLs
    news = client.get("/api/v1/stocks/NVDA/news").json()
    assert len(news) == 2
    assert {n["source"] for n in news} == {"sec", "yahoo"}
    assert all(n["url"].startswith("https://") for n in news)

    # 2. Generate T2 suggestions (all verified with clickable URLs)
    gen = client.post("/api/v1/stocks/NVDA/suggestions/generate", json={"tasks": ["T2"]})
    assert gen.status_code == 200, gen.text
    body = gen.json()
    assert body["created"] == 2 and body["rejected_unverified"] == 0
    assert len(body["suggestions"]) == 2
    assert all(s["status"] == "pending" and s["source_url"].startswith("https://") for s in body["suggestions"])

    # Idempotent re-generation creates 0 duplicates
    gen2 = client.post("/api/v1/stocks/NVDA/suggestions/generate", json={"tasks": ["T2"]})
    assert gen2.status_code == 200 and gen2.json()["created"] == 0

    # 3. Accept first suggestion and dismiss second
    s1, s2 = body["suggestions"][0]["id"], body["suggestions"][1]["id"]
    acc = client.put(f"/api/v1/suggestions/{s1}", json={"status": "accepted"})
    assert acc.status_code == 200 and acc.json()["status"] == "accepted" and acc.json()["decided_by"]
    dis = client.put(f"/api/v1/suggestions/{s2}", json={"status": "dismissed"})
    assert dis.status_code == 200 and dis.json()["status"] == "dismissed"

    # Unknown suggestion returns 404
    assert client.put("/api/v1/suggestions/999999", json={"status": "accepted"}).status_code == 404


def test_t1_apply_to_personal_plan_and_t7_generation(client, monkeypatch):
    from backend.app.models import AiSuggestion, FiledFactRow, RawDocumentRow
    from backend.app.services import valuation_methods as vm_svc
    from decimal import Decimal

    cik = "0001045810"
    now = dt.datetime.now(dt.timezone.utc)
    with session() as db:
        raw = RawDocumentRow(
            source="sec", kind="companyfacts", key=cik, url="https://data.sec.gov/api/xbrl/companyfacts/CIK0001045810.json",
            sha256="a" * 64, bytes=10, content_type="application/json", path="sec/fake.json.gz",
            http_status=200, fetched_at=now, last_checked_at=now,
        )
        db.add(raw)
        db.flush()
        db.add(SecTicker(ticker="NVDA", cik=cik, title="NVIDIA CORP", updated_at=now))
        db.add(Filer(cik=cik, name="NVIDIA CORP", sic="3674", tickers=["NVDA"], synced_at=now))
        db.flush()
        db.add(Filing(accession="0001045810-26-000010", cik=cik, form="10-K", filed=dt.date(2026, 2, 26), report_date=dt.date(2026, 1, 25)))
        db.flush()
        db.add(FiledFactRow(
            cik=cik, taxonomy="us-gaap", concept="Revenues", unit="USD", value=Decimal("130000000000"),
            period_start=dt.date(2025, 1, 27), period_end=dt.date(2026, 1, 25), fy=2026, fp="FY",
            form="10-K", accession="0001045810-26-000010", filed=dt.date(2026, 2, 26), raw_document_id=raw.id, created_at=now,
        ))
        sug = AiSuggestion(
            ticker="NVDA", task_key="T1", title="Restatement: FY2026 revenue",
            detail="Adjust revenue for one-off recognition.",
            payload={"item": "revenue", "period": "FY2026", "value": "128000000000"},
            source_id="restated:revenue:FY2026", source_title="NVDA FY2026 10-K",
            source_url="https://www.sec.gov/Archives/edgar/data/1045810/nvda-10k.htm",
            source_date="2026-02-26", status="pending",
        )
        db.add(sug)
        db.flush()
        sug_id = sug.id

    # 1. Accepting T1 with apply_to_plan=True writes to user's personal adjustment plan with audit trail
    acc = client.put(f"/api/v1/suggestions/{sug_id}", json={"status": "accepted", "apply_to_plan": True})
    assert acc.status_code == 200, acc.text
    assert acc.json()["payload"]["applied_plan_version"] == 1
    plans = client.get("/api/v1/stocks/NVDA/adjustment-plans").json()
    assert plans["personal"]["current_version"] == 1
    adj0 = plans["personal"]["current"]["adjustments"][0]
    assert adj0["item"] == "revenue"
    assert adj0["value"] == "128000000000"
    assert "sec.gov" in adj0["basis"]

    # 2. T7 generates grounded valuation synthesis & reverse DCF explanations
    monkeypatch.setattr(vm_svc, "compute_stock_methods", lambda db, ticker: {
        "ticker": ticker, "currency": "USD", "template": "general", "is_financial": False,
        "composite_value": 170.0, "price": 140.0, "weights": {"dcf": 0.65, "relative": 0.35},
        "warnings": [], "football_field": [], "methods": [],
        "reverse_dcf": {"implied_growth": 0.14, "base_growth": 0.17, "gap_pp": -3.0, "assessment": "market_more_conservative", "price": 140.0, "wacc": 0.09, "terminal_g": 0.03, "years": 5},
    })
    gen = client.post("/api/v1/stocks/NVDA/suggestions/generate", json={"tasks": ["T7"]})
    assert gen.status_code == 200, gen.text
    assert gen.json()["created"] == 2
    assert any(s["task_key"] == "T7" for s in gen.json()["suggestions"])

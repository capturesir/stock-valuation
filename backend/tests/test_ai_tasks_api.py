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

"""Unified news providers (D4) and AI task suggestions (T1/T2, beta.2) with accept/dismiss workflow."""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.sv_ai import NewsItem, generate_t1_suggestions, generate_t2_suggestions, merge_news

from ..models import AiSuggestion, Filing
from . import statements

log = logging.getLogger("sv.ai_tasks")


# ------------------------------------------------------------------ unified news providers (D4)

def _sec_news(db: Session, ticker: str, limit: int = 12) -> list[NewsItem]:
    filer = statements.filer_for(db, ticker)
    if filer is None:
        return []
    filings = db.scalars(
        select(Filing)
        .where(Filing.cik == filer.cik, Filing.form.in_(("8-K", "10-K", "10-Q", "6-K", "20-F")))
        .order_by(Filing.filed.desc())
        .limit(limit)
    ).all()
    out: list[NewsItem] = []
    for f in filings:
        urls = statements.filing_urls(filer.cik, f.accession, f.primary_document)
        url = urls.get("document_url") or urls.get("index_url")
        if not url:
            continue
        desc = f.description or f.items or f.form
        out.append(NewsItem(
            id=f"sec:{f.accession}",
            ticker=ticker,
            source="sec",
            title=f"{f.form} — {desc}"[:200],
            published_at=f.filed.isoformat() if f.filed else None,
            url=url,
            summary=f"SEC {f.form} filed {f.filed.isoformat() if f.filed else ''} (report date {f.report_date.isoformat() if f.report_date else 'N/A'})",
            kind="filing",
        ))
    return out


def _yahoo_news(ticker: str, limit: int = 10) -> list[NewsItem]:
    """Fetch public Yahoo Finance headlines via yfinance (monkeypatched in offline tests)."""
    try:
        import yfinance as yf
        raw = yf.Ticker(ticker).news or []
    except Exception as exc:
        log.debug("yahoo news unavailable for %s: %s", ticker, type(exc).__name__)
        return []
    out: list[NewsItem] = []
    for item in raw[:limit]:
        content = item.get("content") if isinstance(item.get("content"), dict) else item
        title = str(content.get("title") or "").strip()
        canon = content.get("canonicalUrl") if isinstance(content.get("canonicalUrl"), dict) else {}
        url = str(canon.get("url") or content.get("link") or "").strip()
        pub = str(content.get("pubDate") or "")[:10] or None
        if not title or not url.startswith(("http://", "https://")):
            continue
        nid = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        out.append(NewsItem(
            id=f"yahoo:{nid}",
            ticker=ticker,
            source="yahoo",
            title=title[:200],
            published_at=pub,
            url=url[:500],
            summary=str(content.get("summary") or "")[:400],
            kind="news",
        ))
    return out


def _hkex_news(ticker: str, limit: int = 8) -> list[NewsItem]:
    if not ticker.endswith(".HK"):
        return []
    try:
        from src.fetch import hkex
        rows = hkex.fetch_recent_announcements(ticker, limit=limit) if hasattr(hkex, "fetch_recent_announcements") else []
    except Exception:
        return []
    out: list[NewsItem] = []
    for r in rows[:limit]:
        url = str(r.get("url") or "").strip()
        title = str(r.get("title") or "").strip()
        if not url.startswith(("http://", "https://")) or not title:
            continue
        nid = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        out.append(NewsItem(
            id=f"hkex:{nid}",
            ticker=ticker,
            source="hkex",
            title=title[:200],
            published_at=str(r.get("date") or "")[:10] or None,
            url=url[:500],
            summary=str(r.get("summary") or "")[:400],
            kind="announcement",
        ))
    return out


def fetch_unified_news(db: Session, ticker: str, *, limit: int = 25, include_remote: bool = True) -> list[NewsItem]:
    """D4 unified news interface combining free providers (SEC filings + HKEX + Yahoo)."""
    items = _sec_news(db, ticker, limit=limit)
    if include_remote:
        items += _hkex_news(ticker, limit=8)
        items += _yahoo_news(ticker, limit=10)
    return merge_news(items, limit=limit)


# ------------------------------------------------------------------ suggestions (T1 / T2)

def generate_and_store_suggestions(
    db: Session,
    ticker: str,
    *,
    tasks: Optional[list[str]] = None,
    include_remote_news: bool = True,
) -> dict[str, Any]:
    """Run T1 (checks-layer input) and/or T2 (unified news interface), reject 100% of unverified citations,
    and persist new suggestions idempotently."""
    selected = set(tasks or ["T1", "T2"])
    verified = []
    rejected = 0

    if "T1" in selected:
        filer = statements.filer_for(db, ticker)
        if filer is not None and filer.synced_at is not None:
            std = statements.standard_statements(db, filer, view="latest", years=6, ticker=ticker, period="annual")
            v1, r1 = generate_t1_suggestions(ticker, std)
            verified.extend(v1)
            rejected += r1

    if "T2" in selected:
        news = fetch_unified_news(db, ticker, limit=15, include_remote=include_remote_news)
        v2, r2 = generate_t2_suggestions(ticker, news)
        verified.extend(v2)
        rejected += r2

    created = 0
    for s in verified:
        existing = db.scalar(select(AiSuggestion).where(
            AiSuggestion.ticker == ticker,
            AiSuggestion.task_key == s.task_key,
            AiSuggestion.source_id == s.source_id,
        ))
        if existing is not None:
            continue
        db.add(AiSuggestion(
            ticker=ticker,
            task_key=s.task_key,
            title=s.title,
            detail=s.detail,
            payload=s.payload,
            source_id=s.source_id,
            source_title=s.source_title,
            source_url=s.source_url,
            source_date=s.source_date,
            status="pending",
        ))
        created += 1
    db.flush()

    rows = db.scalars(
        select(AiSuggestion).where(AiSuggestion.ticker == ticker).order_by(AiSuggestion.created_at.desc(), AiSuggestion.id.desc())
    ).all()
    return {"ticker": ticker, "created": created, "rejected_unverified": rejected, "rows": rows}


def decide_suggestion(db: Session, suggestion_id: int, status: str, decided_by: str) -> Optional[AiSuggestion]:
    row = db.get(AiSuggestion, suggestion_id)
    if row is None:
        return None
    row.status = status
    row.decided_by = decided_by
    row.decided_at = dt.datetime.now(dt.timezone.utc)
    db.flush()
    return row

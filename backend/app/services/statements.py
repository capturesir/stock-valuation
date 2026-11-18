"""Statement data, layers ① raw and ② filed: fetch from the SEC, keep every document, store every fact.

The parsing lives in ``libs/sv_data`` (no database there); this module stores what it returns.
Every outbound request goes through ``src.fetch.httputil.get``, so SEC calls share the system-wide
pacing (≤ 10 per second) and circuit breaker with everything else.

Accumulate, never rewrite: a fact the SEC drops from a later companyfacts file stays here, and a
restated number is a new row from the newer filing.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from libs.sv_data.filed import parse_companyfacts
from libs.sv_data.raw import RawDocument, RawStore
from libs.sv_data.sources import SourceError, SourceNotFound
from libs.sv_data.sources import sec as sec_source

from ..config import get_settings
from ..i18n import AppError
from ..models import FiledConceptRow, FiledFactRow, Filer, Filing, RawDocumentRow, SecTicker, Setting

KEY = "data_sources"
TICKERS_MAX_AGE = dt.timedelta(days=7)
MAX_PARAMS = 30000          # PostgreSQL allows 65,535 bind parameters per statement


def _chunks(rows: list[dict]):
    size = max(1, MAX_PARAMS // max(1, len(rows[0]))) if rows else 1
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


# ------------------------------------------------------------------ settings

def data_sources(db: Session) -> dict:
    row = db.get(Setting, KEY)
    value = dict(row.value) if row else {}
    contact = value.get("sec_contact") or None
    return {"sec_contact": contact, "sec_ready": sec_source.valid_contact(contact)}


def set_data_sources(db: Session, sec_contact: Optional[str]) -> dict:
    contact = " ".join((sec_contact or "").split()) or None
    if contact is not None and not sec_source.valid_contact(contact):
        raise AppError("sec_contact_invalid")
    row = db.get(Setting, KEY)
    value = dict(row.value) if row else {}
    value["sec_contact"] = contact
    if row is None:
        db.add(Setting(key=KEY, value=value))
    else:
        row.value = value
    db.flush()
    return data_sources(db)


# ------------------------------------------------------------------ plumbing (replaced in tests)

def _http(url, headers):
    from src.fetch import httputil

    return httputil.get(url, headers=dict(headers), timeout=60, retries=3)


def client(db: Session) -> sec_source.SecClient:
    contact = data_sources(db)["sec_contact"]
    if not sec_source.valid_contact(contact):
        raise AppError("sec_contact_missing")
    return sec_source.SecClient(contact, _http)


def store() -> RawStore:
    return RawStore(get_settings().data_dir / "raw")


def keep(db: Session, doc: RawDocument) -> tuple[RawDocumentRow, bool]:
    """Save a fetched document. Returns (row, is_new); unchanged content only updates last_checked_at."""
    row = db.scalar(select(RawDocumentRow).where(
        RawDocumentRow.source == doc.source, RawDocumentRow.kind == doc.kind, RawDocumentRow.key == doc.key,
        RawDocumentRow.sha256 == doc.sha256))
    s = store()
    path = s.put(doc)
    if row is not None:
        row.last_checked_at = doc.fetched_at
        return row, False
    row = RawDocumentRow(source=doc.source, kind=doc.kind, key=doc.key, url=doc.url, sha256=doc.sha256, bytes=doc.size,
                         content_type=doc.content_type, path=s.relative(path), http_status=doc.http_status,
                         fetched_at=doc.fetched_at, last_checked_at=doc.fetched_at)
    db.add(row)
    db.flush()
    return row, True


class Upstream(RuntimeError):
    """The SEC could not answer (network, rate limit, server error); the API answers 502."""


def _fetch(call, *args):
    try:
        return call(*args)
    except SourceNotFound:
        raise
    except SourceError as exc:
        raise Upstream(f"SEC HTTP {exc.status}" + (f" ({exc.detail[:80]})" if exc.detail else "")) from exc


# ------------------------------------------------------------------ ticker → CIK

def refresh_tickers(db: Session, c: sec_source.SecClient) -> int:
    doc = _fetch(c.company_tickers)
    keep(db, doc)
    now = dt.datetime.now(dt.timezone.utc)
    rows = [{"ticker": e.ticker, "cik": e.cik, "title": e.title[:200], "updated_at": now}
            for e in sec_source.parse_company_tickers(doc.content)]
    for chunk in _chunks(rows):
        stmt = pg_insert(SecTicker).values(chunk)
        db.execute(stmt.on_conflict_do_update(index_elements=["ticker"], set_={
            "cik": stmt.excluded.cik, "title": stmt.excluded.title, "updated_at": stmt.excluded.updated_at}))
    db.flush()
    return len(rows)


def resolve_cik(db: Session, c: sec_source.SecClient, ticker: str) -> str:
    newest = db.scalar(select(func.max(SecTicker.updated_at)))
    row = db.get(SecTicker, ticker)
    if row is None or newest is None or dt.datetime.now(dt.timezone.utc) - newest > TICKERS_MAX_AGE:
        refresh_tickers(db, c)
        row = db.get(SecTicker, ticker)
    if row is None:
        raise AppError("sec_not_filer", ticker=ticker)
    return row.cik


# ------------------------------------------------------------------ sync

def _save_filings(db: Session, cik: str, entries) -> int:
    known = set(db.scalars(select(Filing.accession).where(Filing.cik == cik)))
    rows = [{"accession": f.accession, "cik": cik, "form": f.form[:16], "filed": f.filed, "report_date": f.report_date,
             "accepted_at": f.accepted_at, "primary_document": (f.primary_document or "")[:200] or None,
             "description": (f.description or "")[:200] or None, "items": (f.items or "")[:200] or None,
             "is_xbrl": f.is_xbrl, "is_inline_xbrl": f.is_inline_xbrl}
            for f in entries if f.accession not in known]
    rows = list({r["accession"]: r for r in rows}.values())
    for chunk in _chunks(rows):
        db.execute(pg_insert(Filing).values(chunk).on_conflict_do_nothing(index_elements=["accession"]))
    return len(rows)


def _save_facts(db: Session, cik: str, parsed, raw_id: int) -> int:
    known = {(r.taxonomy, r.concept, r.unit, r.period_start, r.period_end, r.accession) for r in db.execute(
        select(FiledFactRow.taxonomy, FiledFactRow.concept, FiledFactRow.unit, FiledFactRow.period_start,
               FiledFactRow.period_end, FiledFactRow.accession).where(FiledFactRow.cik == cik))}
    now = dt.datetime.now(dt.timezone.utc)
    rows = [{"cik": cik, "taxonomy": f.taxonomy, "concept": f.concept, "unit": f.unit, "value": f.value,
             "period_start": f.period_start, "period_end": f.period_end, "fy": f.fy, "fp": f.fp, "form": f.form[:16],
             "accession": f.accession, "filed": f.filed, "frame": f.frame, "raw_document_id": raw_id, "created_at": now}
            for f in parsed.facts if f.key not in known]
    for chunk in _chunks(rows):
        db.execute(pg_insert(FiledFactRow).values(chunk).on_conflict_do_nothing(constraint="uq_filed_facts_fact"))
    concepts = [{"taxonomy": c.taxonomy, "concept": c.concept, "label": c.label, "description": c.description}
                for c in parsed.concepts]
    for chunk in _chunks(concepts):
        db.execute(pg_insert(FiledConceptRow).values(chunk).on_conflict_do_nothing())
    return len(rows)


def sync_sec(db: Session, ticker: str, force: bool = False) -> dict:
    """Fetch submissions and companyfacts for one US ticker and store layers ① and ②.

    ``force`` re-reads older submission pages and re-parses unchanged companyfacts."""
    c = client(db)
    cik = resolve_cik(db, c, ticker)
    filer = db.get(Filer, cik)
    try:
        sub_doc = _fetch(c.submissions, cik)
        keep(db, sub_doc)
        sub = sec_source.parse_submissions(sub_doc.content)
        if filer is None:
            filer = Filer(cik=cik, facts=0, pages_loaded=[])
            db.add(filer)
        for k in ("name", "entity_type", "sic", "sic_description", "fiscal_year_end", "category", "state_of_incorporation"):
            setattr(filer, k, getattr(sub, k))
        filer.tickers, filer.exchanges = sub.tickers, sub.exchanges
        db.flush()
        filings_added = _save_filings(db, cik, sub.filings)
        loaded = [] if force else list(filer.pages_loaded or [])
        for page in sub.older_pages:
            if page in loaded:
                continue
            page_doc = _fetch(c.submissions, cik, page)
            keep(db, page_doc)
            filings_added += _save_filings(db, cik, sec_source.parse_submissions_page(page_doc.content))
            loaded.append(page)
        filer.pages_loaded = loaded

        try:
            facts_doc = _fetch(c.companyfacts, cik)
        except SourceNotFound:          # files with the SEC but has no XBRL financial data
            facts_doc = None
        facts_added, unchanged, parsed = 0, False, None
        if facts_doc is not None:
            raw, _ = keep(db, facts_doc)
            unchanged = filer.facts_document_id == raw.id
            if not unchanged or force:
                parsed = parse_companyfacts(facts_doc.content)
                facts_added = _save_facts(db, cik, parsed, raw.id)
            filer.facts_document_id = raw.id
        filer.facts = db.scalar(select(func.count()).select_from(FiledFactRow).where(FiledFactRow.cik == cik)) or 0
        filer.synced_at, filer.last_error = dt.datetime.now(dt.timezone.utc), None
        db.flush()
    except SourceNotFound as exc:
        raise AppError("sec_not_filer", ticker=ticker) from exc
    except Exception as exc:
        if filer is not None and filer in db:
            filer.last_error = f"{type(exc).__name__}: {exc}"[:500]
        raise
    return {"ticker": ticker, "cik": cik, "name": filer.name, "filings_added": filings_added,
            "facts_added": facts_added, "facts_total": filer.facts, "has_xbrl": facts_doc is not None,
            "unchanged": unchanged and not force,
            "duplicates": parsed.duplicates if parsed else 0, "conflicts": parsed.conflicts if parsed else 0,
            "skipped": parsed.skipped if parsed else 0}


# ------------------------------------------------------------------ reading

def filer_for(db: Session, ticker: str) -> Optional[Filer]:
    row = db.get(SecTicker, ticker)
    return db.get(Filer, row.cik) if row else None


def filing_urls(cik: str, accession: str, primary: Optional[str]) -> dict:
    return {"index_url": sec_source.filing_index_url(cik, accession),
            "document_url": sec_source.filing_document_url(cik, accession, primary) if primary else None}

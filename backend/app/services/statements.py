"""Statement data, layers ① raw and ② filed: fetch from the SEC, keep every document, store every fact.

The parsing lives in ``libs/sv_data`` (no database there); this module stores what it returns.
Every outbound request goes through ``src.fetch.httputil.get``, so SEC calls share the system-wide
pacing (≤ 10 per second) and circuit breaker with everything else.

Accumulate, never rewrite: a fact the SEC drops from a later companyfacts file stays here, and a
restated number is a new row from the newer filing.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
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

def _split(contact: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """An older single-string contact ("Org name a@b.com") → (organisation, email)."""
    m = sec_source.EMAIL.search(contact or "")
    if not m:
        return (" ".join((contact or "").split()) or None), None
    return (" ".join(sec_source.EMAIL.sub(" ", contact).split()) or None), m.group(0)


def data_sources(db: Session) -> dict:
    row = db.get(Setting, KEY)
    value = dict(row.value) if row else {}
    org, email = value.get("sec_organisation"), value.get("sec_email")
    if not (org or email) and value.get("sec_contact"):
        org, email = _split(value["sec_contact"])
    contact = f"{org} {email}" if org and email else None
    return {"sec_organisation": org or None, "sec_email": email or None, "sec_contact": contact,
            "sec_ready": sec_source.valid_contact(contact)}


def set_data_sources(db: Session, organisation: Optional[str], email: Optional[str]) -> dict:
    """SEC asks for both an organisation name and an e-mail; both empty clears the setting."""
    org = " ".join((organisation or "").split()) or None
    mail = (email or "").strip() or None
    if org or mail:
        if not org or len(org) > 120 or "@" in org:
            raise AppError("sec_organisation_invalid")
        if not mail or len(mail) > 254 or not sec_source.EMAIL.fullmatch(mail):
            raise AppError("email_invalid")
    row = db.get(Setting, KEY)
    value = {k: v for k, v in (dict(row.value) if row else {}).items() if k != "sec_contact"}
    value.update(sec_organisation=org, sec_email=mail)
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


FACTS_MAX_AGE = dt.timedelta(days=7)      # light syncs re-read companyfacts at least weekly
LOCK_TTL = 600


def sync_sec(db: Session, ticker: str, force: bool = False, light: bool = False) -> dict:
    """Fetch submissions and companyfacts for one US ticker and store layers ① and ②.

    ``force`` re-reads older submission pages and re-parses unchanged companyfacts.
    ``light`` (the daily check) reads the filing list and downloads companyfacts only when there is a new
    filing or the stored copy is older than FACTS_MAX_AGE: one small request per company on most days.
    One sync per company at a time across all processes; a second caller waits and reports what was stored."""
    from src.infra.kv import kv

    c = client(db)
    cik = resolve_cik(db, c, ticker)
    store_ = kv()
    lock = f"lk:stmt:{cik}"
    if not store_.set(lock, "1", ttl=LOCK_TTL, nx=True):
        import time as _t
        deadline = _t.monotonic() + 120
        while store_.get(lock) and _t.monotonic() < deadline:
            _t.sleep(0.25)
        db.expire_all()
        filer = db.get(Filer, cik)
        return {"ticker": ticker, "cik": cik, "name": filer.name if filer else None, "filings_added": 0, "facts_added": 0,
                "facts_total": filer.facts if filer else 0, "has_xbrl": bool(filer and filer.facts_document_id),
                "unchanged": True, "joined": True}
    try:
        out = _sync_sec(db, c, ticker, cik, force, light)
        db.commit()                      # visible to whoever waited on the lock
    finally:
        store_.delete(lock)
    # second source for the checks layer: weekly, or right after new filings
    if refresh_yahoo(db, ticker, force=bool(out["filings_added"] or out["facts_added"] or force)):
        db.commit()
    return out


def _sync_sec(db: Session, c: sec_source.SecClient, ticker: str, cik: str, force: bool, light: bool) -> dict:
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

        now = dt.datetime.now(dt.timezone.utc)
        stored = db.get(RawDocumentRow, filer.facts_document_id) if filer.facts_document_id else None
        skip_facts = (light and not force and filings_added == 0 and stored is not None
                      and now - stored.last_checked_at < FACTS_MAX_AGE)
        facts_doc, facts_added, unchanged, parsed = None, 0, True, None
        has_xbrl = stored is not None
        if not skip_facts:
            try:
                facts_doc = _fetch(c.companyfacts, cik)
            except SourceNotFound:          # files with the SEC but has no XBRL financial data
                facts_doc = None
            has_xbrl = facts_doc is not None
            unchanged = False
            if facts_doc is not None:
                raw, _ = keep(db, facts_doc)
                unchanged = filer.facts_document_id == raw.id
                if not unchanged or force:
                    parsed = parse_companyfacts(facts_doc.content)
                    facts_added = _save_facts(db, cik, parsed, raw.id)
                filer.facts_document_id = raw.id
        filer.facts = db.scalar(select(func.count()).select_from(FiledFactRow).where(FiledFactRow.cik == cik)) or 0
        filer.synced_at, filer.last_error = now, None
        db.flush()
    except SourceNotFound as exc:
        raise AppError("sec_not_filer", ticker=ticker) from exc
    except Exception as exc:
        if filer is not None and filer in db:
            filer.last_error = f"{type(exc).__name__}: {exc}"[:500]
        raise
    return {"ticker": ticker, "cik": cik, "name": filer.name, "filings_added": filings_added,
            "facts_added": facts_added, "facts_total": filer.facts, "has_xbrl": has_xbrl,
            "unchanged": unchanged and not force, "facts_skipped": skip_facts,
            "duplicates": parsed.duplicates if parsed else 0, "conflicts": parsed.conflicts if parsed else 0,
            "skipped": parsed.skipped if parsed else 0}


# ------------------------------------------------------------------ second source for checks: Yahoo

YAHOO_MAX_AGE = dt.timedelta(days=7)


def _yahoo_annual(ticker: str) -> Optional[dict]:
    """Yahoo's annual income statement, balance sheet and cash flow (unofficial; used only to cross-check).
    Replaced in tests. Paced and circuit-broken like every other Yahoo call."""
    import math

    from src.fetch import guard

    with guard.call("yahoo"):
        import yfinance as yf

        T = yf.Ticker(ticker)
        annual: dict[str, dict[str, float]] = {}
        for attr in ("income_stmt", "balance_sheet", "cashflow"):
            df = getattr(T, attr)
            if df is None or len(df) == 0:
                continue
            for c in df.columns:
                day = str(c.date()) if hasattr(c, "date") else str(c)[:10]
                row = annual.setdefault(day, {})
                for name, v in df[c].items():
                    if v is not None and not (isinstance(v, float) and math.isnan(v)):
                        row[str(name)] = float(v)
    return {"ticker": ticker, "source": "Yahoo Finance via yfinance", "annual": annual} if annual else None


def refresh_yahoo(db: Session, ticker: str, force: bool = False) -> bool:
    """Keep Yahoo's annual figures as a raw document (source "yahoo"), at most weekly. Never raises."""
    import json as _json
    import logging

    last = db.scalar(select(RawDocumentRow).where(RawDocumentRow.source == "yahoo", RawDocumentRow.kind == "annual_financials",
                                                  RawDocumentRow.key == ticker).order_by(RawDocumentRow.last_checked_at.desc()).limit(1))
    if last is not None and not force and dt.datetime.now(dt.timezone.utc) - last.last_checked_at < YAHOO_MAX_AGE:
        return False
    try:
        data = _yahoo_annual(ticker)
    except Exception as exc:
        logging.getLogger("sv.statements").info("Yahoo cross-check data for %s unavailable: %s", ticker, exc)
        return False
    if not data:
        return False
    body = _json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    keep(db, RawDocument("yahoo", "annual_financials", ticker, f"https://finance.yahoo.com/quote/{ticker}/financials", body))
    return True


def yahoo_figures(db: Session, ticker: str) -> tuple[Optional[dict], Optional[dt.datetime]]:
    """The latest stored Yahoo annual figures as {period end: {row: Decimal}}, and when they were fetched."""
    import json as _json

    row = db.scalar(select(RawDocumentRow).where(RawDocumentRow.source == "yahoo", RawDocumentRow.kind == "annual_financials",
                                                 RawDocumentRow.key == ticker).order_by(RawDocumentRow.last_checked_at.desc()).limit(1))
    if row is None:
        return None, None
    try:
        data = _json.loads(store().read(row.path))
    except (OSError, ValueError):
        return None, None
    out = {}
    for day, values in (data.get("annual") or {}).items():
        try:
            out[dt.date.fromisoformat(day[:10])] = {k: Decimal(str(v)) for k, v in values.items() if v is not None}
        except ValueError:
            continue
    return out, row.last_checked_at


# ------------------------------------------------------------------ background and daily

PENDING_TTL = 600
STATUS_KEY = "statements_status"


def _pending_key(ticker: str) -> str:
    return f"stmt:pending:{ticker}"


def is_pending(ticker: str) -> bool:
    from src.infra.kv import kv
    return kv().get(_pending_key(ticker)) is not None


def submit(ticker: str) -> bool:
    """Fetch one ticker's statements in the background (the same single worker thread as market syncs).
    False when one is already pending for that ticker."""
    from src.infra.kv import kv

    from . import market_jobs

    if not kv().set(_pending_key(ticker), "1", ttl=PENDING_TTL, nx=True):
        return False
    market_jobs.submit_call(_run_background, ticker)
    return True


def _run_background(ticker: str) -> None:
    import logging

    from src.infra.kv import kv

    from ..db import session

    try:
        with session() as db:
            sync_sec(db, ticker)
    except Exception as exc:  # the error is on the filer (last_error) or the ticker is not an SEC filer
        logging.getLogger("sv.statements").warning("background statements sync of %s failed: %s", ticker, exc)
        _remember_failure(ticker, exc)
    finally:
        kv().delete(_pending_key(ticker))


def _remember_failure(ticker: str, exc: Exception) -> None:
    """A ticker that is not an SEC filer has no filer row to carry the error; keep it briefly for the status."""
    from src.infra.kv import kv

    code = exc.code if isinstance(exc, AppError) else "upstream"
    kv().set(f"stmt:failed:{ticker}", code, ttl=PENDING_TTL)


def last_failure(ticker: str) -> Optional[str]:
    from src.infra.kv import kv
    return kv().get(f"stmt:failed:{ticker}")


def known_us_filer(db: Session, ticker: str) -> Optional[bool]:
    """True/False from SEC's ticker list without any network call; None when the list was never loaded."""
    if db.scalar(select(SecTicker.ticker).limit(1)) is None:
        return None
    return db.get(SecTicker, ticker) is not None


def update_all(session_factory) -> dict:
    """The daily check: every company with statements, in its own transaction (one failure doesn't stop the rest)."""
    started = dt.datetime.now(dt.timezone.utc)
    with session_factory() as db:
        if not data_sources(db)["sec_ready"]:
            return {"skipped": "sec_contact_missing", "started_at": started.isoformat(timespec="seconds")}
        work = [(f.cik, (f.tickers or [None])[0]) for f in db.scalars(select(Filer).where(Filer.synced_at.is_not(None)))]
        tickers = {cik: db.scalar(select(SecTicker.ticker).where(SecTicker.cik == cik).order_by(SecTicker.ticker).limit(1))
                   for cik, _ in work}
    checked, updated, failed = 0, [], []
    for cik, first in work:
        t = first or tickers.get(cik)
        if not t:
            continue
        try:
            with session_factory() as db:
                r = sync_sec(db, t, light=True)
            checked += 1
            if r["filings_added"] or r["facts_added"]:
                updated.append({"ticker": t, "filings_added": r["filings_added"], "facts_added": r["facts_added"]})
        except Exception as exc:
            failed.append({"ticker": t, "error": f"{type(exc).__name__}: {exc}"[:300]})
    summary = {"started_at": started.isoformat(timespec="seconds"),
               "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               "checked": checked, "updated": updated, "failed": failed}
    with session_factory() as db:
        row = db.get(Setting, STATUS_KEY)
        if row is None:
            db.add(Setting(key=STATUS_KEY, value=summary))
        else:
            row.value = summary
    return summary


# ------------------------------------------------------------------ reading

def filer_for(db: Session, ticker: str) -> Optional[Filer]:
    row = db.get(SecTicker, ticker)
    return db.get(Filer, row.cik) if row else None


def filing_urls(cik: str, accession: str, primary: Optional[str]) -> dict:
    return {"index_url": sec_source.filing_index_url(cik, accession),
            "document_url": sec_source.filing_document_url(cik, accession, primary) if primary else None}


# ------------------------------------------------------------------ layer ③ standard (computed on read)

def standard_statements(db: Session, filer: Filer, view: str = "latest", as_of: Optional[dt.date] = None,
                        years: int = 10, ticker: Optional[str] = None, period: str = "annual") -> dict:
    """Standardised statements from the stored filed facts: fiscal years or quarters (newest first),
    with the trailing twelve months as the first column when four consecutive quarters exist.

    Deterministic: the same filed facts, ruleset version, view and as_of give the same numbers, so the
    values are computed on read rather than stored; a valuation that uses them will record the
    ruleset version and as_of with it."""
    from sqlalchemy import tuple_

    from libs.sv_data.standard import FactRef, Split, load_ruleset, standardize, template_for_sic

    from ..models import CorporateAction

    template = template_for_sic(filer.sic)
    rs = load_ruleset(template)                  # general | bank | insurance, chosen by SIC code
    rows = db.execute(select(FiledFactRow).where(
        FiledFactRow.cik == filer.cik,
        tuple_(FiledFactRow.taxonomy, FiledFactRow.concept).in_(sorted(rs.concepts)))).scalars()
    facts = [FactRef(id=r.id, taxonomy=r.taxonomy, concept=r.concept, unit=r.unit, value=r.value,
                     period_start=r.period_start, period_end=r.period_end, form=r.form, accession=r.accession,
                     filed=r.filed) for r in rows]
    # stock splits from the accumulated market data; only those the filings confirm are applied
    def ratio(v: float) -> Decimal:                  # stored as float (market data): 4.0 → 4, 1.5 → 1.5
        d = Decimal(str(v))
        return d.to_integral_value() if d == d.to_integral_value() else d.normalize()

    splits = [Split(a.date, ratio(a.value)) for a in db.scalars(select(CorporateAction).where(
        CorporateAction.ticker == (ticker or ""), CorporateAction.kind == "split"))]
    std = standardize(facts, rs, view=view, as_of=as_of, splits=splits)
    periods = std.quarters if period == "quarterly" else std.fiscal_years
    fys = list(reversed(periods))[:years]                        # newest first
    if std.ttm is not None:
        fys = [std.ttm] + fys
    used = {v.fact.accession for v in std.values.values() if v.fact is not None}
    used |= {f.accession for v in std.values.values() for _, f in v.parts if f is not None}
    primary = {a: p for a, p in db.execute(select(Filing.accession, Filing.primary_document).where(Filing.accession.in_(used)))} if used else {}

    def cell(code: str, label: str) -> Optional[dict]:
        v = std.get(code, label)
        if v is None:
            return None
        out = {"value": format(v.value, "f"), "method": v.method, "rule_id": v.rule_id, "concept": v.concept,
               "restated": v.restated, "change": v.change, "formula": v.formula, "inputs": v.inputs,
               "raw_value": format(v.raw_value, "f") if v.raw_value is not None else None,
               "split_factor": format(v.split_factor.normalize(), "f"), "assumed_zero": v.assumed_zero,
               "original_value": format(v.original_value, "f") if v.original_value is not None else None,
               "latest_value": format(v.latest_value, "f") if v.latest_value is not None else None,
               "form": None, "accession": None, "filed": None, "index_url": None, "document_url": None}
        if v.fact is not None:
            out.update(form=v.fact.form, accession=v.fact.accession, filed=v.fact.filed.isoformat(),
                       **filing_urls(filer.cik, v.fact.accession, primary.get(v.fact.accession)))
        out["parts"] = [{"sign": sign, "value": format(f.value, "f"), "form": f.form, "accession": f.accession,
                         "filed": f.filed.isoformat(), "period_start": f.period_start.isoformat() if f.period_start else None,
                         "period_end": f.period_end.isoformat(), **filing_urls(filer.cik, f.accession, primary.get(f.accession))}
                        for sign, f in v.parts if f is not None]
        return out

    from libs.sv_data.checks import grades, load_checks, run_checks

    cs = load_checks(f"checks_{template}")
    yahoo, yahoo_at = yahoo_figures(db, ticker or "")
    results = run_checks(std, cs, external={"yahoo": yahoo} if yahoo is not None else None)
    shown = {y.label for y in fys}
    grade = grades(results)
    summary: dict[str, dict[str, int]] = {}
    for r in results:
        if r.period in shown:
            summary.setdefault(r.period, {"pass": 0, "fail": 0, "skip": 0})[r.status] += 1
    checks = {"checkset": cs.name, "version": cs.version,
              "yahoo_fetched_at": yahoo_at.isoformat(timespec="seconds") if yahoo_at else None,
              "grades": {k: v for k, v in grade.items() if k in shown}, "summary": summary,
              "failures": [{"rule_id": r.rule_id, "period": r.period, "severity": r.severity, "values": r.values,
                            "difference": format(r.difference.quantize(Decimal("0.0001")), "f") if r.difference is not None else None}
                           for r in results if r.status == "fail" and r.period in shown]}

    from libs.sv_data.ratios import cagr, compute_ratios, growth, load_ratios

    rr = load_ratios(f"ratios_{template}")
    rv = compute_ratios(std, rr, rs)
    gv = growth(std, rr.growth)
    cv = cagr(std, rr.growth)
    q4 = lambda d: format(d.quantize(Decimal("0.0001")), "f")           # noqa: E731
    ratios = {"ratioset": rr.name, "version": rr.version,
              "rows": [{"code": m.id, "kind": m.kind,
                        "cells": [({"value": q4(rv[(m.id, y.label)].value), "inputs": rv[(m.id, y.label)].inputs}
                                   if (m.id, y.label) in rv else None) for y in fys]} for m in rr.measures],
              "growth": [{"code": c, "cells": [q4(gv[(c, y.label)]) if (c, y.label) in gv else None for y in fys]}
                         for c in rr.growth],
              "cagr": [{"code": c, "years": n, "value": q4(cv[(c, n)])} for c in rr.growth for n in (3, 5) if (c, n) in cv]}

    return {"cik": filer.cik, "name": filer.name, "template": template, "template_applied": rs.template, "checks": checks,
            "ratios": ratios,
            "ruleset": rs.name, "ruleset_version": rs.version, "currency": std.currency, "view": view,
            "as_of": as_of.isoformat() if as_of else None,
            "splits_applied": [{"date": sp.date.isoformat(), "ratio": format(sp.ratio.normalize(), "f")} for sp in std.splits_applied],
            "period": period,
            "years": [{"label": y.label, "start": y.start.isoformat(), "end": y.end.isoformat(), "kind": y.kind,
                       "fiscal_year": y.fy_label, "quarter": y.quarter} for y in fys],
            "rows": [{"code": code, "statement": st, "unit": rs.item(code).unit, "total": code in rs.totals,
                      "cells": [cell(code, y.label) for y in fys]}
                     for st in ("income", "balance", "cash_flow") for code in rs.display_order(st)]}

"""Stock document library, hybrid search, standard-layer numeric tool, web fallback and Q&A (D13, alpha.5)."""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from libs.sv_docs import (
    CandidateChunk,
    ParsedDocument,
    build_qa_prompt,
    build_statements_summary_doc,
    build_valuation_doc,
    chunk_document,
    compress_history,
    embed_hash,
    evidence_sufficient,
    extractive_answer,
    hybrid_rank,
    parse_html_filing,
    parse_markdown,
    parse_pdf,
    parse_plain_text,
    refusal_text,
    summarize_document,
    verify_citations,
)

from ..i18n import AppError
from ..models import DocumentChunk, Filing, QaMessage, QaThread, Run, StockDocument
from . import settings_store, statements

log = logging.getLogger("sv.docs")

MAX_UPLOAD_BYTES = 10 * 1024 * 1024       # 10 MB per document
SEC_TEXT_FORMS = ("10-K", "10-Q", "8-K", "20-F")

NUMERIC_KEYWORDS: list[tuple[re.Pattern, list[str]]] = [
    (re.compile(r"營收|营收|收入|revenue|sales", re.I), ["revenue", "net_interest_income", "premiums_earned"]),
    (re.compile(r"毛利|gross\s*profit|gross\s*margin", re.I), ["gross_profit", "revenue"]),
    (re.compile(r"營業利潤|营业利润|operating\s*income|ebit|operating\s*margin", re.I), ["operating_income"]),
    (re.compile(r"淨利|净利|盈利|利潤|利润|net\s*income|earnings|profit", re.I), ["net_income", "net_income_parent"]),
    (re.compile(r"eps|每股盈利|每股收益", re.I), ["eps_diluted", "eps_basic"]),
    (re.compile(r"自由現金流|自由现金流|fcf|free\s*cash", re.I), ["free_cash_flow", "cfo", "capex"]),
    (re.compile(r"現金流|现金流|cash\s*flow|cfo", re.I), ["cfo", "capex", "free_cash_flow"]),
    (re.compile(r"資本開支|资本开支|capex|capital\s*expenditure", re.I), ["capex"]),
    (re.compile(r"資產|资产|asset|負債|负债|liabilit|權益|权益|equity|現金|现金|cash|債務|债务|debt", re.I),
     ["total_assets", "total_liabilities", "total_equity", "cash", "debt_noncurrent"]),
    (re.compile(r"roe|roa|roic|比率|ratio|margin|利潤率|利润率|增長|增长|growth|cagr", re.I),
     ["revenue", "net_income", "total_equity", "total_assets"]),
]

RECENT_EVENT_RE = re.compile(
    r"最新|最近|今日|今天|本週|本周|本月|新聞|新闻|剛公布|刚公布|latest|recent|today|breaking|news|current\s+quarter",
    re.I,
)


# ------------------------------------------------------------------ ingestion

def ingest_parsed(
    db: Session,
    ticker: str,
    parsed: ParsedDocument,
    *,
    source_kind: str,
    doc_date: Optional[str] = None,
    form: Optional[str] = None,
    accession: Optional[str] = None,
    url: Optional[str] = None,
) -> tuple[StockDocument, bool]:
    """Store a parsed document and its ~500-token chunks (idempotent on ticker + sha256)."""
    existing = db.scalar(select(StockDocument).where(
        StockDocument.ticker == ticker, StockDocument.sha256 == parsed.sha256
    ))
    if existing is not None:
        return existing, False

    chunks = chunk_document(parsed, target_tokens=500, overlap_tokens=60)
    if not chunks:
        raise AppError("doc_empty_text")
    doc_summary, sec_summaries = summarize_document(parsed.sections)
    doc = StockDocument(
        ticker=ticker,
        source_kind=source_kind,
        title=(parsed.title or ticker)[:240],
        doc_date=doc_date,
        form=(form or "")[:20] or None,
        accession=(accession or "")[:24] or None,
        url=(url or parsed.url or "")[:500] or None,
        sha256=parsed.sha256,
        summary=doc_summary,
        section_summaries=sec_summaries,
        chunk_count=len(chunks),
        status="ready",
    )
    db.add(doc)
    db.flush()

    for ch in chunks:
        db.add(DocumentChunk(
            document_id=doc.id,
            ticker=ticker,
            seq=ch.seq,
            section=(ch.section or "")[:200],
            page=ch.page,
            content=ch.content,
            token_count=ch.token_count,
            url=(ch.url or doc.url or "")[:500] or None,
            embedding=embed_hash(f"{ch.section} {ch.content}"),
        ))
    db.flush()
    return doc, True


def ingest_upload(
    db: Session,
    ticker: str,
    filename: str,
    data: bytes,
    *,
    title: Optional[str] = None,
) -> tuple[StockDocument, bool]:
    if not data:
        raise AppError("file_empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise AppError("file_too_large", kb=MAX_UPLOAD_BYTES // 1024)
    ext = Path(filename or "").suffix.lower()
    doc_title = (title or Path(filename or "document").stem or ticker).strip()[:240]
    if ext == ".pdf":
        parsed = parse_pdf(data, title=doc_title)
        kind = "hkex_pdf" if ticker.endswith(".HK") else "upload"
        form = "PDF"
    elif ext in (".md", ".markdown"):
        parsed = parse_markdown(data.decode("utf-8", errors="replace"), title=doc_title)
        kind, form = "upload", "MD"
    elif ext in (".html", ".htm"):
        parsed = parse_html_filing(data, title=doc_title)
        kind, form = "upload", "HTML"
    elif ext in (".txt", ".csv"):
        parsed = parse_plain_text(data.decode("utf-8", errors="replace"), title=doc_title)
        kind, form = "upload", "TXT"
    else:
        raise AppError("doc_unsupported_type")
    if not parsed.sections:
        raise AppError("doc_empty_text")
    return ingest_parsed(db, ticker, parsed, source_kind=kind, doc_date=dt.date.today().isoformat(), form=form)


def _fetch_sec_document(url: str, contact: str) -> bytes:
    from src.fetch import httputil

    status, body, _ = httputil.get(url, headers={"User-Agent": contact, "Accept-Encoding": "gzip, deflate"}, timeout=60, retries=2)
    if status != 200 or not body:
        raise RuntimeError(f"SEC HTTP {status}")
    return body


def sync_stock_library(db: Session, ticker: str, *, include_sec: bool = True, max_filings: int = 3) -> dict[str, Any]:
    """Ingest existing valuations, standardized financial summary, and recent SEC filing prose."""
    added, skipped, errors = 0, 0, []

    # 1. Completed valuation runs (calc JSON / markdown report)
    runs = db.scalars(select(Run).where(Run.ticker == ticker, Run.status == "done").order_by(Run.run_at.desc()).limit(5)).all()
    for r in runs:
        try:
            calc = json.loads(Path(r.calc_json_path).read_text(encoding="utf-8")) if (r.calc_json_path and Path(r.calc_json_path).is_file()) else None
            md_text = ""
            if r.calc_json_path:
                cand_md = Path(r.calc_json_path).with_name(f"{r.valuation_date}_{r.ticker}_valuation.md")
                if cand_md.is_file():
                    md_text = cand_md.read_text(encoding="utf-8")
            if calc:
                parsed = build_valuation_doc(calc, report_md=md_text, url=f"/api/v1/runs/{r.id}/report")
                _, is_new = ingest_parsed(db, ticker, parsed, source_kind="valuation",
                                          doc_date=r.valuation_date, form="VALUATION", url=f"/api/v1/runs/{r.id}/report")
                added += int(is_new)
                skipped += int(not is_new)
        except Exception as exc:
            errors.append(f"valuation {r.id}: {type(exc).__name__}")

    # 2. Standardized statements summary (searchable index; exact numbers still come from standard tool)
    filer = statements.filer_for(db, ticker)
    if filer is not None and filer.synced_at is not None:
        try:
            std = statements.standard_statements(db, filer, view="latest", years=6, ticker=ticker, period="annual")
            if std.get("years"):
                parsed = build_statements_summary_doc(std, ticker=ticker, url=f"/stock/{ticker}/statements")
                _, is_new = ingest_parsed(db, ticker, parsed, source_kind="statements",
                                          doc_date=std["years"][0]["end"], form="STATEMENTS", url=f"/stock/{ticker}/statements")
                added += int(is_new)
                skipped += int(not is_new)
        except Exception as exc:
            errors.append(f"statements: {type(exc).__name__}")

        # 3. Recent SEC 10-K / 10-Q / 8-K primary HTML filings
        contact = statements.data_sources(db).get("sec_contact")
        if include_sec and contact:
            filings = db.scalars(select(Filing).where(
                Filing.cik == filer.cik, Filing.form.in_(SEC_TEXT_FORMS), Filing.primary_document.is_not(None)
            ).order_by(Filing.filed.desc()).limit(max_filings)).all()
            for fil in filings:
                urls = statements.filing_urls(filer.cik, fil.accession, fil.primary_document)
                doc_url = urls.get("document_url")
                if not doc_url:
                    continue
                if db.scalar(select(StockDocument.id).where(StockDocument.ticker == ticker, StockDocument.accession == fil.accession)):
                    skipped += 1
                    continue
                try:
                    raw = _fetch_sec_document(doc_url, contact)
                    title = f"{ticker} {fil.form} ({fil.report_date or fil.filed})"
                    parsed = parse_html_filing(raw, title=title, form=fil.form, url=doc_url)
                    if parsed.sections:
                        _, is_new = ingest_parsed(
                            db, ticker, parsed, source_kind="sec_filing",
                            doc_date=(fil.filed or fil.report_date).isoformat() if (fil.filed or fil.report_date) else None,
                            form=fil.form, accession=fil.accession, url=doc_url,
                        )
                        added += int(is_new)
                        skipped += int(not is_new)
                except Exception as exc:
                    errors.append(f"{fil.form} {fil.accession}: {type(exc).__name__}")

    total = db.scalar(select(func.count()).select_from(StockDocument).where(StockDocument.ticker == ticker)) or 0
    return {"ticker": ticker, "added": added, "skipped": skipped, "total_documents": int(total), "errors": errors[:10]}


# ------------------------------------------------------------------ retrieval & numeric tool

def retrieve_chunks(db: Session, ticker: str, query: str, *, top_k: int = 10, max_tokens: int = 6500):
    """Hybrid PostgreSQL FTS + BM25 + dense vector cosine ranking over a stock's document chunks."""
    # PostgreSQL full-text rank (gracefully returns 0 if query has only CJK punctuation)
    fts_rows = db.execute(
        text(
            "SELECT dc.id, ts_rank_cd(to_tsvector('simple', coalesce(dc.section, '') || ' ' || dc.content), "
            "plainto_tsquery('simple', :q)) AS r "
            "FROM document_chunks dc WHERE dc.ticker = :t"
        ),
        {"q": query, "t": ticker},
    ).all()
    fts_map = {int(row[0]): float(row[1] or 0.0) for row in fts_rows}

    rows = db.execute(
        select(DocumentChunk, StockDocument)
        .join(StockDocument, DocumentChunk.document_id == StockDocument.id)
        .where(DocumentChunk.ticker == ticker)
    ).all()
    candidates = [
        CandidateChunk(
            chunk_id=ch.id,
            document_id=doc.id,
            source_kind=doc.source_kind,
            title=doc.title,
            doc_date=doc.doc_date,
            section=ch.section,
            page=ch.page,
            content=ch.content,
            token_count=ch.token_count,
            url=ch.url or doc.url,
            embedding=ch.embedding,
            fts_rank=fts_map.get(ch.id, 0.0),
        )
        for ch, doc in rows
    ]
    return hybrid_rank(query, candidates, top_k=top_k, max_tokens=max_tokens)


def extract_numeric_facts(db: Session, ticker: str, question: str) -> list[dict[str, Any]]:
    """Standard-layer numeric tool (D13): read exact financial & valuation figures directly from DB."""
    wanted_codes: list[str] = []
    for pat, codes in NUMERIC_KEYWORDS:
        if pat.search(question):
            for c in codes:
                if c not in wanted_codes:
                    wanted_codes.append(c)

    wants_val = bool(re.search(r"估值|理論價|理论价|目標價|目标价|fair\s*value|dcf|wacc|valuation", question, re.I))
    facts: list[dict[str, Any]] = []

    if wanted_codes:
        filer = statements.filer_for(db, ticker)
        if filer is not None and filer.synced_at is not None:
            std = statements.standard_statements(db, filer, view="latest", years=5, ticker=ticker, period="annual")
            years = [y["label"] for y in std.get("years") or []]
            ccy = std.get("currency") or ""
            row_map = {r["code"]: r for r in std.get("rows") or []}
            for code in wanted_codes[:6]:
                row = row_map.get(code)
                if not row:
                    continue
                vals = []
                first_url, first_filed, first_form = None, None, None
                for y, cell in zip(years, row["cells"]):
                    if cell and cell.get("value") is not None:
                        vals.append(f"{y}: {cell['value']} {ccy}".strip())
                        if first_url is None:
                            first_url = cell.get("document_url") or cell.get("index_url")
                            first_filed = cell.get("filed")
                            first_form = cell.get("form")
                if vals:
                    facts.append({
                        "title": f"{ticker} Standardized Statements ({std.get('ruleset_version')})",
                        "period": ", ".join(years[:5]),
                        "form": first_form or "10-K",
                        "filed": first_filed,
                        "url": first_url or f"/stock/{ticker}/statements",
                        "summary": f"{code} — " + "; ".join(vals[:5]),
                    })
            # Include key ratios when ratio/margin/ROE/ROIC asked
            if re.search(r"roe|roa|roic|margin|毛利率|淨利率|净利率|比率|ratio|cagr|增長|增长", question, re.I):
                for rr in (std.get("ratios") or {}).get("rows") or []:
                    if rr["code"] in ("gross_margin", "operating_margin", "net_margin", "roe", "roic", "fcf_margin"):
                        rvals = [f"{y}: {float(c['value']) * 100:.2f}%" for y, c in zip(years, rr["cells"]) if c and c.get("value") is not None]
                        if rvals:
                            facts.append({
                                "title": f"{ticker} Standardized Ratios",
                                "period": ", ".join(years[:5]),
                                "form": "RATIO",
                                "filed": None,
                                "url": f"/stock/{ticker}/statements",
                                "summary": f"{rr['code']} — " + "; ".join(rvals[:5]),
                            })

    if wants_val:
        run = db.scalar(select(Run).where(Run.ticker == ticker, Run.status == "done").order_by(Run.run_at.desc()).limit(1))
        if run is not None:
            facts.append({
                "title": f"{ticker} Latest Valuation ({run.valuation_date})",
                "period": run.valuation_date,
                "form": "VALUATION",
                "filed": run.valuation_date,
                "url": f"/api/v1/runs/{run.id}/report",
                "summary": (
                    f"Latest valuation ({run.valuation_date}): composite fair value {run.composite_value} {run.trading_ccy or ''}, "
                    f"market price {run.price} ({run.price_date}), WACC {run.wacc}, terminal g {run.terminal_g}."
                ),
            })
    return facts


# ------------------------------------------------------------------ optional web fallback

def _web_search(ticker: str, query: str, limit: int = 3) -> list[dict[str, str]]:
    """Fetch public web snippets for a ticker (monkeypatched in offline tests)."""
    from src.fetch import httputil

    url = f"https://html.duckduckgo.com/html/?q={quote_plus(f'{ticker} {query}')}"
    status, body, _ = httputil.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15, retries=1)
    if status != 200 or not body:
        return []
    soup = BeautifulSoup(body.decode("utf-8", errors="replace"), "lxml")
    out: list[dict[str, str]] = []
    for res in soup.select(".result")[:limit]:
        a = res.select_one(".result__a")
        sn = res.select_one(".result__snippet")
        if not a:
            continue
        href = str(a.get("href") or "").strip()
        title = a.get_text(" ", strip=True)
        snippet = sn.get_text(" ", strip=True) if sn else ""
        if href.startswith(("http://", "https://")) and (title or snippet):
            out.append({"title": title[:200], "url": href[:500], "content": f"{title}\n\n{snippet}".strip()})
    return out


def search_and_ingest_web(db: Session, ticker: str, query: str) -> int:
    """Search the web, archive each fetched result into ``stock_documents`` (source_kind='web'), return count added."""
    today = dt.date.today().isoformat()
    added = 0
    try:
        results = _web_search(ticker, query, limit=3)
    except Exception as exc:
        log.warning("web fallback failed for %s: %s", ticker, type(exc).__name__)
        return 0
    for item in results:
        if not item.get("content"):
            continue
        parsed = parse_plain_text(item["content"], title=item.get("title") or f"{ticker} Web Result", url=item.get("url"))
        if parsed.sections:
            _, is_new = ingest_parsed(
                db, ticker, parsed, source_kind="web", doc_date=today, form="WEB", url=item.get("url")
            )
            added += int(is_new)
    return added


# ------------------------------------------------------------------ Q&A orchestration

def _call_llm(db: Session, messages: list[dict[str, str]]) -> tuple[Optional[str], Optional[str]]:
    """Call configured OpenAI-compatible chat model (supports local 7B–14B and cloud models)."""
    ai = settings_store.ai_settings(db)
    pub = settings_store.public_ai_settings(db)
    if not pub.get("configured"):
        return None, None
    import requests

    url = ai["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if ai.get("api_key"):
        headers["Authorization"] = f"Bearer {ai['api_key']}"
    payload = {"model": ai["model"], "messages": messages, "temperature": 0.1, "max_tokens": 1200}
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    return content.strip() or None, str(ai["model"])


def ask_question(
    db: Session,
    *,
    user_id: str,
    ticker: str,
    question: str,
    thread_id: Optional[str] = None,
    allow_web: bool = False,
    lang: str = "zh-Hant",
) -> dict[str, Any]:
    """Answer a question about ``ticker`` grounded in library + standard-layer tool (+ optional web fallback)."""
    q = question.strip()
    if thread_id:
        thread = db.get(QaThread, thread_id)
        if thread is None or thread.user_id != user_id or thread.ticker != ticker:
            raise AppError("thread_not_found")
    else:
        thread = QaThread(user_id=user_id, ticker=ticker, title=q[:120])
        db.add(thread)
        db.flush()

    prior = db.scalars(select(QaMessage).where(QaMessage.thread_id == thread.id).order_by(QaMessage.id)).all()
    prior_dicts = [{"role": m.role, "content": m.content} for m in prior]
    summary, recent_turns = compress_history(prior_dicts, thread.summary or "", max_recent=6)
    if summary != (thread.summary or ""):
        thread.summary = summary

    user_msg = QaMessage(thread_id=thread.id, role="user", content=q, citations=[])
    db.add(user_msg)
    db.flush()

    # 1. Standard-layer numeric tool (exact numbers, never hallucinated from prose)
    numeric_facts = extract_numeric_facts(db, ticker, q)

    # 2. Library-first hybrid search
    ranked = retrieve_chunks(db, ticker, q, top_k=10, max_tokens=6500)
    sufficient = evidence_sufficient(ranked, q)

    # 3. Optional web fallback ONLY when library evidence is insufficient or asking about latest post-filing events
    used_web = False
    if allow_web and (not sufficient or RECENT_EVENT_RE.search(q)):
        if search_and_ingest_web(db, ticker, q) > 0:
            ranked = retrieve_chunks(db, ticker, q, top_k=10, max_tokens=6500)
            sufficient = evidence_sufficient(ranked, q)

    top_score = ranked[0].score if ranked else 0.0
    used_web = any(rc.chunk.source_kind == "web" and rc.score >= 0.18 for rc in ranked)

    # 4. Generate grounded answer (refuse strictly if no evidence)
    model_used: Optional[str] = None
    insufficient = False
    if not sufficient and not numeric_facts:
        answer_text = refusal_text(lang)
        verified_cits = []
        insufficient = True
    else:
        prompt_msgs, cmap = build_qa_prompt(
            q, ticker, ranked, numeric_facts=numeric_facts,
            history_summary=summary, recent_turns=recent_turns, lang=lang,
        )
        llm_text: Optional[str] = None
        try:
            llm_text, model_used = _call_llm(db, prompt_msgs)
        except Exception as exc:
            log.warning("QA LLM call failed, falling back to extractive answer: %s", type(exc).__name__)
            llm_text = None

        if llm_text:
            answer_text, verified_cits = verify_citations(llm_text, cmap)
            if not verified_cits and refusal_text(lang) not in answer_text:
                # Never return uncited LLM claims: fall back to verified extractive citations
                answer_text, verified_cits, insufficient = extractive_answer(
                    q, ranked, numeric_facts=numeric_facts, lang=lang
                )
        else:
            answer_text, verified_cits, insufficient = extractive_answer(
                q, ranked, numeric_facts=numeric_facts, lang=lang
            )

    cits_json = [c.to_dict() for c in verified_cits]
    used_web = used_web or any(c["source_type"] == "web" for c in cits_json)
    assistant_msg = QaMessage(
        thread_id=thread.id,
        role="assistant",
        content=answer_text,
        citations=cits_json,
        used_web=used_web,
        used_statements=bool(numeric_facts),
        insufficient_evidence=insufficient,
        model=model_used,
    )
    db.add(assistant_msg)
    thread.updated_at = dt.datetime.now(dt.timezone.utc)
    db.flush()
    return {
        "thread": thread,
        "message": assistant_msg,
        "retrieved_chunks": len(ranked),
        "top_score": round(float(top_score), 4),
    }

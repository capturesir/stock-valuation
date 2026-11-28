"""Stock document library, bilingual hybrid search, rich structured financial tool, web fallback and Q&A (D13)."""
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
    is_overview_query,
    normalize_cjk,
    parse_html_filing,
    parse_markdown,
    parse_pdf,
    parse_plain_text,
    refusal_text,
    refusal_with_hints,
    summarize_document,
    verify_citations,
)

from ..i18n import AppError
from ..models import AiSuggestion, DocumentChunk, Filer, Filing, PriceBar, QaMessage, QaThread, Run, Security, StockDocument
from . import settings_store, statements, valuation_methods

log = logging.getLogger("sv.docs")

MAX_UPLOAD_BYTES = 10 * 1024 * 1024       # 10 MB per document
SEC_TEXT_FORMS = ("10-K", "10-Q", "8-K", "20-F")

NUMERIC_KEYWORDS: list[tuple[re.Pattern, list[str]]] = [
    (re.compile(r"營收|营收|收入|銷售|销售|業績|业绩|revenue|sales|turnover", re.I),
     ["revenue", "net_interest_income", "premiums_earned"]),
    (re.compile(r"毛利|gross\s*profit|gross\s*margin", re.I), ["gross_profit", "revenue"]),
    (re.compile(r"營業利潤|营业利润|經營利潤|经营利润|operating\s*income|ebit|operating\s*margin", re.I),
     ["operating_income"]),
    (re.compile(r"淨利|净利|盈利|利潤|利润|獲利|获利|net\s*income|earnings|profit", re.I),
     ["net_income", "net_income_parent"]),
    (re.compile(r"\beps\b|每股盈利|每股收益", re.I), ["eps_diluted", "eps_basic"]),
    (re.compile(r"自由現金流|自由现金流|\bfcf\b|free\s*cash", re.I), ["free_cash_flow", "cfo", "capex"]),
    (re.compile(r"現金流|现金流|cash\s*flow|\bcfo\b", re.I), ["cfo", "capex", "free_cash_flow"]),
    (re.compile(r"資本開支|资本开支|capex|capital\s*expenditure", re.I), ["capex"]),
    (re.compile(r"股息|派息|分紅|分红|回購|回购|dividend|payout|buyback|repurchase", re.I),
     ["dividends_paid", "share_repurchases", "free_cash_flow"]),
    (re.compile(r"資產|资产|asset|負債|负债|liabilit|權益|权益|equity|現金|现金|\bcash\b|債務|债务|debt|槓桿|杠杆|償債|偿债", re.I),
     ["total_assets", "total_liabilities", "total_equity", "cash", "debt_noncurrent"]),
    (re.compile(r"\broe\b|\broa\b|\broic\b|比率|ratio|margin|利潤率|利润率|增長|增长|growth|cagr|財務|财务|财报|財報|基本面|杜邦|dupont", re.I),
     ["revenue", "net_income", "free_cash_flow", "total_equity", "total_assets"]),
]

RECENT_EVENT_RE = re.compile(
    r"最新|最近|今日|今天|本週|本周|本月|新聞|新闻|剛公布|刚公布|動態|动态|消息|催化|"
    r"latest|recent|today|breaking|news|current\s+quarter|catalyst|headline",
    re.I,
)

VALUATION_INTENT_RE = re.compile(
    r"估值|理論價|理论价|目標價|目标价|內在價值|内在价值|貴不貴|贵不贵|便宜|高估|低估|合理價|合理价|值得買|值得买|值得投資|值得投资|"
    r"市盈率|本益比|市淨率|市净率|倍數|倍数|fair\s*value|intrinsic|dcf|wacc|valuation|target\s*price|multiple|\bp/e\b|\bp/b\b|ev/ebitda",
    re.I,
)

PROFILE_INTENT_RE = re.compile(
    r"公司概況|公司概况|公司簡介|公司简介|介紹|介绍|主營|主营|做什麼|做什么|業務|业务|行業|行业|板塊|板块|"
    r"股價|股价|收盤價|收盘价|市價|市价|交易所|share\s+price|stock\s+price|market\s+price|trading\s+at|"
    r"company\s+(?:profile|overview|info|summary)|what\s+does\s+\w+\s+do|business\s+model|sector|industry",
    re.I,
)

ANALYTICS_INTENT_RE = re.compile(
    r"杜邦|dupont|\broe\b|\broa\b|\broic\b|盈利品質|盈利质量|應計|应计|accrual|sloan|現金含量|现金含量|財務健康|财务健康|基本面|財務品質|财务质量|earnings\s+quality",
    re.I,
)

RISK_INTENT_RE = re.compile(
    r"風險|风险|隱患|隐患|缺點|缺点|紅隊|红队|警示|警告|異常|异常|問題|问题|挑戰|挑战|利空|擔憂|担忧|"
    r"\brisks?\b|red\s*team|warning|concern|anomal|weakness|downside|threat",
    re.I,
)

FOLLOWUP_RE = re.compile(
    r"^(?:那|它|其|這|这|還有|还有|另外|以及|再|詳細|详细|具體|具体|為甚麼|為什麼|为什么|怎麼樣|怎么样|呢|"
    r"and\s+|what\s+about\s+|how\s+about\s+|its\s+|their\s+|more\s+|why\s+|can\s+you\s+elaborate)",
    re.I,
)

_ITEM_LABELS: dict[str, dict[str, str]] = {
    "revenue": {"zh-Hant": "營業收入", "zh-Hans": "营业收入", "en": "Revenue"},
    "net_interest_income": {"zh-Hant": "淨利息收入", "zh-Hans": "净利息收入", "en": "Net interest income"},
    "premiums_earned": {"zh-Hant": "已賺保費淨額", "zh-Hans": "已赚保费净额", "en": "Premiums earned"},
    "gross_profit": {"zh-Hant": "毛利", "zh-Hans": "毛利", "en": "Gross profit"},
    "operating_income": {"zh-Hant": "營業利潤", "zh-Hans": "营业利润", "en": "Operating income"},
    "net_income": {"zh-Hant": "淨利", "zh-Hans": "净利", "en": "Net income"},
    "net_income_parent": {"zh-Hant": "歸屬股東淨利", "zh-Hans": "归属股东净利", "en": "Net income (parent)"},
    "eps_diluted": {"zh-Hant": "攤薄每股盈利", "zh-Hans": "摊薄每股盈利", "en": "Diluted EPS"},
    "eps_basic": {"zh-Hant": "基本每股盈利", "zh-Hans": "基本每股盈利", "en": "Basic EPS"},
    "free_cash_flow": {"zh-Hant": "自由現金流", "zh-Hans": "自由现金流", "en": "Free cash flow"},
    "cfo": {"zh-Hant": "經營活動現金流", "zh-Hans": "经营活动现金流", "en": "Operating cash flow"},
    "capex": {"zh-Hant": "資本開支", "zh-Hans": "资本开支", "en": "CapEx"},
    "dividends_paid": {"zh-Hant": "已付股息", "zh-Hans": "已付股息", "en": "Dividends paid"},
    "share_repurchases": {"zh-Hant": "股份回購", "zh-Hans": "股份回购", "en": "Share repurchases"},
    "total_assets": {"zh-Hant": "總資產", "zh-Hans": "总资产", "en": "Total assets"},
    "total_liabilities": {"zh-Hant": "總負債", "zh-Hans": "总负债", "en": "Total liabilities"},
    "total_equity": {"zh-Hant": "權益合計", "zh-Hans": "权益合计", "en": "Total equity"},
    "cash": {"zh-Hant": "現金及等價物", "zh-Hans": "现金及等价物", "en": "Cash & equivalents"},
    "debt_noncurrent": {"zh-Hant": "長期債務", "zh-Hans": "长期债务", "en": "Long-term debt"},
    "gross_margin": {"zh-Hant": "毛利率", "zh-Hans": "毛利率", "en": "Gross margin"},
    "operating_margin": {"zh-Hant": "營業利潤率", "zh-Hans": "营业利润率", "en": "Operating margin"},
    "net_margin": {"zh-Hant": "淨利率", "zh-Hans": "净利率", "en": "Net margin"},
    "roe": {"zh-Hant": "ROE（淨資產收益率）", "zh-Hans": "ROE（净资产收益率）", "en": "ROE"},
    "roic": {"zh-Hant": "ROIC（投入資本回報率）", "zh-Hans": "ROIC（投入资本回报率）", "en": "ROIC"},
    "fcf_margin": {"zh-Hant": "自由現金流利潤率", "zh-Hans": "自由现金流利润率", "en": "FCF margin"},
}


def _label(code: str, lang: str) -> str:
    entry = _ITEM_LABELS.get(code)
    if not entry:
        return code
    return entry.get(lang, entry["zh-Hant"])


def _fmt_num(val: Any, unit: str, ccy: str, lang: str) -> str:
    try:
        n = float(val)
    except (TypeError, ValueError):
        return f"{val} {ccy}".strip()
    if unit == "per_share":
        suffix = {"zh-Hant": f" {ccy}/股", "zh-Hans": f" {ccy}/股", "en": f" {ccy}/sh"}.get(lang, f" {ccy}/sh")
        return f"{n:,.2f}{suffix}".strip()
    if unit == "shares":
        if abs(n) >= 1e8 and lang != "en":
            u = "億股" if lang == "zh-Hant" else "亿股"
            return f"{n / 1e8:,.2f} {u}"
        if abs(n) >= 1e6:
            return f"{n / 1e6:,.2f}M"
        return f"{n:,.0f}"
    # Money formatting
    if lang in ("zh-Hant", "zh-Hans"):
        yi = "億" if lang == "zh-Hant" else "亿"
        wan = "百萬" if lang == "zh-Hant" else "百万"
        if abs(n) >= 1e8:
            return f"{n / 1e8:,.2f} {yi} {ccy}".strip()
        if abs(n) >= 1e6:
            return f"{n / 1e6:,.2f} {wan} {ccy}".strip()
        return f"{n:,.0f} {ccy}".strip()
    if abs(n) >= 1e9:
        return f"{n / 1e9:,.2f}B {ccy}".strip()
    if abs(n) >= 1e6:
        return f"{n / 1e6:,.2f}M {ccy}".strip()
    return f"{n:,.0f} {ccy}".strip()


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

        # 3. Recent SEC 10-K / 10-Q / 8-K primary HTML filings (US filers only)
        contact = statements.data_sources(db).get("sec_contact")
        if include_sec and contact and not str(filer.cik).startswith("HK"):
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


# ------------------------------------------------------------------ retrieval & rich structured tool

def retrieve_chunks(db: Session, ticker: str, query: str, *, top_k: int = 10, max_tokens: int = 6500):
    """Hybrid PostgreSQL FTS + bilingual BM25 + dense vector cosine ranking over a stock's document chunks."""
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


def extract_numeric_facts(db: Session, ticker: str, question: str, *, lang: str = "zh-Hant") -> list[dict[str, Any]]:
    """Structured DB tool (D13): read exact profile, statements, DuPont, valuation methods, scenarios, risks & news."""
    q_norm = normalize_cjk(question)
    overview = is_overview_query(question)

    wanted_codes: list[str] = []
    for pat, codes in NUMERIC_KEYWORDS:
        if pat.search(question):
            for c in codes:
                if c not in wanted_codes:
                    wanted_codes.append(c)
    if overview and not wanted_codes:
        wanted_codes = ["revenue", "net_income", "free_cash_flow"]

    wants_val = overview or bool(VALUATION_INTENT_RE.search(question))
    wants_profile = overview or bool(PROFILE_INTENT_RE.search(question))
    wants_analytics = overview or bool(ANALYTICS_INTENT_RE.search(question))
    wants_risks = overview or bool(RISK_INTENT_RE.search(question))
    wants_news = overview or bool(RECENT_EVENT_RE.search(question))

    facts: list[dict[str, Any]] = []
    is_hk = ticker.upper().endswith(".HK")
    filer: Optional[Filer] = statements.filer_for(db, ticker)

    # 1. Company profile & latest market price from DB
    if wants_profile:
        sec = db.get(Security, ticker)
        bar = db.scalar(select(PriceBar).where(PriceBar.ticker == ticker).order_by(PriceBar.date.desc()).limit(1))
        if sec is not None or filer is not None or bar is not None:
            name = (filer.name if filer and filer.name else None) or (sec.name if sec and sec.name else ticker)
            parts = [f"{name} ({ticker})"]
            if sec and sec.exchange:
                parts.append(f"{'交易所' if lang != 'en' else 'Exchange'}: {sec.exchange}")
            if filer and filer.sic_description:
                parts.append(f"{'行業' if lang == 'zh-Hant' else '行业' if lang == 'zh-Hans' else 'Sector'}: {filer.sic_description}")
            if bar is not None:
                ccy = (sec.currency if sec else None) or ("HKD" if is_hk else "USD")
                p_lbl = "最新收盤價" if lang == "zh-Hant" else "最新收盘价" if lang == "zh-Hans" else "Last close"
                parts.append(f"{p_lbl}: {bar.close:,.2f} {ccy} ({bar.date.isoformat()})")
            facts.append({
                "category": "profile",
                "title": f"{ticker} Company Profile",
                "period": bar.date.isoformat() if bar else "",
                "form": "PROFILE",
                "filed": bar.date.isoformat() if bar else None,
                "url": f"/stock/{ticker}",
                "summary": " · ".join(parts),
            })

    # 2. Standardized statements, ratios, DuPont & earnings quality
    std: Optional[dict[str, Any]] = None
    if (wanted_codes or wants_analytics) and filer is not None and filer.synced_at is not None:
        try:
            std = statements.standard_statements(db, filer, view="latest", years=5, ticker=ticker, period="annual")
        except Exception as exc:
            log.debug("standard_statements skipped for %s: %s", ticker, type(exc).__name__)
            std = None

    if std and wanted_codes:
        years = [y["label"] for y in std.get("years") or []]
        ccy = std.get("currency") or ("HKD" if is_hk else "USD")
        row_map = {r["code"]: r for r in std.get("rows") or []}
        default_form = "HKEX-AR" if is_hk else "10-K"
        for code in wanted_codes[:6]:
            row = row_map.get(code)
            if not row:
                continue
            unit = row.get("unit") or "money"
            vals = []
            first_url, first_filed, first_form = None, None, None
            for y, cell in zip(years, row["cells"]):
                if cell and cell.get("value") is not None:
                    vals.append(f"{y}: {_fmt_num(cell['value'], unit, ccy, lang)}")
                    if first_url is None:
                        first_url = cell.get("document_url") or cell.get("index_url")
                        first_filed = cell.get("filed")
                        first_form = cell.get("form")
            if vals:
                facts.append({
                    "category": "statements",
                    "title": f"{ticker} Standardized Statements ({std.get('ruleset_version')})",
                    "period": ", ".join(years[:5]),
                    "form": first_form or default_form,
                    "filed": first_filed,
                    "url": first_url or f"/stock/{ticker}/statements",
                    "summary": f"{_label(code, lang)} — " + "；".join(vals[:5]) if lang != "en" else f"{_label(code, lang)} — " + "; ".join(vals[:5]),
                })
        # Key ratios
        if overview or re.search(r"roe|roa|roic|margin|毛利率|淨利率|净利率|比率|ratio|cagr|增長|增长|利潤率|利润率|財務|财务|基本面", q_norm, re.I):
            for rr in (std.get("ratios") or {}).get("rows") or []:
                if rr["code"] in ("gross_margin", "operating_margin", "net_margin", "roe", "roic", "fcf_margin"):
                    rvals = [f"{y}: {float(c['value']) * 100:.1f}%" for y, c in zip(years, rr["cells"]) if c and c.get("value") is not None]
                    if rvals:
                        sep = "；" if lang != "en" else "; "
                        facts.append({
                            "category": "statements",
                            "title": f"{ticker} Standardized Ratios",
                            "period": ", ".join(years[:5]),
                            "form": "RATIO",
                            "filed": None,
                            "url": f"/stock/{ticker}/statements",
                            "summary": f"{_label(rr['code'], lang)} — " + sep.join(rvals[:4]),
                        })

    if wants_analytics and filer is not None and filer.synced_at is not None:
        try:
            ana = statements.statement_analytics(db, filer, ticker=ticker, years=5)
            dup = (ana.get("dupont") or [])[:1]
            eq = (ana.get("earnings_quality") or [])[:1]
            if dup:
                d0 = dup[0]
                parts = [f"{d0['period']}"]
                if d0.get("roe") is not None:
                    parts.append(f"ROE {d0['roe'] * 100:.1f}%")
                if d0.get("net_margin") is not None:
                    parts.append(f"{_label('net_margin', lang)} {d0['net_margin'] * 100:.1f}%")
                if d0.get("asset_turnover") is not None:
                    parts.append(f"{'資產周轉率' if lang == 'zh-Hant' else '资产周转率' if lang == 'zh-Hans' else 'Asset turnover'} {d0['asset_turnover']:.2f}×")
                if d0.get("equity_multiplier") is not None:
                    parts.append(f"{'權益乘數' if lang == 'zh-Hant' else '权益乘数' if lang == 'zh-Hans' else 'Equity mult'} {d0['equity_multiplier']:.2f}×")
                facts.append({
                    "category": "analytics",
                    "title": f"{ticker} DuPont Analysis",
                    "period": str(d0.get("period") or ""),
                    "form": "ANALYTICS",
                    "filed": None,
                    "url": f"/stock/{ticker}/statements",
                    "summary": " · ".join(parts),
                })
            if eq:
                e0 = eq[0]
                eparts = [f"{e0['period']}"]
                if e0.get("cfo_to_net_income") is not None:
                    eparts.append(f"CFO/NI {e0['cfo_to_net_income']:.2f}×")
                if e0.get("fcf_to_net_income") is not None:
                    eparts.append(f"FCF/NI {e0['fcf_to_net_income']:.2f}×")
                if e0.get("sloan_accrual_ratio") is not None:
                    eparts.append(f"Sloan {e0['sloan_accrual_ratio'] * 100:.1f}%")
                if e0.get("quality_flag"):
                    eparts.append(f"{'品質評級' if lang == 'zh-Hant' else '质量评级' if lang == 'zh-Hans' else 'Quality'}: {e0['quality_flag']}")
                facts.append({
                    "category": "analytics",
                    "title": f"{ticker} Earnings Quality",
                    "period": str(e0.get("period") or ""),
                    "form": "ANALYTICS",
                    "filed": None,
                    "url": f"/stock/{ticker}/statements",
                    "summary": " · ".join(eparts),
                })
        except Exception as exc:
            log.debug("statement_analytics skipped for %s: %s", ticker, type(exc).__name__)

    # 3. Latest valuation run (5 scenarios, WACC, terminal g, AI analysis) + Football Field methods
    latest_run: Optional[Run] = None
    if wants_val or wants_risks:
        latest_run = db.scalar(select(Run).where(Run.ticker == ticker, Run.status == "done").order_by(Run.run_at.desc()).limit(1))

    calc_data: Optional[dict[str, Any]] = None
    if latest_run is not None and latest_run.calc_json_path and Path(latest_run.calc_json_path).is_file():
        try:
            calc_data = json.loads(Path(latest_run.calc_json_path).read_text(encoding="utf-8"))
        except Exception:
            calc_data = None

    if wants_val and latest_run is not None:
        ccy = latest_run.trading_ccy or ("HKD" if is_hk else "USD")
        scen_map = {m.scenario: m.value for m in (latest_run.methods or []) if m.method == "dcf" and m.value is not None}
        upside = ""
        if latest_run.composite_value and latest_run.price and latest_run.price > 0:
            pct = (latest_run.composite_value / latest_run.price - 1.0) * 100.0
            upside = f" ({pct:+.1f}%)"
        wacc_str = f"{latest_run.wacc * 100:.2f}%" if latest_run.wacc is not None else "—"
        g_str = f"{latest_run.terminal_g * 100:.2f}%" if latest_run.terminal_g is not None else "—"
        scen_str = ""
        if scen_map:
            ordered = [f"{k} {scen_map[k]:,.2f}" for k in ("bear", "base", "bull", "static", "floor9") if k in scen_map]
            if ordered:
                scen_str = " | " + ", ".join(ordered)
        if lang == "en":
            val_summary = (
                f"Latest DCF valuation ({latest_run.valuation_date}): composite fair value {latest_run.composite_value:,.2f} {ccy}{upside} "
                f"vs market price {latest_run.price:,.2f} ({latest_run.price_date}); WACC {wacc_str}, terminal g {g_str}{scen_str}."
                if latest_run.composite_value is not None and latest_run.price is not None
                else f"Latest valuation ({latest_run.valuation_date}): composite {latest_run.composite_value} {ccy}, WACC {wacc_str}, terminal g {g_str}{scen_str}."
            )
        else:
            val_summary = (
                f"最新估值（{latest_run.valuation_date}）：綜合理論價 {latest_run.composite_value:,.2f} {ccy}{upside}，"
                f"市價 {latest_run.price:,.2f} {ccy}（{latest_run.price_date or ''}）；WACC {wacc_str}、永續增長率 {g_str}{scen_str}。"
                if latest_run.composite_value is not None and latest_run.price is not None
                else f"最新估值（{latest_run.valuation_date}）：綜合理論價 {latest_run.composite_value} {ccy}；WACC {wacc_str}、永續增長率 {g_str}{scen_str}。"
            )
        facts.append({
            "category": "valuation",
            "title": f"{ticker} Latest Valuation ({latest_run.valuation_date})",
            "period": latest_run.valuation_date,
            "form": "VALUATION",
            "filed": latest_run.valuation_date,
            "url": f"/api/v1/runs/{latest_run.id}/report",
            "summary": val_summary,
        })

        # Multi-method Football Field comparison (DDM / RI / P/B-ROE / Relative)
        try:
            mm = valuation_methods.compute_stock_methods(db, ticker)
            avail = [m for m in (mm.get("methods") or []) if m.get("applicable") and m.get("fair_value") is not None]
            if avail:
                m_parts = [f"{m['id']}: {float(m['fair_value']):,.2f} {mm.get('currency') or ccy}" for m in avail[:5]]
                facts.append({
                    "category": "valuation",
                    "title": f"{ticker} Multi-Method Football Field",
                    "period": latest_run.valuation_date,
                    "form": "METHODS",
                    "filed": latest_run.valuation_date,
                    "url": f"/stock/{ticker}/valuation",
                    "summary": ("多方法估值對比：" if lang == "zh-Hant" else "多方法估值对比：" if lang == "zh-Hans" else "Multi-method fair values: ") + "；".join(m_parts),
                })
        except Exception as exc:
            log.debug("compute_stock_methods skipped for %s: %s", ticker, type(exc).__name__)

        # T7 executive synthesis from calc ai_analysis if present
        ai_ana = (calc_data or {}).get("ai_analysis") or {}
        t7 = ai_ana.get("T7") or {}
        if isinstance(t7, dict) and t7.get("executive_summary"):
            facts.append({
                "category": "valuation",
                "title": f"{ticker} Valuation Synthesis (T7)",
                "period": latest_run.valuation_date,
                "form": "AI-T7",
                "filed": latest_run.valuation_date,
                "url": f"/stock/{ticker}/valuation",
                "summary": str(t7["executive_summary"])[:320],
            })

    # 4. Risks, data gaps & T6 Red-Team findings
    if wants_risks:
        ai_ana = (calc_data or {}).get("ai_analysis") or {}
        t6 = ai_ana.get("T6") or {}
        if isinstance(t6, dict) and (t6.get("findings") or t6.get("summary")):
            f_texts = []
            for item in (t6.get("findings") or [])[:3]:
                if isinstance(item, dict):
                    f_texts.append(f"{item.get('title') or ''}: {item.get('detail') or ''}".strip(": "))
            summary_t6 = "；".join(f_texts) if f_texts else str(t6.get("summary") or "")
            if summary_t6:
                facts.append({
                    "category": "risks",
                    "title": f"{ticker} Red-Team Review (T6)",
                    "period": latest_run.valuation_date if latest_run else "",
                    "form": "AI-T6",
                    "filed": latest_run.valuation_date if latest_run else None,
                    "url": f"/stock/{ticker}/valuation",
                    "summary": summary_t6[:360],
                })
        elif latest_run and latest_run.data_gaps:
            from src.pipeline.gaps import text as gap_text
            g_lines = [gap_text(g, lang) for g in (latest_run.data_gaps or [])[:3]]
            g_lines = [x for x in g_lines if x]
            if g_lines:
                facts.append({
                    "category": "risks",
                    "title": f"{ticker} Valuation Data Gaps",
                    "period": latest_run.valuation_date,
                    "form": "GAPS",
                    "filed": latest_run.valuation_date,
                    "url": f"/stock/{ticker}/overview",
                    "summary": "；".join(g_lines)[:360],
                })

    # 5. Stored AI news / catalyst / anomaly suggestions
    if wants_news or wants_risks:
        wanted_keys = []
        if wants_news:
            wanted_keys.append("T2")
        if wants_risks:
            wanted_keys.append("T1")
        sugs = db.scalars(
            select(AiSuggestion)
            .where(AiSuggestion.ticker == ticker, AiSuggestion.task_key.in_(wanted_keys))
            .order_by(AiSuggestion.created_at.desc())
            .limit(4)
        ).all()
        for s in sugs:
            facts.append({
                "category": "news" if s.task_key == "T2" else "risks",
                "title": s.source_title or s.title,
                "period": s.source_date or "",
                "form": f"AI-{s.task_key}",
                "filed": s.source_date,
                "url": s.source_url or f"/stock/{ticker}/news",
                "summary": f"{s.title} — {s.detail}"[:300],
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


def _contextualize_query(question: str, recent_turns: list[dict[str, str]]) -> str:
    """Enrich short pronoun follow-up questions with the prior user turn for better retrieval."""
    if not recent_turns:
        return question
    if len(question) <= 24 or FOLLOWUP_RE.search(question):
        prev_users = [t["content"] for t in recent_turns if t.get("role") == "user" and t.get("content")]
        if prev_users:
            return f"{prev_users[-1][:140]} {question}"
    return question


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
    """Answer a question about ``ticker`` grounded in library + structured DB tool (+ optional web fallback)."""
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

    # 0. Zero-network auto-sync if the stock's library is empty (indexes existing valuations & statements)
    doc_count = int(db.scalar(select(func.count()).select_from(StockDocument).where(StockDocument.ticker == ticker)) or 0)
    if doc_count == 0:
        try:
            synced = sync_stock_library(db, ticker, include_sec=False)
            doc_count = int(synced.get("total_documents") or 0)
        except Exception as exc:
            log.debug("auto local sync skipped for %s: %s", ticker, type(exc).__name__)

    ai_configured = bool(settings_store.public_ai_settings(db).get("configured"))
    retrieval_query = _contextualize_query(q, recent_turns)

    # 1. Structured DB tool (profile, standardized statements, DuPont, valuation methods, risks, news)
    numeric_facts = extract_numeric_facts(db, ticker, q, lang=lang)

    # 2. Library-first bilingual hybrid search
    ranked = retrieve_chunks(db, ticker, retrieval_query, top_k=10, max_tokens=6500)
    sufficient = evidence_sufficient(ranked, retrieval_query)

    # 3. Optional web fallback ONLY when library evidence is insufficient or asking about latest post-filing events
    used_web = False
    if allow_web and (not sufficient or RECENT_EVENT_RE.search(q)):
        if search_and_ingest_web(db, ticker, q) > 0:
            doc_count = int(db.scalar(select(func.count()).select_from(StockDocument).where(StockDocument.ticker == ticker)) or 0)
            ranked = retrieve_chunks(db, ticker, retrieval_query, top_k=10, max_tokens=6500)
            sufficient = evidence_sufficient(ranked, retrieval_query)

    top_score = ranked[0].score if ranked else 0.0
    used_web = any(rc.chunk.source_kind == "web" and rc.score >= 0.18 for rc in ranked)

    # When AI is configured, allow moderately-scored library chunks (>= 0.14) to reach the LLM
    # so semantic comprehension can evaluate indirect phrasing (off-topic queries still score ~0.0).
    llm_can_inspect = ai_configured and bool(ranked) and top_score >= 0.14

    # 4. Generate grounded answer (refuse strictly with actionable hints if no evidence)
    model_used: Optional[str] = None
    insufficient = False
    if not sufficient and not numeric_facts and not llm_can_inspect:
        answer_text = refusal_with_hints(lang, ai_configured=ai_configured, doc_count=doc_count, allow_web=allow_web)
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

        effective_ai = ai_configured or (model_used is not None)
        if llm_text:
            answer_text, verified_cits = verify_citations(llm_text, cmap)
            if refusal_text(lang) in answer_text and not verified_cits:
                insufficient = True
                answer_text = refusal_with_hints(lang, ai_configured=effective_ai, doc_count=doc_count, allow_web=allow_web)
            elif not verified_cits:
                # Never return uncited LLM claims: fall back to verified extractive citations
                answer_text, verified_cits, insufficient = extractive_answer(
                    q, ranked, numeric_facts=numeric_facts, lang=lang,
                    ai_configured=effective_ai, doc_count=doc_count, allow_web=allow_web,
                )
        else:
            answer_text, verified_cits, insufficient = extractive_answer(
                q, ranked, numeric_facts=numeric_facts, lang=lang,
                ai_configured=effective_ai, doc_count=doc_count, allow_web=allow_web,
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

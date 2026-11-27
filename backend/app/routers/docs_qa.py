"""Document library & citation-grounded Q&A endpoints (D13, alpha.5)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Principal, current_principal, require_admin, require_user
from ..db import get_db
from ..i18n import bad_request, http_error, lang_dep
from ..models import DocumentChunk, QaMessage, QaThread, StockDocument, User
from ..schemas import (
    DocSyncOut,
    QaAskIn,
    QaAskOut,
    QaThreadOut,
    QaThreadSummary,
    StockDocumentDetail,
    StockDocumentOut,
)
from ..services import doc_library
from .common import iso, ticker_or_400

router = APIRouter(tags=["documents"])


def _doc_out(d: StockDocument) -> dict:
    return {
        "id": d.id, "ticker": d.ticker, "source_kind": d.source_kind, "title": d.title,
        "doc_date": d.doc_date, "form": d.form, "accession": d.accession, "url": d.url,
        "summary": d.summary, "chunk_count": d.chunk_count, "status": d.status,
        "created_at": iso(d.created_at),
    }


def _msg_out(m: QaMessage) -> dict:
    return {
        "id": m.id, "role": m.role, "content": m.content, "citations": m.citations or [],
        "used_web": bool(m.used_web), "used_statements": bool(m.used_statements),
        "insufficient_evidence": bool(m.insufficient_evidence), "model": m.model,
        "created_at": iso(m.created_at),
    }


def _thread_summary(t: QaThread) -> dict:
    return {
        "id": t.id, "ticker": t.ticker, "title": t.title, "summary": t.summary,
        "created_at": iso(t.created_at), "updated_at": iso(t.updated_at),
    }


@router.get("/stocks/{ticker}/documents", response_model=list[StockDocumentOut], summary="股票文件庫清單")
def list_documents(ticker: str, db: Session = Depends(get_db)):
    t = ticker_or_400(ticker)
    rows = db.scalars(select(StockDocument).where(StockDocument.ticker == t)
                      .order_by(StockDocument.doc_date.desc().nullslast(), StockDocument.id.desc())).all()
    return [_doc_out(d) for d in rows]


@router.post("/stocks/{ticker}/documents/sync", response_model=DocSyncOut, summary="同步估值報告、財報摘要與近期 SEC 申報正文入庫")
def sync_documents(ticker: str, include_sec: bool = Query(default=True),
                   max_filings: int = Query(default=3, ge=1, le=10),
                   db: Session = Depends(get_db)):
    t = ticker_or_400(ticker)
    return doc_library.sync_stock_library(db, t, include_sec=include_sec, max_filings=max_filings)


@router.post("/stocks/{ticker}/documents/upload", response_model=StockDocumentOut, status_code=201,
             summary="上傳 PDF／Markdown／HTML／純文字檔至該股票文件庫")
async def upload_document(
    ticker: str,
    file: UploadFile = File(...),
    title: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    lang: str = Depends(lang_dep),
):
    t = ticker_or_400(ticker)
    data = await file.read(doc_library.MAX_UPLOAD_BYTES + 1)
    try:
        doc, _ = doc_library.ingest_upload(db, t, file.filename or "upload.txt", data, title=title)
    except ValueError as exc:
        raise bad_request(exc, lang)
    return _doc_out(doc)


@router.get("/stocks/{ticker}/documents/{doc_id}", response_model=StockDocumentDetail, summary="單份文件的章節摘要與切段內容")
def get_document(ticker: str, doc_id: int, db: Session = Depends(get_db), lang: str = Depends(lang_dep)):
    t = ticker_or_400(ticker)
    d = db.get(StockDocument, doc_id)
    if d is None or d.ticker != t:
        raise http_error(404, "doc_not_found", lang)
    chunks = db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == d.id).order_by(DocumentChunk.seq)).all()
    return {
        **_doc_out(d),
        "section_summaries": d.section_summaries or [],
        "chunks": [
            {"id": c.id, "seq": c.seq, "section": c.section, "page": c.page, "content": c.content,
             "token_count": c.token_count, "url": c.url}
            for c in chunks
        ],
    }


@router.delete("/stocks/{ticker}/documents/{doc_id}", status_code=204, summary="從文件庫移除文件（管理員）")
def delete_document(ticker: str, doc_id: int, _: Principal = Depends(require_admin),
                    db: Session = Depends(get_db), lang: str = Depends(lang_dep)):
    t = ticker_or_400(ticker)
    d = db.get(StockDocument, doc_id)
    if d is None or d.ticker != t:
        raise http_error(404, "doc_not_found", lang)
    db.delete(d)


@router.get("/stocks/{ticker}/qa/threads", response_model=list[QaThreadSummary], summary="我在這隻股票的私人問答對話清單")
def list_threads(ticker: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    t = ticker_or_400(ticker)
    rows = db.scalars(select(QaThread).where(QaThread.ticker == t, QaThread.user_id == user.id)
                      .order_by(QaThread.updated_at.desc())).all()
    return [_thread_summary(r) for r in rows]


@router.get("/stocks/{ticker}/qa/threads/{thread_id}", response_model=QaThreadOut, summary="讀取私人對話內容與出處")
def get_thread(ticker: str, thread_id: str, user: User = Depends(require_user),
               db: Session = Depends(get_db), lang: str = Depends(lang_dep)):
    t = ticker_or_400(ticker)
    th = db.get(QaThread, thread_id)
    if th is None or th.ticker != t or th.user_id != user.id:
        raise http_error(404, "thread_not_found", lang)
    msgs = db.scalars(select(QaMessage).where(QaMessage.thread_id == th.id).order_by(QaMessage.id)).all()
    return {**_thread_summary(th), "messages": [_msg_out(m) for m in msgs]}


@router.delete("/stocks/{ticker}/qa/threads/{thread_id}", status_code=204, summary="刪除私人問答對話")
def delete_thread(ticker: str, thread_id: str, user: User = Depends(require_user),
                  db: Session = Depends(get_db), lang: str = Depends(lang_dep)):
    t = ticker_or_400(ticker)
    th = db.get(QaThread, thread_id)
    if th is None or th.ticker != t or th.user_id != user.id:
        raise http_error(404, "thread_not_found", lang)
    db.delete(th)


@router.post("/stocks/{ticker}/qa/ask", response_model=QaAskOut, summary="向這隻股票的文件庫提問（先文件庫與標準層數字，不足時可上網並標示來源）")
def ask(ticker: str, body: QaAskIn, p: Principal = Depends(current_principal),
        db: Session = Depends(get_db), lang: str = Depends(lang_dep)):
    if p.user is None:
        raise http_error(403, "user_required", lang)
    t = ticker_or_400(ticker)
    try:
        res = doc_library.ask_question(
            db, user_id=p.user.id, ticker=t, question=body.question,
            thread_id=body.thread_id, allow_web=body.allow_web, lang=lang,
        )
    except ValueError as exc:
        if getattr(exc, "code", None) == "thread_not_found":
            raise http_error(404, "thread_not_found", lang)
        raise bad_request(exc, lang)
    return {
        "thread_id": res["thread"].id,
        "message": _msg_out(res["message"]),
        "retrieved_chunks": res["retrieved_chunks"],
        "top_score": res["top_score"],
    }

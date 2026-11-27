"""Unified news feed (D4) and AI task suggestions (T1/T2 accept/dismiss, beta.2)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Principal, current_principal
from ..db import get_db
from ..i18n import http_error, lang_dep
from ..models import AiSuggestion
from ..schemas import (
    AiSuggestionDecisionIn,
    AiSuggestionGenerateIn,
    AiSuggestionGenerateOut,
    AiSuggestionOut,
    NewsItemOut,
)
from ..services import ai_tasks
from .common import iso, ticker_or_400

router = APIRouter(tags=["ai_tasks"])


def _sug_out(s: AiSuggestion) -> dict:
    return {
        "id": s.id, "ticker": s.ticker, "task_key": s.task_key, "title": s.title, "detail": s.detail,
        "payload": s.payload or {}, "source_id": s.source_id, "source_title": s.source_title,
        "source_url": s.source_url, "source_date": s.source_date, "status": s.status,
        "decided_by": s.decided_by, "decided_at": iso(s.decided_at) if s.decided_at else None,
        "created_at": iso(s.created_at),
    }


@router.get("/stocks/{ticker}/news", response_model=list[NewsItemOut],
            summary="統一新聞與公告介面（D4：SEC 申報、披露易與 Yahoo 標題，每則附可點擊原文連結）")
def stock_news(ticker: str, limit: int = Query(default=25, ge=1, le=50), db: Session = Depends(get_db)):
    t = ticker_or_400(ticker)
    return [n.to_dict() for n in ai_tasks.fetch_unified_news(db, t, limit=limit)]


@router.get("/stocks/{ticker}/suggestions", response_model=list[AiSuggestionOut],
            summary="該股票的 AI 建議清單（T1 財報檢查異常、T2 新聞與催化劑，含採用／忽略狀態與可點擊來源）")
def list_suggestions(ticker: str, db: Session = Depends(get_db)):
    t = ticker_or_400(ticker)
    rows = db.scalars(
        select(AiSuggestion).where(AiSuggestion.ticker == t).order_by(AiSuggestion.created_at.desc(), AiSuggestion.id.desc())
    ).all()
    return [_sug_out(r) for r in rows]


@router.post("/stocks/{ticker}/suggestions/generate", response_model=AiSuggestionGenerateOut,
             summary="執行 T1（以檢查層為輸入）與 T2（以統一新聞為輸入）產生建議並嚴格驗證來源引用")
def generate_suggestions(ticker: str, body: AiSuggestionGenerateIn, db: Session = Depends(get_db)):
    t = ticker_or_400(ticker)
    res = ai_tasks.generate_and_store_suggestions(db, t, tasks=list(body.tasks))
    return {
        "ticker": res["ticker"],
        "created": res["created"],
        "rejected_unverified": res["rejected_unverified"],
        "suggestions": [_sug_out(r) for r in res["rows"]],
    }


@router.put("/suggestions/{suggestion_id}", response_model=AiSuggestionOut,
            summary="採用（accepted）、忽略（dismissed）或重設（pending）一項 AI 建議")
def update_suggestion(
    suggestion_id: int,
    body: AiSuggestionDecisionIn,
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
    lang: str = Depends(lang_dep),
):
    actor = p.user.display_name or p.user.email if p.user else "token"
    row = ai_tasks.decide_suggestion(db, suggestion_id, body.status, actor)
    if row is None:
        raise http_error(404, "suggestion_not_found", lang)
    return _sug_out(row)

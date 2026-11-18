"""Accumulated market data: coverage, update status, manual sync (admin) and the revision log."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import scheduler
from ..auth import Principal, require_admin
from ..config import get_settings
from ..db import get_db
from ..i18n import http_error, lang_dep
from ..models import PriceBar, PriceRevision, Security, Setting
from ..schemas import MarketStatus, RevisionOut, SecurityOut, SyncResult
from ..services import market
from .common import iso, ticker_or_400

router = APIRouter(tags=["market"])


def security_out(s: Security) -> dict:
    return {"ticker": s.ticker, "name": s.name, "exchange": s.exchange, "currency": s.currency,
            "quote_type": s.quote_type, "tracked": s.tracked,
            "first_date": s.first_date.isoformat() if s.first_date else None,
            "last_date": s.last_date.isoformat() if s.last_date else None, "bars": s.bars or 0,
            "backfilled_at": iso(s.backfilled_at) if s.backfilled_at else None,
            "updated_at": iso(s.updated_at) if s.updated_at else None, "last_error": s.last_error, "checks": s.checks}


@router.get("/market/securities", response_model=list[SecurityOut], summary="系統累積的證券與匯率：覆蓋期間、筆數、檢查結果")
def securities(db: Session = Depends(get_db)) -> list[dict]:
    return [security_out(s) for s in db.scalars(select(Security).order_by(Security.ticker))]


@router.get("/market/status", response_model=MarketStatus, summary="每日更新排程與上次結果")
def status(db: Session = Depends(get_db)) -> dict:
    s = get_settings()
    mode = "off" if not scheduler.enabled() else ("worker" if s.redis_url else "inline")
    last = db.get(Setting, scheduler.STATUS_KEY)
    return {"scheduler": mode, "timezone": s.timezone, "times": [f"{h:02d}:{m:02d}" for h, m in scheduler.times()],
            "next_runs": [t.isoformat(timespec="minutes") for t in scheduler.next_runs()],
            "last_run": last.value if last else None,
            "securities": db.scalar(select(func.count()).select_from(Security)) or 0,
            "bars": db.scalar(select(func.count()).select_from(PriceBar)) or 0}


@router.get("/market/revisions", response_model=list[RevisionOut], summary="來源修正過的數字（舊值保存於此）")
def revisions(ticker: str | None = None, limit: int = Query(200, ge=1, le=2000), db: Session = Depends(get_db)) -> list[dict]:
    q = select(PriceRevision).order_by(PriceRevision.seen_at.desc()).limit(limit)
    if ticker:
        q = q.where(PriceRevision.ticker == ticker_or_400(ticker))
    return [{"ticker": r.ticker, "date": r.date.isoformat(), "field": r.field, "old": r.old, "new": r.new,
             "source": r.source, "seen_at": iso(r.seen_at)} for r in db.scalars(q)]


@router.post("/admin/market/sync/{ticker}", response_model=SyncResult, summary="立即更新一隻證券（full=true 重新回補全部歷史；管理員）")
def sync_one(ticker: str, full: bool = False, _: Principal = Depends(require_admin), db: Session = Depends(get_db),
             lang: str = Depends(lang_dep)) -> dict:
    t = ticker_or_400(ticker)
    try:
        return market.sync(db, t, full=full)
    except Exception as exc:
        db.commit()                     # keep last_error on the security
        raise http_error(502, "upstream", lang, detail=exc)


@router.post("/admin/market/check/{ticker}", summary="立即檢查一隻證券的完整性：按交易日曆找缺口並重抓；full=true 先重讀全部歷史（管理員）")
def check_one(ticker: str, full: bool = False, _: Principal = Depends(require_admin), db: Session = Depends(get_db),
              lang: str = Depends(lang_dep)) -> dict:
    from ..services import completeness

    t = ticker_or_400(ticker)
    if db.get(Security, t) is None:
        raise http_error(404, "not_found", lang, what=t)
    try:
        out = completeness.reread(db, t) if full else completeness.check(db, t)
        d = completeness.dividend_check(db, t)
        if d is not None:
            sec = db.get(Security, t)
            sec.checks = dict(sec.checks or {}, dividends=d)
        return out
    except Exception as exc:
        db.commit()
        raise http_error(502, "upstream", lang, detail=exc)


@router.post("/admin/market/update-all", status_code=202, summary="立即執行每日更新（背景；管理員）")
def update_all(_: Principal = Depends(require_admin)) -> dict:
    return {"started": scheduler.run_in_background()}

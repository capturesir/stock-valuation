"""Stocks: lookup, valuation history (trend chart), daily prices, Excel download."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..i18n import http_error, lang_dep
from ..models import Run, Security, WatchlistItem
from ..schemas import PriceSeries, StockProfile, StockValuations
from ..services import market
from .common import iso, run_out, ticker_or_400

router = APIRouter(tags=["stocks"])


@router.get("/stocks/{ticker}/lookup", summary="查詢公司與最新市價（不估值）")
def lookup(ticker: str, lang: str = Depends(lang_dep)) -> dict:
    from src.web import services as v1

    t = ticker_or_400(ticker)
    try:
        res = v1.lookup_ticker(t)
    except Exception as exc:
        raise http_error(502, "upstream", lang, detail=exc)
    if not res.get("ok"):
        raise http_error(404, "not_found", lang, what=t)
    return res


@router.get("/stocks/{ticker}/profile", response_model=StockProfile,
            responses={404: {"description": "找不到代號；detail.suggestions 為可能的代號"}},
            summary="公司基本資料（查詢時先顯示，讓用戶核實代號）")
def stock_profile(ticker: str, db: Session = Depends(get_db), lang: str = Depends(lang_dep)) -> dict:
    from fastapi import HTTPException

    from src.web.services import KNOWN_SPECS

    from ..i18n import msg
    from ..services import profile as prof

    t = ticker_or_400(ticker)
    valued = db.scalar(select(Run.id).where(Run.ticker == t, Run.status == "done").limit(1)) is not None
    try:
        return prof.profile(db, t, has_name_spec=t in KNOWN_SPECS, valued=valued)
    except prof.NotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "ticker_not_found", "error": msg("ticker_not_found", lang, ticker=t),
                                                     "suggestions": exc.suggestions})


@router.get("/stocks/{ticker}/valuations", response_model=StockValuations, summary="估值紀錄（走勢圖資料）")
def valuations(ticker: str, db: Session = Depends(get_db)) -> dict:
    t = ticker_or_400(ticker)
    runs = db.scalars(select(Run).where(Run.ticker == t).order_by(Run.run_at)).all()
    done = [r for r in runs if r.status == "done"]
    company = next((r.company for r in reversed(runs) if r.company), None)
    if not company:
        w = db.scalar(select(WatchlistItem).where(WatchlistItem.ticker == t))
        company = w.company if w else None
    return {
        "ticker": t, "company": company,
        "currency": next((r.trading_ccy for r in reversed(done) if r.trading_ccy), None),
        "runs": [run_out(r) for r in runs],
        "latest": run_out(done[-1]) if done else None,
        "previous": run_out(done[-2]) if len(done) > 1 else None,
    }


@router.get("/stocks/{ticker}/prices", response_model=PriceSeries,
            summary="每日價格（系統累積；首次使用時自動回補全部歷史）")
def prices(ticker: str, days: int = Query(365, ge=10, le=36500), db: Session = Depends(get_db)) -> dict:
    t = ticker_or_400(ticker)
    sec = db.get(Security, t)
    error = None
    stale = sec is None or sec.backfilled_at is None or sec.updated_at is None or \
        dt.datetime.now(dt.timezone.utc) - _aware(sec.updated_at) > dt.timedelta(hours=20)
    if stale:
        try:
            market.sync(db, t)
        except Exception as exc:   # keep serving what is stored; say why it is not fresh
            error = f"{type(exc).__name__}: {exc}"
        sec = db.get(Security, t)
    start = dt.date.today() - dt.timedelta(days=days)
    pts = market.series(db, t, start=start)
    return {"ticker": t, "currency": sec.currency if sec else None,
            "points": [{"date": p.date.isoformat(), "close": p.close, "adj_close": round(p.adj_close, 6)} for p in pts],
            "coverage": {"first_date": sec.first_date.isoformat() if sec and sec.first_date else None,
                         "last_date": sec.last_date.isoformat() if sec and sec.last_date else None,
                         "bars": sec.bars if sec else 0,
                         "updated_at": iso(sec.updated_at) if sec and sec.updated_at else None},
            "error": None if error is None else (error if not pts else f"未能更新，顯示已保存的數據：{error}")}


def _aware(ts: dt.datetime) -> dt.datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=dt.timezone.utc)


@router.get("/runs/{run_id}/excel", summary="下載該次估值的 Excel")
def excel(run_id: str, db: Session = Depends(get_db), lang: str = Depends(lang_dep)):
    r = db.get(Run, run_id)
    if r is None or not r.xlsx_path or not Path(r.xlsx_path).is_file():
        raise http_error(404, "no_excel", lang)
    return FileResponse(r.xlsx_path, filename=Path(r.xlsx_path).name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

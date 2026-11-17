"""Stocks: lookup, valuation history (trend chart), daily prices, Excel download."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..i18n import http_error, lang_dep
from ..models import Run, WatchlistItem
from ..schemas import PriceSeries, StockValuations
from .common import run_out, ticker_or_400

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


@router.get("/stocks/{ticker}/prices", response_model=PriceSeries, summary="每日收市價")
def prices(ticker: str, days: int = Query(365, ge=10, le=3650)) -> dict:
    from src.fetch import yahoo

    t = ticker_or_400(ticker)
    try:
        ch = yahoo.chart_quote(t, days=days)
    except Exception as exc:  # network down / blocked: the chart falls back to valuation-time prices
        return {"ticker": t, "currency": None, "points": [], "error": f"{type(exc).__name__}: {exc}"}
    if not ch.get("ok"):
        return {"ticker": t, "currency": None, "points": [], "error": ch.get("error") or "無法取得市價"}
    return {"ticker": t, "currency": (ch.get("meta") or {}).get("currency"),
            "points": [{"date": p["date"], "close": p["close"]} for p in ch.get("closes") or []]}


@router.get("/runs/{run_id}/excel", summary="下載該次估值的 Excel")
def excel(run_id: str, db: Session = Depends(get_db), lang: str = Depends(lang_dep)):
    r = db.get(Run, run_id)
    if r is None or not r.xlsx_path or not Path(r.xlsx_path).is_file():
        raise http_error(404, "no_excel", lang)
    return FileResponse(r.xlsx_path, filename=Path(r.xlsx_path).name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

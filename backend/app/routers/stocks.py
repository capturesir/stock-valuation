"""Stocks: lookup, valuation history (trend chart), daily prices, Excel & report download."""
from __future__ import annotations

import datetime as dt
import json
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from .. import limits
from ..auth import Principal, current_principal
from ..db import get_db
from ..i18n import DEFAULT as DEFAULT_LANG, http_error, lang_dep, pick
from ..models import Run, Security, WatchlistItem
from ..schemas import PriceSeries, StockMethodsOut, StockProfile, StockValuations
from ..services import branding, market, valuation_methods
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
def stock_profile(ticker: str, request: Request, p: Principal = Depends(current_principal),
                  db: Session = Depends(get_db), lang: str = Depends(lang_dep)) -> dict:
    from fastapi import HTTPException

    from src.web.services import KNOWN_SPECS

    from ..i18n import msg
    from ..services import profile as prof

    lim = limits.get(db)
    limits.hit("lookup", limits.who(request, p.user.id if p.user else None), lim["lookup_per_minute"], 60)
    t = ticker_or_400(ticker)
    valued = db.scalar(select(Run.id).where(Run.ticker == t, Run.status == "done").limit(1)) is not None
    try:
        return prof.profile(db, t, has_name_spec=t in KNOWN_SPECS, valued=valued)
    except prof.NotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "ticker_not_found", "error": msg("ticker_not_found", lang, ticker=t),
                                                     "suggestions": exc.suggestions})
    except prof.Unavailable as exc:
        secs = max(1, int(exc.retry_after + 0.999))
        raise HTTPException(status_code=503, headers={"Retry-After": str(secs)},
                            detail={"code": "upstream_unavailable", "retry_after": secs,
                                    "error": msg("upstream_unavailable", lang, reason=exc.reason, seconds=secs)})


@router.get("/stocks/{ticker}/valuations", response_model=StockValuations, summary="估值紀錄（走勢圖資料）")
def valuations(ticker: str, db: Session = Depends(get_db), lang: str = Depends(lang_dep)) -> dict:
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
        "runs": [run_out(r, lang) for r in runs],
        "latest": run_out(done[-1], lang) if done else None,
        "previous": run_out(done[-2], lang) if len(done) > 1 else None,
    }


@router.get("/stocks/{ticker}/prices", response_model=PriceSeries,
            summary="每日價格（系統累積；首次使用時自動回補全部歷史）")
def prices(ticker: str, request: Request, days: int = Query(365, ge=10, le=36500),
           p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    lim = limits.get(db)
    limits.hit("prices", limits.who(request, p.user.id if p.user else None), lim["prices_per_minute"], 60)
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


def _load_calc_for_export(r: Run) -> Optional[dict]:
    if not r.calc_json_path or not Path(r.calc_json_path).is_file():
        return None
    try:
        calc = json.loads(Path(r.calc_json_path).read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(calc, dict) and not calc.get("data_gap_codes") and r.data_gaps:
        calc["data_gap_codes"] = r.data_gaps
    return calc if isinstance(calc, dict) else None


@router.get("/runs/{run_id}/excel", summary="下載該次估值的 Excel（按語言產生並蓋品牌）")
def excel(run_id: str, lang_q: Optional[str] = Query(default=None, alias="lang"),
          db: Session = Depends(get_db), header_lang: str = Depends(lang_dep)):
    out_lang = pick(lang_q) if lang_q else header_lang
    r = db.get(Run, run_id)
    if r is None or not r.xlsx_path or not Path(r.xlsx_path).is_file():
        raise http_error(404, "no_excel", out_lang)
    filename = Path(r.xlsx_path).name
    if out_lang == DEFAULT_LANG:
        return FileResponse(r.xlsx_path, filename=filename,
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    calc = _load_calc_for_export(r)
    if calc is None:
        return FileResponse(r.xlsx_path, filename=filename,
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    from src.export.workbook import build_workbook

    tmp = Path(tempfile.mkdtemp(prefix="sv-val-xlsx-"))
    out = tmp / filename
    build_workbook(calc, str(out), lang=out_lang)
    branding.stamp_run_files(db, [str(out)])
    return FileResponse(out, filename=filename, background=BackgroundTask(shutil.rmtree, tmp, ignore_errors=True),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/runs/{run_id}/report", summary="下載該次估值的 Markdown 報告（按語言產生並蓋品牌）")
def report(run_id: str, lang_q: Optional[str] = Query(default=None, alias="lang"),
           db: Session = Depends(get_db), header_lang: str = Depends(lang_dep)):
    out_lang = pick(lang_q) if lang_q else header_lang
    r = db.get(Run, run_id)
    if r is None:
        raise http_error(404, "no_report", out_lang)
    calc = _load_calc_for_export(r)
    if calc is None:
        raise http_error(404, "no_report", out_lang)
    from src.export.report_md import write_report

    val_date = r.valuation_date or calc.get("valuation_date") or "valuation"
    filename = f"{val_date}_{r.ticker}_valuation.md"
    tmp = Path(tempfile.mkdtemp(prefix="sv-val-md-"))
    out = tmp / filename
    write_report(calc, str(out), lang=out_lang)
    branding.stamp_run_files(db, [str(out)])
    return FileResponse(out, filename=filename, background=BackgroundTask(shutil.rmtree, tmp, ignore_errors=True),
                        media_type="text/markdown; charset=utf-8")


@router.get("/stocks/{ticker}/methods", response_model=StockMethodsOut,
            summary="多方法估值（金融業 DDM／RI／P/B-ROE 與警示、反向 DCF、相對估值、綜合理論價與足球場圖）")
def stock_methods(
    ticker: str,
    w_dcf: Optional[float] = Query(default=None, ge=0, le=100),
    w_relative: Optional[float] = Query(default=None, ge=0, le=100),
    w_ri: Optional[float] = Query(default=None, ge=0, le=100),
    w_ddm: Optional[float] = Query(default=None, ge=0, le=100),
    w_pb_roe: Optional[float] = Query(default=None, ge=0, le=100),
    db: Session = Depends(get_db),
):
    t = ticker_or_400(ticker)
    raw_w = {"dcf": w_dcf, "relative": w_relative, "ri": w_ri, "ddm": w_ddm, "pb_roe": w_pb_roe}
    custom = {k: v for k, v in raw_w.items() if v is not None} or None
    return valuation_methods.compute_stock_methods(db, t, custom_weights=custom)

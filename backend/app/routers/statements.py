"""Statement data, layers ① raw and ② filed (alpha.3): SEC settings, sync, and the facts as filed."""
from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import limits
from ..auth import Principal, current_principal, require_admin
from ..db import get_db
from ..i18n import http_error, lang_dep
from ..models import FiledConceptRow, FiledFactRow, Filer, Filing
from ..schemas import (DataSourcesIn, DataSourcesOut, FiledConceptOut, FiledFactOut, FilerOut, StandardStatements,
                       StatementsStatus, StatementsSyncResult)
from ..services import statements
from .common import iso, ticker_or_400

router = APIRouter(tags=["statements"])


@router.get("/admin/data-sources", response_model=DataSourcesOut, summary="數據來源設定：SEC 聯絡資料（管理員）")
def get_sources(_: Principal = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    from ..models import Setting
    last = db.get(Setting, statements.STATUS_KEY)
    return {**statements.data_sources(db), "statements_last_run": last.value if last else None}


@router.put("/admin/data-sources", response_model=DataSourcesOut, summary="修改數據來源設定（管理員）")
def put_sources(body: DataSourcesIn, _: Principal = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    return statements.set_data_sources(db, body.sec_organisation, body.sec_email)


@router.post("/admin/statements/sync/{ticker}", response_model=StatementsSyncResult,
             summary="從 SEC 取得一隻美股的申報清單與全部 XBRL 數字（force=true 重新讀取舊頁並重新解析；管理員）")
def sync(ticker: str, force: bool = False, _: Principal = Depends(require_admin), db: Session = Depends(get_db),
         lang: str = Depends(lang_dep)) -> dict:
    t = ticker_or_400(ticker)
    try:
        return statements.sync_sec(db, t, force=force)
    except statements.Upstream as exc:
        db.commit()                     # keep the documents already saved and last_error on the filer
        raise http_error(502, "upstream", lang, detail=exc)


def _filer_or_404(db: Session, ticker: str, lang: str) -> Filer:
    f = statements.filer_for(db, ticker_or_400(ticker))
    if f is None or f.synced_at is None:
        raise http_error(404, "statements_not_synced", lang, ticker=ticker_or_400(ticker))
    return f


@router.get("/stocks/{ticker}/filer", response_model=FilerOut, summary="SEC 申報公司資料與已累積的申報數量")
def filer(ticker: str, db: Session = Depends(get_db), lang: str = Depends(lang_dep)) -> dict:
    f = _filer_or_404(db, ticker, lang)
    n = db.scalar(select(func.count()).select_from(Filing).where(Filing.cik == f.cik)) or 0
    return {"cik": f.cik, "name": f.name, "entity_type": f.entity_type, "sic": f.sic, "sic_description": f.sic_description,
            "fiscal_year_end": f.fiscal_year_end, "category": f.category, "tickers": f.tickers or [],
            "exchanges": f.exchanges or [], "filings": n, "facts": f.facts or 0,
            "synced_at": iso(f.synced_at) if f.synced_at else None, "last_error": f.last_error}


@router.get("/stocks/{ticker}/filed-concepts", response_model=list[FiledConceptOut],
            summary="申報原貌：公司申報過的全部科目，附筆數與期間")
def concepts(ticker: str, db: Session = Depends(get_db), lang: str = Depends(lang_dep)) -> list[dict]:
    f = _filer_or_404(db, ticker, lang)
    q = (select(FiledFactRow.taxonomy, FiledFactRow.concept, func.array_agg(func.distinct(FiledFactRow.unit)),
                func.count(), func.min(FiledFactRow.period_end), func.max(FiledFactRow.period_end), FiledConceptRow.label)
         .join(FiledConceptRow, (FiledConceptRow.taxonomy == FiledFactRow.taxonomy) & (FiledConceptRow.concept == FiledFactRow.concept),
               isouter=True)
         .where(FiledFactRow.cik == f.cik)
         .group_by(FiledFactRow.taxonomy, FiledFactRow.concept, FiledConceptRow.label)
         .order_by(FiledFactRow.taxonomy, FiledFactRow.concept))
    return [{"taxonomy": t, "concept": c, "label": label, "units": sorted(units), "facts": n,
             "first_end": first.isoformat(), "last_end": last.isoformat()}
            for t, c, units, n, first, last, label in db.execute(q)]


@router.get("/stocks/{ticker}/filed-facts", response_model=list[FiledFactOut],
            summary="申報原貌：一個科目的每個申報數字，附申報文件連結（重編時同一期間有多筆）")
def facts(ticker: str, concept: str = Query(..., max_length=200), taxonomy: str = Query("us-gaap", max_length=24),
          unit: str | None = Query(None, max_length=40), limit: int = Query(500, ge=1, le=5000),
          db: Session = Depends(get_db), lang: str = Depends(lang_dep)) -> list[dict]:
    f = _filer_or_404(db, ticker, lang)
    q = (select(FiledFactRow, Filing.primary_document)
         .join(Filing, Filing.accession == FiledFactRow.accession, isouter=True)
         .where(FiledFactRow.cik == f.cik, FiledFactRow.taxonomy == taxonomy, FiledFactRow.concept == concept)
         .order_by(FiledFactRow.period_end.desc(), FiledFactRow.period_start.desc().nulls_last(), FiledFactRow.filed.desc())
         .limit(limit))
    if unit:
        q = q.where(FiledFactRow.unit == unit)
    out = []
    for r, primary in db.execute(q):
        out.append({"taxonomy": r.taxonomy, "concept": r.concept, "unit": r.unit, "value": format(r.value, "f"),
                    "period_start": r.period_start.isoformat() if r.period_start else None,
                    "period_end": r.period_end.isoformat(), "fy": r.fy, "fp": r.fp, "form": r.form,
                    "accession": r.accession, "filed": r.filed.isoformat(), "frame": r.frame,
                    **statements.filing_urls(f.cik, r.accession, primary)})
    return out


@router.get("/stocks/{ticker}/statements", response_model=StandardStatements,
            summary="標準化財報：年度或季度（period），最前一欄為近十二個月（TTM）；每個數字附規則與申報出處（view=latest 含重編｜original 首次公布；as_of 只用當日或之前申報的數字）")
def standard(ticker: str, view: Literal["latest", "original"] = "latest", as_of: dt.date | None = None,
             period: Literal["annual", "quarterly"] = "annual", years: int = Query(10, ge=1, le=40),
             db: Session = Depends(get_db), lang: str = Depends(lang_dep)) -> dict:
    f = _filer_or_404(db, ticker, lang)
    t = ticker_or_400(ticker)
    return {"ticker": t, **statements.standard_statements(db, f, view=view, as_of=as_of, years=years, ticker=t, period=period)}


FRESH = dt.timedelta(hours=6)          # a fetch within this window is not repeated (the daily check keeps it current)


def _status(db: Session, t: str) -> dict:
    f = statements.filer_for(db, t)
    synced = f is not None and f.synced_at is not None
    return {"ticker": t, "sec_ready": statements.data_sources(db)["sec_ready"], "us_filer": statements.known_us_filer(db, t),
            "synced": synced, "synced_at": iso(f.synced_at) if synced else None, "pending": statements.is_pending(t),
            "facts": f.facts if f else 0, "last_error": (f.last_error if f else None) or statements.last_failure(t)}


@router.get("/stocks/{ticker}/statements/status", response_model=StatementsStatus,
            summary="財報數據狀態：是否已取得、是否正在取得、是否 SEC 申報公司")
def fetch_status(ticker: str, db: Session = Depends(get_db)) -> dict:
    return _status(db, ticker_or_400(ticker))


@router.post("/stocks/{ticker}/statements/fetch", response_model=StatementsStatus,
             responses={202: {"description": "已開始在背景取得"}, 200: {"description": "正在取得，或近 6 小時內已取得"}},
             summary="取得一隻美股的財報數據（任何已登入用戶；背景執行；近 6 小時內已取得則不重複）")
def fetch(ticker: str, request: Request, response: Response, db: Session = Depends(get_db),
          p: Principal = Depends(current_principal), lang: str = Depends(lang_dep)) -> dict:
    t = ticker_or_400(ticker)
    lim = limits.get(db)
    limits.hit("lookup", limits.who(request, p.user.id if p.user else None), lim["lookup_per_minute"], 60)
    if not statements.data_sources(db)["sec_ready"]:
        raise http_error(400, "sec_contact_missing", lang)
    if statements.known_us_filer(db, t) is False:
        raise http_error(400, "sec_not_filer", lang, ticker=t)
    st = _status(db, t)
    f = statements.filer_for(db, t)
    if st["pending"] or (f is not None and f.synced_at and dt.datetime.now(dt.timezone.utc) - f.synced_at < FRESH):
        return st
    if statements.submit(t):
        response.status_code = 202
    db.expire_all()
    return _status(db, t)


@router.get("/stocks/{ticker}/statements/export", response_class=FileResponse,
            responses={200: {"content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}, "text/csv": {}}}},
            summary="匯出標準化財報：Excel（財報、比率與增長、出處、檢查，附系統名稱）或 CSV；數值為精確原值")
def export(ticker: str, format: Literal["xlsx", "csv"] = "xlsx", view: Literal["latest", "original"] = "latest",
           period: Literal["annual", "quarterly"] = "annual", years: int = Query(10, ge=1, le=40),
           lang: str = Depends(lang_dep), db: Session = Depends(get_db)):
    import tempfile
    from pathlib import Path

    from ..services import branding, statements_export

    f = _filer_or_404(db, ticker, lang)
    t = ticker_or_400(ticker)
    data = {"ticker": t, **statements.standard_statements(db, f, view=view, years=years, ticker=t, period=period)}
    stem = f"{t}_{period}_{dt.date.today().isoformat()}"
    if format == "csv":
        return Response(statements_export.to_csv(data, lang), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{stem}.csv"'})
    import shutil

    from starlette.background import BackgroundTask

    tmp = Path(tempfile.mkdtemp(prefix="sv-export-"))
    out = tmp / f"{stem}.xlsx"
    statements_export.to_xlsx(data, lang, out)
    branding.stamp_run_files(db, [str(out)])
    return FileResponse(out, filename=out.name, background=BackgroundTask(shutil.rmtree, tmp, ignore_errors=True),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

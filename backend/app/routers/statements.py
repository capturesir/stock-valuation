"""Statement data, layers ① raw and ② filed (alpha.3): SEC settings, sync, and the facts as filed."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import Principal, require_admin
from ..db import get_db
from ..i18n import http_error, lang_dep
from ..models import FiledConceptRow, FiledFactRow, Filer, Filing
from ..schemas import DataSourcesIn, DataSourcesOut, FiledConceptOut, FiledFactOut, FilerOut, StatementsSyncResult
from ..services import statements
from .common import iso, ticker_or_400

router = APIRouter(tags=["statements"])


@router.get("/admin/data-sources", response_model=DataSourcesOut, summary="數據來源設定：SEC 聯絡資料（管理員）")
def get_sources(_: Principal = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    return statements.data_sources(db)


@router.put("/admin/data-sources", response_model=DataSourcesOut, summary="修改數據來源設定（管理員）")
def put_sources(body: DataSourcesIn, _: Principal = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    return statements.set_data_sources(db, body.sec_contact)


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

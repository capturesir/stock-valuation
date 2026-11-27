"""Statement data, layers ① raw and ② filed (alpha.3): SEC settings, sync, and the facts as filed."""
from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.sv_data.sources.hkex_free import is_hk_ticker

from .. import limits
from ..auth import Principal, current_principal, require_admin, require_user
from ..db import get_db
from ..i18n import AppError, http_error, lang_dep
from ..models import FiledConceptRow, FiledFactRow, Filer, Filing, InstanceFactRow
from ..schemas import (AdjustmentPlanIn, AdjustmentPlansOut, AdjustmentVersionOut, DataSourcesIn, DataSourcesOut, FiledConceptOut, FiledFactOut, FilerOut, HkexPdfSyncOut, PeerFinancialCompareOut,
                       StandardStatements, StatementAnalyticsOut, StatementsStatus, StatementsSyncResult)
from ..services import adjustments, statements
from .common import iso, ticker_or_400

router = APIRouter(tags=["statements"])
PlanChoice = Literal["auto", "central", "personal", "none"]   # which adjustment plan the statements are shown with


@router.get("/admin/data-sources", response_model=DataSourcesOut, summary="數據來源設定：SEC 聯絡資料（管理員）")
def get_sources(_: Principal = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    from ..models import Setting
    last = db.get(Setting, statements.STATUS_KEY)
    return {**statements.data_sources(db), "statements_last_run": last.value if last else None}


@router.put("/admin/data-sources", response_model=DataSourcesOut, summary="修改數據來源設定（管理員）")
def put_sources(body: DataSourcesIn, _: Principal = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    return statements.set_data_sources(db, body.sec_organisation, body.sec_email)


@router.post("/admin/statements/sync/{ticker}", response_model=StatementsSyncResult,
             summary="取得一隻美股（SEC）或港股（D11 Yahoo＋年報 PDF）的申報清單與財報數字（force=true 重新讀取；管理員）")
def sync(ticker: str, force: bool = False, _: Principal = Depends(require_admin), db: Session = Depends(get_db),
         lang: str = Depends(lang_dep)) -> dict:
    t = ticker_or_400(ticker)
    try:
        if is_hk_ticker(t):
            res = statements.sync_hkex(db, t, force=force)
            db.commit()
            return res
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
    out = [{"taxonomy": t, "concept": c, "label": label, "units": sorted(units), "facts": n,
            "first_end": first.isoformat(), "last_end": last.isoformat()}
           for t, c, units, n, first, last, label in db.execute(q)]
    # the filer's own concepts (not in companyfacts), from each annual report's XBRL instance
    I = InstanceFactRow
    q = (select(I.taxonomy, I.concept, func.array_agg(func.distinct(I.unit)), func.count(), func.min(I.period_end),
                func.max(I.period_end))
         .where(I.cik == f.cik, I.extension.is_(True), I.dims_key == "").group_by(I.taxonomy, I.concept)
         .order_by(I.taxonomy, I.concept))
    out += [{"taxonomy": t, "concept": c, "source": "instance", "label": None, "units": sorted(units), "facts": n,
             "first_end": first.isoformat(), "last_end": last.isoformat()}
            for t, c, units, n, first, last in db.execute(q)]
    return out


@router.get("/stocks/{ticker}/filed-facts", response_model=list[FiledFactOut],
            summary="申報原貌：一個科目的每個申報數字，附申報文件連結（重編時同一期間有多筆）")
def facts(ticker: str, concept: str = Query(..., max_length=200), taxonomy: str = Query("us-gaap", max_length=24),
          unit: str | None = Query(None, max_length=40), limit: int = Query(500, ge=1, le=5000),
          db: Session = Depends(get_db), lang: str = Depends(lang_dep)) -> list[dict]:
    f = _filer_or_404(db, ticker, lang)
    if taxonomy not in statements.STANDARD_TAXONOMIES:        # a filer's own concept: from the XBRL instances
        I = InstanceFactRow
        q = (select(I, Filing.primary_document).join(Filing, Filing.accession == I.accession, isouter=True)
             .where(I.cik == f.cik, I.taxonomy == taxonomy, I.concept == concept, I.dims_key == "")
             .order_by(I.period_end.desc(), I.period_start.desc().nulls_last(), I.filed.desc()).limit(limit))
        if unit:
            q = q.where(I.unit == unit)
        return [{"taxonomy": r.taxonomy, "concept": r.concept, "source": "instance", "unit": r.unit,
                 "value": format(r.value, "f"), "period_start": r.period_start.isoformat() if r.period_start else None,
                 "period_end": r.period_end.isoformat(), "fy": None, "fp": None, "form": r.form, "accession": r.accession,
                 "filed": r.filed.isoformat(), "frame": None, **statements.filing_urls(f.cik, r.accession, primary)}
                for r, primary in db.execute(q)]
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
             plan: PlanChoice = "auto", p: Principal = Depends(current_principal),
             db: Session = Depends(get_db), lang: str = Depends(lang_dep)) -> dict:
    f = _filer_or_404(db, ticker, lang)
    t = ticker_or_400(ticker)
    return {"ticker": t, **statements.standard_statements(db, f, view=view, as_of=as_of, years=years, ticker=t, period=period,
                                                          plan=_plan(db, t, p, plan))}


@router.get("/stocks/{ticker}/statements/analytics", response_model=StatementAnalyticsOut,
            summary="財報進階分析（§6）：3／5 因子杜邦分析與盈利質素（CFO／淨利、FCF 轉換、Sloan 應計比率）")
def statement_analytics_route(
    ticker: str,
    years: int = Query(10, ge=1, le=40),
    plan: PlanChoice = "auto",
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
    lang: str = Depends(lang_dep),
) -> dict:
    f = _filer_or_404(db, ticker, lang)
    t = ticker_or_400(ticker)
    return statements.statement_analytics(db, f, t, years=years, plan=_plan(db, t, p, plan))


@router.get("/statements/compare", response_model=PeerFinancialCompareOut,
            summary="多家公司財報並排比較（§6）：最多 10 隻已同步財報的股票，含毛利率、ROE、ROIC、盈利質素與同業中位數")
def peer_compare_route(
    tickers: str = Query(..., description="以逗號分隔的股票代號，例如 NVDA,AAPL,MSFT"),
    _: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict:
    parsed = [ticker_or_400(x) for x in tickers.split(",") if x.strip()]
    return statements.peer_financial_compare(db, parsed)


def _plan(db: Session, t: str, p: Principal, which: str) -> dict | None:
    """auto: my personal plan, else the central plan, else none (system default)."""
    if p.user is None:                          # service calls have no personal plan
        which = "central" if which in ("auto", "central") else "none"
        if which == "none":
            return None
        from ..models import User
        return adjustments.chosen(db, t, User(id="", display_name=""), "central")
    return adjustments.chosen(db, t, p.user, which)


FRESH = dt.timedelta(hours=6)          # a fetch within this window is not repeated (the daily check keeps it current)


def _status(db: Session, t: str) -> dict:
    f = statements.filer_for(db, t)
    synced = f is not None and f.synced_at is not None
    return {"ticker": t, "sec_ready": statements.data_sources(db)["sec_ready"], "us_filer": statements.known_us_filer(db, t),
            "hk_supported": is_hk_ticker(t),
            "synced": synced, "synced_at": iso(f.synced_at) if synced else None, "pending": statements.is_pending(t),
            "facts": f.facts if f else 0, "last_error": (f.last_error if f else None) or statements.last_failure(t)}


@router.get("/stocks/{ticker}/statements/status", response_model=StatementsStatus,
            summary="財報數據狀態：是否已取得、是否正在取得、是否 SEC 申報公司或港股（D11）")
def fetch_status(ticker: str, db: Session = Depends(get_db)) -> dict:
    return _status(db, ticker_or_400(ticker))


@router.post("/stocks/{ticker}/statements/fetch", response_model=StatementsStatus,
             responses={202: {"description": "已開始在背景取得"}, 200: {"description": "正在取得，或近 6 小時內已取得"}},
             summary="取得一隻美股（SEC）或港股（D11 免費來源）的財報數據（任何已登入用戶；背景執行；近 6 小時內已取得則不重複）")
def fetch(ticker: str, request: Request, response: Response, db: Session = Depends(get_db),
          p: Principal = Depends(current_principal), lang: str = Depends(lang_dep)) -> dict:
    t = ticker_or_400(ticker)
    lim = limits.get(db)
    limits.hit("lookup", limits.who(request, p.user.id if p.user else None), lim["lookup_per_minute"], 60)
    if not is_hk_ticker(t):
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


@router.post("/stocks/{ticker}/statements/hkex-pdf", response_model=HkexPdfSyncOut,
             summary="上傳港股年報 PDF 抽取 IFRS／HKFRS 財報數字並與 Yahoo 交叉核對（D11）")
async def upload_hkex_pdf(
    ticker: str,
    file: UploadFile = File(...),
    document_url: str | None = Form(None),
    _: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
    lang: str = Depends(lang_dep),
) -> dict:
    t = ticker_or_400(ticker)
    if not is_hk_ticker(t):
        raise http_error(400, "sec_not_filer", lang, ticker=t)
    raw = await file.read(20 * 1024 * 1024 + 1)
    if len(raw) > 20 * 1024 * 1024 or not raw:
        raise http_error(400, "doc_empty_text", lang)
    res = statements.sync_hkex(db, t, force=True, pdf_bytes=raw, document_url=(document_url or "").strip() or None)
    db.commit()
    return res


@router.get("/stocks/{ticker}/statements/export", response_class=FileResponse,
            responses={200: {"content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}, "text/csv": {}}}},
            summary="匯出標準化財報：Excel（財報、比率與增長、出處、檢查，附系統名稱）或 CSV；數值為精確原值")
def export(ticker: str, format: Literal["xlsx", "csv"] = "xlsx", view: Literal["latest", "original"] = "latest",
           period: Literal["annual", "quarterly"] = "annual", years: int = Query(10, ge=1, le=40),
           plan: PlanChoice = "auto", p: Principal = Depends(current_principal),
           lang: str = Depends(lang_dep), db: Session = Depends(get_db)):
    import tempfile
    from pathlib import Path

    from ..services import branding, statements_export

    f = _filer_or_404(db, ticker, lang)
    t = ticker_or_400(ticker)
    data = {"ticker": t, **statements.standard_statements(db, f, view=view, years=years, ticker=t, period=period,
                                                         plan=_plan(db, t, p, plan))}
    import re

    # the download name only: request input never becomes part of a filesystem path
    stem = f"{re.sub(r'[^A-Za-z0-9._-]', '_', t)}_{period}_{dt.date.today().isoformat()}"
    if format == "csv":
        return Response(statements_export.to_csv(data, lang), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{stem}.csv"'})
    import shutil

    from starlette.background import BackgroundTask

    tmp = Path(tempfile.mkdtemp(prefix="sv-export-"))
    out = tmp / "statements.xlsx"
    statements_export.to_xlsx(data, lang, out)
    branding.stamp_run_files(db, [str(out)])
    return FileResponse(out, filename=f"{stem}.xlsx", background=BackgroundTask(shutil.rmtree, tmp, ignore_errors=True),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ------------------------------------------------------------------ adjustment plans (D12)

@router.get("/stocks/{ticker}/adjustment-plans", response_model=AdjustmentPlansOut,
            summary="調整方案：中央方案與我的個人方案（目前版本的全部調整、版本清單），以及中央方案是否在我複製後更新")
def plans(ticker: str, user=Depends(require_user), db: Session = Depends(get_db)) -> dict:
    return adjustments.overview(db, ticker_or_400(ticker), user)


@router.get("/adjustment-plans/{plan_id}/versions/{version}", response_model=AdjustmentVersionOut,
            summary="調整方案的某一版本（全部調整）；中央方案所有人可看，個人方案只限本人")
def plan_version(plan_id: str, version: int, user=Depends(require_user), db: Session = Depends(get_db),
                 lang: str = Depends(lang_dep)) -> dict:
    from ..models import AdjustmentPlan

    plan = db.get(AdjustmentPlan, plan_id)
    if plan is None or (plan.kind == "personal" and plan.user_id != user.id):
        raise http_error(404, "plan_not_found", lang)
    ver = adjustments.version_row(db, plan, version)
    if ver is None:
        raise http_error(404, "plan_not_found", lang)
    return adjustments.version_out(db, plan, ver)


def _save(ticker: str, kind: str, body: AdjustmentPlanIn, user, db: Session, lang: str) -> dict:
    f = _filer_or_404(db, ticker, lang)
    t = ticker_or_400(ticker)
    try:
        adjustments.save(db, f, t, kind, user, [a.model_dump() for a in body.adjustments], body.note, body.base_version,
                         body.central_version if kind == "personal" else None)
    except AppError as exc:
        db.rollback()
        if exc.code == "plan_conflict":
            raise http_error(409, exc.code, lang, **exc.params)
        raise
    db.commit()
    return adjustments.overview(db, t, user)


@router.put("/stocks/{ticker}/adjustment-plans/central", response_model=AdjustmentPlansOut,
            summary="保存中央方案的新版本（管理員）：送出完整一套調整；base_version 不是目前版本時回 409")
def save_central(ticker: str, body: AdjustmentPlanIn, p: Principal = Depends(require_admin), db: Session = Depends(get_db),
                 lang: str = Depends(lang_dep)) -> dict:
    if p.user is None:
        raise http_error(403, "admin_required", lang)
    return _save(ticker, "central", body, p.user, db, lang)


@router.put("/stocks/{ticker}/adjustment-plans/personal", response_model=AdjustmentPlansOut,
            summary="保存我的個人方案的新版本：第一次保存即建立（記錄當時的中央方案版本）；central_version 記錄已合併的中央版本")
def save_personal(ticker: str, body: AdjustmentPlanIn, user=Depends(require_user), db: Session = Depends(get_db),
                  lang: str = Depends(lang_dep)) -> dict:
    return _save(ticker, "personal", body, user, db, lang)

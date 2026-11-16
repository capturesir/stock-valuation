"""Local web UI for the portable valuation system.

Start (from the package root, same as the CLI):

    python -m src.web.app
    python -m src.web
    uvicorn src.web.app:app --host 127.0.0.1 --port 8765

This module does not change the CLI.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..pipeline.orchestrator import check_filings
from ..signals.export_signals import (
    GENERATED_BY,
    ProductionPathError,
    SignalExportError,
    SignalValidationError,
    build_single_document,
    dumps_doc,
    export_signals,
    load_calc_file,
    summarize_signal,
)
from . import jobs as jobmod
from . import research
from . import services
from . import settings as settingsmod
from ..pipeline import adjustments as adjmod
from .sheets import EXPECTED_SHEETS, render_workbook

WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
# src/web/app.py -> src/web -> src -> project root; docs/ sits at the root.
DOCS_DIR = WEB_DIR.parents[1] / "docs"
# R01–R03: the page keeps the original seven tabs. The research sheets added for
# the Excel download are data for the in-page blocks, not extra tabs.
UI_SHEETS = list(EXPECTED_SHEETS)
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

app = FastAPI(title="可攜式股票估值系統", docs_url=None, redoc_url=None)


class NoCacheStatic(StaticFiles):
    """Serve static assets with revalidation.

    Without this, browsers keep a 304-cached app.js after a fix and the old
    JS keeps running, which looks like "the fix didn't work".
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


app.mount("/static", NoCacheStatic(directory=str(STATIC_DIR)), name="static")


@app.exception_handler(Exception)
async def _unhandled(request, exc: Exception):
    """Last-resort handler: turn any uncaught exception into JSON, not a bare 500.

    The browser otherwise only shows "Internal Server Error", which hides the
    real cause (upstream timeout, corrupt file, missing key) from the user and
    from whoever is debugging.
    """
    import logging
    import traceback

    logging.getLogger("uvicorn.error").error(
        "unhandled error on %s %s: %s\n%s",
        request.method, request.url.path, exc, traceback.format_exc(),
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": f"伺服器內部錯誤：{type(exc).__name__}: {exc}",
            "path": request.url.path,
            "hint": "詳細堆疊已寫入 out/web.log；若為上游資料來源問題請稍後重試。",
        },
    )


class ValuateBody(BaseModel):
    ticker: str = Field(..., min_length=1)
    date: str | None = None
    no_llm: bool = False


class CheckFilingsBody(BaseModel):
    ticker: str = Field(..., min_length=1)
    rerun_if_needed: bool = False
    date: str | None = None


class ExportSignalsBody(BaseModel):
    ticker: str = Field(..., min_length=1)
    date: str | None = None


def _http_error(status: int, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": message, **extra})


def _signals_from_rec(rec: dict) -> dict:
    """Build an in-memory single-ticker signals document from a valuation record."""
    js = rec.get("json")
    if not js:
        raise FileNotFoundError("找不到對應的估值 JSON。")
    path = Path(js)
    if not path.is_file():
        raise FileNotFoundError(f"找不到對應的估值 JSON（{path.name}）。")
    calc = load_calc_file(path)
    date = rec.get("valuation_date") or calc.get("valuation_date")
    if not date:
        raise ValueError("缺少估值日，無法組成 signal_id。")
    md = rec.get("md")
    if not md:
        folder = path.parent
        found = sorted(folder.glob("*_valuation.md"))
        if not found:
            raise FileNotFoundError("找不到對應的估值說明 Markdown。")
        md = str(found[-1])
    return build_single_document(calc, date, md)


def _signals_filename(doc: dict) -> str:
    sigs = doc.get("signals") or []
    if len(sigs) == 1:
        return f"{sigs[0]['signal_id']}_signals.json"
    date = doc.get("price_date") or "signals"
    return f"signals_{date}.json"




@app.get("/")
def index():
    # No-cache so a redeployed app.js/index.html is picked up on reload.
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "service": "valuation-web",
        "llm": services.llm_status(),
        "disclaimer": "本系統產出不構成投資建議。",
    }


@app.get("/api/lookup")
def api_lookup(ticker: str = Query(..., min_length=1)):
    try:
        result = services.lookup_ticker(ticker)
    except Exception as exc:
        # Never leak an unhandled exception as a bare 500: the UI can only show
        # "Internal Server Error", which tells the user nothing actionable.
        raise _http_error(
            502,
            f"查詢「{ticker}」時上游資料來源（Yahoo Finance）暫時無法連線：{exc}",
            ticker=ticker,
        )
    if not result.get("ok"):
        return JSONResponse(result, status_code=404)
    return result


@app.post("/api/valuate")
def api_valuate(body: ValuateBody):
    try:
        job = jobmod.store.start_valuation(
            body.ticker, valuation_date=body.date, use_llm=not body.no_llm,
        )
    except jobmod.JobBusy as exc:
        return JSONResponse(
            {"ok": False, "error": str(exc), "active_job": jobmod.store.public(exc.current)},
            status_code=409,
        )
    except ValueError as exc:
        raise _http_error(400, str(exc))
    return {"ok": True, "job": jobmod.store.public(job)}


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str):
    job = jobmod.store.get(job_id)
    if not job:
        raise _http_error(404, "找不到此工作。")
    return {"ok": True, "job": jobmod.store.public(job)}


@app.post("/api/jobs/{job_id}/cancel")
def api_job_cancel(job_id: str):
    job = jobmod.store.cancel(job_id)
    if not job:
        raise _http_error(404, "找不到此工作。")
    return {"ok": True, "job": jobmod.store.public(job)}


@app.get("/api/errors")
def api_errors():
    """Newest runs (status, failing step, message) for the 錯誤 panel.

    The panel shows three; the file keeps ten because the ETA estimate needs
    them. Both numbers are returned so the UI does not have to assume either.
    """
    runs = jobmod.load_history()
    return {"ok": True, "runs": runs[: jobmod.HISTORY_SHOWN],
            "shown": jobmod.HISTORY_SHOWN, "stored": jobmod.HISTORY_MAX,
            "eta": research.eta_estimate()}


@app.get("/api/jobs/{job_id}/sheets")
def api_job_sheets(job_id: str):
    job = jobmod.store.get(job_id)
    if not job:
        raise _http_error(404, "找不到此工作。")
    if job.get("status") != "done" or not (job.get("result") or {}).get("xlsx"):
        raise _http_error(409, "估值尚未完成，或沒有可顯示的 Excel。", job_status=job.get("status"))
    rec = job["result"]
    data = render_workbook(rec["xlsx"], rec.get("json"), include=UI_SHEETS)
    extra = research.research_payload(rec.get("ticker") or job.get("ticker") or "",
                                      valuation_date=rec.get("valuation_date"))
    return {"ok": True, "job_id": job_id, "meta": rec, "workbook": data,
            "research": extra, "usage": job.get("usage") or extra.get("usage") or {}}


@app.get("/api/jobs/{job_id}/download")
def api_job_download(job_id: str):
    job = jobmod.store.get(job_id)
    if not job:
        raise _http_error(404, "找不到此工作。")
    path = (job.get("result") or {}).get("xlsx")
    if not path or not Path(path).is_file():
        raise _http_error(404, "找不到對應的 Excel 檔。")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=Path(path).name,
    )


@app.get("/api/workbook")
def api_workbook(ticker: str = Query(..., min_length=1), date: str | None = None):
    try:
        rec = services.resolve_workbook(ticker, date)
        data = render_workbook(rec["xlsx"], rec.get("json"), include=UI_SHEETS)
    except FileNotFoundError as exc:
        raise _http_error(404, str(exc))
    except ValueError as exc:
        raise _http_error(400, str(exc))
    except PermissionError as exc:
        raise _http_error(403, str(exc))
    except Exception as exc:
        raise _http_error(422, f"無法讀取 {ticker} 的 Excel：{type(exc).__name__}: {exc}")
    extra = research.research_payload(rec.get("ticker") or ticker,
                                      valuation_date=rec.get("valuation_date"))
    return {"ok": True, "meta": rec, "workbook": data, "research": extra,
            "usage": extra.get("usage") or {}}


@app.get("/api/workbook/excel")
def api_workbook_excel(ticker: str = Query(..., min_length=1), date: str | None = None):
    try:
        rec = services.resolve_workbook(ticker, date)
    except FileNotFoundError as exc:
        raise _http_error(404, str(exc))
    except ValueError as exc:
        raise _http_error(400, str(exc))
    path = rec["xlsx"]
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=Path(path).name,
    )


# ---------------------------------------------------------------------------
# v1.1 research endpoints (R04–R21)
# ---------------------------------------------------------------------------

class AdjustmentBody(BaseModel):
    ticker: str = Field(..., min_length=1)
    statement: str = Field(..., min_length=1)
    item: str = Field(..., min_length=1)
    fy: str = Field(..., min_length=1)
    adjusted: float
    reason: str = ""


@app.get("/api/review")
def api_review(ticker: str = Query(..., min_length=1), period: str = "annual"):
    """Every collected statement line item, plus the adjustments already made."""
    ticker = services.norm_ticker(ticker)
    data = research.statement_rows(ticker, period=period)
    data["adjustments"] = adjmod.load_adjustments(ticker)
    data["adopted"] = adjmod.latest_adjustments(ticker)
    return data


@app.post("/api/review/adjustments")
def api_add_adjustment(body: AdjustmentBody):
    try:
        record = adjmod.add_adjustment(body.ticker, body.model_dump())
    except ValueError as exc:
        raise _http_error(400, str(exc))
    return {"ok": True, "adjustment": record,
            "adjustments": adjmod.load_adjustments(services.norm_ticker(body.ticker))}


@app.delete("/api/review/adjustments/{adj_id}")
def api_delete_adjustment(adj_id: str, ticker: str = Query(..., min_length=1)):
    if not adjmod.delete_adjustment(ticker, adj_id):
        raise _http_error(404, "找不到這筆修改紀錄。")
    return {"ok": True, "adjustments": adjmod.load_adjustments(services.norm_ticker(ticker))}


@app.get("/api/history")
def api_history(ticker: str = Query(..., min_length=1)):
    return research.valuation_history(ticker)


@app.get("/api/forecast")
def api_forecast(ticker: str = Query(..., min_length=1), date: str | None = None):
    data = research.forecast_block(ticker, research.load_calc(ticker, date))
    if not data.get("ok"):
        return JSONResponse(data, status_code=404)
    return data


@app.get("/api/ai-plan")
def api_ai_plan(ticker: str = Query(..., min_length=1), date: str | None = None,
                use_llm: bool = True):
    """What the AI steps would do for this ticker, before the run starts (R13)."""
    ticker_n = services.norm_ticker(ticker)
    cfg = services.load_config()
    client_status = services.llm_status()
    return research.ai_plan(ticker_n, date, use_llm=use_llm,
                            model=client_status.get("model"))


@app.get("/api/eta")
def api_eta(use_llm: bool | None = None, ticker: str | None = None):
    return research.eta_estimate(use_llm=use_llm, ticker=ticker)


@app.get("/settings")
def settings_page():
    page = STATIC_DIR / "settings.html"
    if not page.is_file():
        # FileResponse only fails when the response is sent, which surfaced as a
        # 500 plus a full traceback. A missing page is a 404.
        raise _http_error(404, "尚未提供 AI 設定頁（static/settings.html 不存在）。")
    return FileResponse(page, headers={"Cache-Control": "no-cache, must-revalidate"})


@app.get("/api/settings/llm")
def api_settings_llm():
    return settingsmod.current()


@app.post("/api/settings/llm")
def api_settings_llm_save(body: dict):
    try:
        return settingsmod.save(
            base_url=body.get("base_url") or "",
            model=body.get("model") or "",
            api_key=body.get("api_key"),
        )
    except ValueError as exc:
        raise _http_error(400, str(exc))


@app.post("/api/settings/llm/test")
def api_settings_llm_test():
    result = settingsmod.test_connection()
    return result


@app.get("/api/package.zip")
def api_package():
    """R24: the whole runnable project, minus local secrets and run output."""
    from .package import build_package

    try:
        path = build_package()
    except Exception as exc:
        raise _http_error(500, f"打包失敗：{type(exc).__name__}: {exc}")
    return FileResponse(path, media_type="application/zip", filename=Path(path).name)


@app.get("/api/guide.pdf")
def api_guide():
    """R25: the illustrated user guide that ships with this version.

    Only files this project produced are served. Falling back to "any PDF in
    docs/" handed out a third-party guide that documented a different version.
    """
    candidates = sorted(DOCS_DIR.glob("*使用指南*.pdf"))
    if not candidates:
        raise _http_error(404, "docs/ 內沒有本版使用指南 PDF（*使用指南*.pdf）。")
    return FileResponse(candidates[-1], media_type="application/pdf",
                        filename=candidates[-1].name)


@app.post("/api/check-filings")
def api_check_filings(body: CheckFilingsBody):
    ticker = services.norm_ticker(body.ticker)
    if not ticker:
        raise _http_error(400, "請輸入股票代號。")
    try:
        services.validate_date(body.date)
    except ValueError as exc:
        raise _http_error(400, str(exc))
    try:
        result = check_filings(ticker)
    except Exception as exc:
        payload = {"ticker": ticker, "error": str(exc), "rerun_needed": False, "blocked": True}
        return {
            "ok": False,
            "status": services.filings_status(payload),
            "result": payload,
        }
    status = services.filings_status(result)
    out = {"ok": True, "status": status, "result": result, "job": None}
    if body.rerun_if_needed and result.get("rerun_needed"):
        try:
            job = jobmod.store.start_valuation(
                ticker, valuation_date=body.date, use_llm=True,
            )
            out["job"] = jobmod.store.public(job)
            out["message"] = "檢查結果為需要重跑，已開始自動估值。"
        except jobmod.JobBusy as exc:
            return JSONResponse(
                {"ok": False, "status": status, "result": result,
                 "error": str(exc), "active_job": jobmod.store.public(exc.current)},
                status_code=409,
            )
    return out



@app.get("/api/jobs/{job_id}/signals")
def api_job_signals(job_id: str):
    job = jobmod.store.get(job_id)
    if not job:
        raise _http_error(404, "找不到此工作。")
    if job.get("status") != "done" or not (job.get("result") or {}).get("json"):
        raise _http_error(409, "估值尚未完成，或沒有可匯出的估值 JSON。", job_status=job.get("status"))
    rec = job["result"]
    try:
        doc = _signals_from_rec(rec)
    except (SignalExportError, FileNotFoundError, ValueError) as exc:
        raise _http_error(400, str(exc))
    name = _signals_filename(doc)
    return Response(
        content=dumps_doc(doc),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.get("/api/workbook/signals")
def api_workbook_signals(ticker: str = Query(..., min_length=1), date: str | None = None):
    try:
        rec = services.resolve_workbook(ticker, date)
    except FileNotFoundError as exc:
        raise _http_error(404, str(exc))
    except ValueError as exc:
        raise _http_error(400, str(exc))
    try:
        doc = _signals_from_rec(rec)
    except (SignalExportError, FileNotFoundError, ValueError) as exc:
        raise _http_error(400, str(exc))
    name = _signals_filename(doc)
    return Response(
        content=dumps_doc(doc),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.post("/api/export-signals")
def api_export_signals(body: ExportSignalsBody):
    """Explicit user action: merge one ticker into package out/signals.json."""
    ticker = services.norm_ticker(body.ticker)
    if not ticker:
        raise _http_error(400, "請輸入股票代號。")
    try:
        date = services.validate_date(body.date)
        rec = services.resolve_workbook(ticker, date)
    except FileNotFoundError as exc:
        raise _http_error(404, str(exc))
    except ValueError as exc:
        raise _http_error(400, str(exc))
    run_date = rec.get("valuation_date") or date
    if not run_date:
        raise _http_error(400, "缺少估值日，無法匯出訊號。")
    report_paths = {}
    if rec.get("md"):
        report_paths[ticker] = rec["md"]
    elif rec.get("json"):
        report_paths[ticker] = rec["json"]  # placeholder, exporter will prefer sibling md if needed
    try:
        result = export_signals(
            tickers=[ticker],
            run_date=run_date,
            dest="out/signals.json",
            merge=True,
            generated_by=GENERATED_BY,
            force_dest=False,
            report_paths=report_paths if rec.get("md") else None,
        )
    except SignalValidationError as exc:
        raise _http_error(400, str(exc), errors=exc.errors)
    except ProductionPathError as exc:
        raise _http_error(403, str(exc))
    except SignalExportError as exc:
        raise _http_error(400, str(exc))
    incoming = result["incoming"]
    return {
        "ok": True,
        "dest": str(result["dest"]),
        "dated": str(result["dated"]),
        "summary": [summarize_signal(s) for s in incoming],
        "doc": result["doc"],
        "disclaimer": "此為研究訊號檔，並非投資建議或下單指令。",
    }


def main() -> None:
    host = os.environ.get("VALUATION_WEB_HOST") or DEFAULT_HOST
    port = int(os.environ.get("VALUATION_WEB_PORT") or DEFAULT_PORT)
    print(f"本機網頁介面：http://{host}:{port}/")
    print("預設只綁定本機。本系統產出不構成投資建議。CLI 指令不受影響。")
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()

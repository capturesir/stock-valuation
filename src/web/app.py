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
from . import services
from .sheets import render_workbook

WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

app = FastAPI(title="可攜式股票估值系統", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


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
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "service": "valuation-web", "disclaimer": "本系統產出不構成投資建議。"}


@app.get("/api/lookup")
def api_lookup(ticker: str = Query(..., min_length=1)):
    result = services.lookup_ticker(ticker)
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


@app.get("/api/jobs/{job_id}/sheets")
def api_job_sheets(job_id: str):
    job = jobmod.store.get(job_id)
    if not job:
        raise _http_error(404, "找不到此工作。")
    if job.get("status") != "done" or not (job.get("result") or {}).get("xlsx"):
        raise _http_error(409, "估值尚未完成，或沒有可顯示的 Excel。", status=job.get("status"))
    rec = job["result"]
    data = render_workbook(rec["xlsx"], rec.get("json"))
    return {"ok": True, "job_id": job_id, "meta": rec, "workbook": data}


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
    except FileNotFoundError as exc:
        raise _http_error(404, str(exc))
    except ValueError as exc:
        raise _http_error(400, str(exc))
    data = render_workbook(rec["xlsx"], rec.get("json"))
    return {"ok": True, "meta": rec, "workbook": data}


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
        raise _http_error(409, "估值尚未完成，或沒有可匯出的估值 JSON。", status=job.get("status"))
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

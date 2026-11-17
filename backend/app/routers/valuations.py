"""AI plan, starting a valuation, job status, live progress (SSE) and cancel."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import pricing
from ..auth import Principal, current_principal
from ..db import get_db
from ..i18n import http_error, lang_dep
from ..jobs import enqueue, new_job, store
from ..schemas import Job, Mode, Plan, ValuateIn
from ..services import settings_store
from ..services.valuation import build_steps
from .common import ticker_or_400, today

router = APIRouter(tags=["valuations"])
TERMINAL = ("done", "failed", "cancelled")


def _has_name_spec(ticker: str) -> bool:
    from src.web.services import KNOWN_SPECS
    return ticker in KNOWN_SPECS


def make_plan(db: Session, ticker: str, mode: str, tasks=None, budget=None) -> dict:
    ai = settings_store.public_ai_settings(db)
    model = ai["model"]
    p = pricing.plan(mode=mode, model=model, prices=settings_store.price_table(db),
                     llm_configured=ai["configured"], local=ai["local"],
                     has_name_spec=_has_name_spec(ticker), selected=tasks,
                     calibration=settings_store.calibration(db, model) if model else 1.0)
    spent = settings_store.month_spend(db)
    per_run = ai["budget_per_run_usd"] if budget is None else budget
    high = p["total"]["cost_high_usd"]
    over = high > 0 and (high > per_run or spent + high > ai["budget_monthly_usd"])
    return {**p, "ticker": ticker, "budget_per_run_usd": per_run, "budget_monthly_usd": ai["budget_monthly_usd"],
            "month_spent_usd": round(spent, 6), "over_budget": over, "over_budget_action": ai["over_budget"],
            "has_name_spec": _has_name_spec(ticker)}


@router.get("/ai/plan", response_model=Plan, summary="AI 工作清單與預估費用")
def plan(ticker: str, mode: Mode = "standard", tasks: str | None = Query(None, description="逗號分隔，例如 T1,T3"),
         db: Session = Depends(get_db)) -> dict:
    t = ticker_or_400(ticker)
    chosen = [x.strip().upper() for x in tasks.split(",") if x.strip()] if tasks else None
    return make_plan(db, t, mode, chosen)


@router.post("/valuations", response_model=Job, status_code=202, summary="開始估值（背景執行）")
async def start(body: ValuateIn, who: Principal = Depends(current_principal), db: Session = Depends(get_db),
                lang: str = Depends(lang_dep)) -> dict:
    t = ticker_or_400(body.ticker)
    st = store()
    if any(j["ticker"] == t for j in st.active()):
        raise http_error(409, "job_busy", lang, ticker=t)
    p = make_plan(db, t, body.mode, body.tasks, body.budget_usd)
    tasks, note = p["tasks"], None
    if p["over_budget"]:
        if p["over_budget_action"] == "ask":
            left = max(p["budget_monthly_usd"] - p["month_spent_usd"], 0)
            raise http_error(402, "over_budget", lang, cost=p["total"]["cost_high_usd"],
                             run=p["budget_per_run_usd"], left=left)
        note = pricing.SKIP_REASONS["over_budget"]
        tasks = [dict(x, will_run=False, skip_reason=note, skip_code="over_budget") if x["will_run"] else x
                 for x in tasks]
    use_llm = any(x["will_run"] for x in tasks)
    job = new_job(t, body.mode, body.valuation_date or today(), tasks, build_steps(tasks),
                  estimate=p["total"] if use_llm else {**p["total"], "cost_usd": 0, "cost_low_usd": 0, "cost_high_usd": 0},
                  budget_usd=p["budget_per_run_usd"], note=note)
    st.put(job)
    await enqueue(job, {"use_llm": use_llm, "trigger": "manual", "requested_by": who.user.id if who.user else None})
    return st.get(job["id"]) or job


@router.get("/jobs", response_model=list[Job], summary="進行中的工作")
def active_jobs() -> list[dict]:
    return store().active()


@router.get("/jobs/{job_id}", response_model=Job, summary="工作狀態（輪詢用，小程序適用）")
def job(job_id: str, lang: str = Depends(lang_dep)) -> dict:
    j = store().get(job_id)
    if j is None:
        raise http_error(404, "job_not_found", lang)
    return j


@router.post("/jobs/{job_id}/cancel", response_model=Job, summary="停止估值（已完成的步驟保留）")
def cancel(job_id: str, lang: str = Depends(lang_dep)) -> dict:
    st = store()
    j = st.get(job_id)
    if j is None:
        raise http_error(404, "job_not_found", lang)
    if j["status"] not in TERMINAL:
        st.cancel(job_id)
        if j["status"] == "queued":
            j = st.update(job_id, status="cancelled", activity="已取消")
    return j


@router.get("/jobs/{job_id}/events", summary="工作進度串流（Server-Sent Events）",
            response_class=StreamingResponse)
async def events(job_id: str, request: Request, lang: str = Depends(lang_dep)):
    st = store()
    if await asyncio.to_thread(st.get, job_id) is None:
        raise http_error(404, "job_not_found", lang)

    async def stream():
        last, idle = -1, 0.0
        yield "retry: 2000\n\n"
        while True:
            if await request.is_disconnected():
                return
            j = await asyncio.to_thread(st.get, job_id)
            if j is None:
                return
            if j["version"] != last:
                last, idle = j["version"], 0.0
                yield f"event: job\ndata: {json.dumps(j, ensure_ascii=False)}\n\n"
                if j["status"] in TERMINAL:
                    return
            elif idle >= 15:
                idle = 0.0
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.5)
            idle += 0.5

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

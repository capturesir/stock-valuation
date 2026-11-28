"""AI plan, starting a valuation, job status, live progress (SSE) and cancel."""
from __future__ import annotations

import asyncio
import datetime as dt
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import pricing
from ..auth import Principal, current_principal
from .. import limits
from ..db import get_db
from ..i18n import msg
from ..models import Run
from ..i18n import http_error, lang_dep
from ..jobs import enqueue, new_job, store, with_position
from ..schemas import Job, Mode, Plan, ValuateIn
from ..services import settings_store, valuation
from ..services.valuation import build_steps
from .common import iso, ticker_or_400, today

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
                     has_name_spec=False, selected=tasks,
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


@router.post("/valuations", response_model=Job, status_code=202,
             responses={200: {"description": "同一代號已在估值：加入該工作（joined=true）"},
                        409: {"description": "近期已用相同條件估值（recent_valuation），可帶 force=true 重算"},
                        429: {"description": "超出每人估值次數或同時數量（rate_limited／too_many_jobs）"},
                        503: {"description": "估值佇列已滿（queue_full）"}},
             summary="開始估值（背景執行；同一代號正在估值時加入該工作）")
async def start(body: ValuateIn, request: Request, response: Response, who: Principal = Depends(current_principal),
                db: Session = Depends(get_db), lang: str = Depends(lang_dep)) -> dict:
    t = ticker_or_400(body.ticker)
    st = store()
    active = st.active()
    uid = who.user.id if who.user else None
    plan = valuation.resolve_plan(db, t, uid, body.plan)
    key = valuation.plan_key(plan)
    # 1. someone is already valuing this ticker: follow that job (same adjustment plan version) instead of a second one
    same = next((j for j in active if j["ticker"] == t), None)
    if same is not None:
        if valuation.plan_key(same.get("plan")) != key:
            raise http_error(409, "ticker_busy", lang, ticker=t)
        response.status_code = 200
        return dict(with_position(same, active), joined=True)
    lim = limits.get(db)
    # 2. per-user and whole-system budgets
    mine = [j for j in active if uid and j.get("requested_by") == uid]
    if uid and len(mine) >= lim["concurrent_valuations_per_user"]:
        raise limits.RateLimited("too_many_jobs", 30, n=len(mine))
    if len(active) >= lim["queue_max"]:
        raise limits.RateLimited("queue_full", 60, n=len(active))
    vdate = body.valuation_date or today()
    # 3. same ticker, date and AI mode valued moments ago: unchanged inputs give the same answer
    if not body.force and lim["revalue_cooldown_hours"] > 0:
        since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=lim["revalue_cooldown_hours"])
        recent = db.scalars(select(Run).where(Run.ticker == t, Run.status == "done", Run.valuation_date == vdate,
                                              Run.ai_mode == body.mode, Run.plan_key == key, Run.run_at >= since)
                            .order_by(Run.run_at.desc()).limit(1)).first()
        if recent is not None:
            raise HTTPException(status_code=409, detail={
                "code": "recent_valuation", "run_id": recent.id, "run_at": iso(recent.run_at),
                "composite_value": recent.composite_value,
                "error": msg("recent_valuation", lang, ticker=t, when=iso(recent.run_at)[:16].replace("T", " "),
                             value=f"{recent.composite_value:.2f}" if recent.composite_value is not None else "—")})
    limits.hit("valuations", limits.who(request, uid), lim["valuations_per_hour"], 3600)
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
    job = new_job(t, body.mode, vdate, tasks, build_steps(tasks),
                  estimate=p["total"] if use_llm else {**p["total"], "cost_usd": 0, "cost_low_usd": 0, "cost_high_usd": 0},
                  budget_usd=p["budget_per_run_usd"], note=note, requested_by=uid)
    job["plan"] = plan
    st.put(job)
    await enqueue(job, {"use_llm": use_llm, "trigger": "manual", "requested_by": uid})
    return with_position(st.get(job["id"]) or job)


@router.get("/jobs", response_model=list[Job], summary="進行中的工作")
def active_jobs() -> list[dict]:
    active = store().active()
    return [with_position(j, active) for j in sorted(active, key=lambda j: j["created_at"])]


@router.get("/jobs/{job_id}", response_model=Job, summary="工作狀態（輪詢用，小程序適用）")
def job(job_id: str, lang: str = Depends(lang_dep)) -> dict:
    j = store().get(job_id)
    if j is None:
        raise http_error(404, "job_not_found", lang)
    return with_position(j)


@router.post("/jobs/{job_id}/cancel", response_model=Job, summary="停止估值（已完成的步驟保留）")
def cancel(job_id: str, who: Principal = Depends(current_principal), lang: str = Depends(lang_dep)) -> dict:
    st = store()
    j = st.get(job_id)
    if j is None:
        raise http_error(404, "job_not_found", lang)
    # a job can be shared by several users who joined it: only its starter or an admin may stop it
    if not who.is_admin and j.get("requested_by") and (who.user is None or who.user.id != j["requested_by"]):
        raise http_error(403, "not_your_job", lang)
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
        last, idle = None, 0.0
        yield "retry: 2000\n\n"
        while True:
            if await request.is_disconnected():
                return
            j = await asyncio.to_thread(st.get, job_id)
            if j is None:
                return
            if j["version"] != last or j["status"] == "queued":
                j = await asyncio.to_thread(with_position, j)
                key = (j["version"], j.get("queue_position"))
                if key == last:
                    await asyncio.sleep(0.5)
                    idle += 0.5
                    continue
                last, idle = key, 0.0
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

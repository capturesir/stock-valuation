"""AI connection (bring your own key), price table, budgets and usage."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import pricing
from ..db import get_db
from ..i18n import http_error, lang_dep
from ..models import AiTask, Run
from ..schemas import AiSettingsIn, AiSettingsOut, PriceRow, UsageOut
from ..services import settings_store

router = APIRouter(tags=["settings"])


@router.get("/settings/ai", response_model=AiSettingsOut, summary="AI 連線與預算（金鑰只顯示長度與末四碼）")
def get_ai(db: Session = Depends(get_db)) -> dict:
    return settings_store.public_ai_settings(db)


@router.put("/settings/ai", response_model=AiSettingsOut, summary="更新 AI 連線與預算")
def put_ai(body: AiSettingsIn, db: Session = Depends(get_db), lang: str = Depends(lang_dep)) -> dict:
    try:
        return settings_store.update_ai_settings(db, body.model_dump())
    except ValueError as exc:
        raise http_error(400, "bad_request", lang, detail=exc)


@router.post("/settings/ai/test", summary="測試 AI 連線（一次極小請求，顯示 Token 與費用）")
def test_ai(db: Session = Depends(get_db), lang: str = Depends(lang_dep)) -> dict:
    from src.llm.client import LLMClient

    cfg = settings_store.llm_cfg(db)
    if not (cfg["llm"]["base_url"] and cfg["llm"]["model"]):
        raise http_error(400, "ai_not_configured", lang)
    client = LLMClient(cfg)
    started = dt.datetime.now()
    res = client.chat_json('只回覆 JSON：{"ok": true}', "ping", tag="settings_test")
    seconds = (dt.datetime.now() - started).total_seconds()
    u = res.usage or {}
    local = pricing.is_local(cfg["llm"]["base_url"])
    price = {"input": 0.0, "output": 0.0} if local else pricing.find_price(cfg["llm"]["model"], settings_store.price_table(db))
    c = pricing.cost(u.get("prompt_tokens") or 0, u.get("completion_tokens") or 0, price)
    return {"ok": res.ok, "error": res.error, "seconds": round(seconds, 2), "model": cfg["llm"]["model"],
            "tokens_in": u.get("prompt_tokens"), "tokens_out": u.get("completion_tokens"),
            "cost_usd": c, "priced": price is not None}


@router.get("/settings/prices", response_model=list[PriceRow], summary="價格表（US$／百萬 Token）")
def get_prices(db: Session = Depends(get_db)) -> list[dict]:
    return settings_store.price_table(db)


@router.put("/settings/prices", response_model=PriceRow, summary="新增或修改一個模型的價格")
def put_price(body: PriceRow, db: Session = Depends(get_db)) -> dict:
    return settings_store.upsert_price(db, body.model_dump(exclude={"stale"}))


@router.get("/ai/usage", response_model=UsageOut, summary="AI 用量：每日、按任務、按股票")
def usage(month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"), db: Session = Depends(get_db)) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    y, m = (int(month[:4]), int(month[5:])) if month else (now.year, now.month)
    start = dt.datetime(y, m, 1, tzinfo=dt.timezone.utc)
    end = dt.datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=dt.timezone.utc)
    tasks = db.scalars(select(AiTask).where(AiTask.created_at >= start, AiTask.created_at < end)).all()
    runs = db.scalars(select(Run).where(Run.run_at >= start, Run.run_at < end, Run.source == "v2")).all()
    daily: dict[str, float] = defaultdict(float)
    by_task: dict[str, float] = defaultdict(float)
    by_ticker: dict[str, float] = defaultdict(float)
    for t in tasks:
        created = t.created_at if t.created_at.tzinfo else t.created_at.replace(tzinfo=dt.timezone.utc)
        daily[created.date().isoformat()] += t.cost_usd or 0
        by_task[t.task] += t.cost_usd or 0
        by_ticker[t.ticker] += t.cost_usd or 0
    ai = settings_store.public_ai_settings(db)
    days = (end - start).days
    labels = {x.key: x.label for x in pricing.TASKS}
    return {
        "month": f"{y:04d}-{m:02d}",
        "total_usd": round(sum(t.cost_usd or 0 for t in tasks), 6),
        "budget_monthly_usd": ai["budget_monthly_usd"],
        "runs": len(runs), "runs_with_ai": sum(1 for r in runs if (r.ai_cost_usd or 0) > 0),
        "tokens_in": sum(t.tokens_in or 0 for t in tasks), "tokens_out": sum(t.tokens_out or 0 for t in tasks),
        "calibration": settings_store.calibration(db, ai["model"]) if ai["model"] else 1.0,
        "daily": [{"date": (start + dt.timedelta(days=i)).date().isoformat(),
                   "cost_usd": round(daily.get((start + dt.timedelta(days=i)).date().isoformat(), 0.0), 6)}
                  for i in range(days)],
        "by_task": sorted(({"task": k, "label": labels.get(k, k), "cost_usd": round(v, 6)} for k, v in by_task.items()),
                          key=lambda x: -x["cost_usd"]),
        "by_ticker": sorted(({"ticker": k, "cost_usd": round(v, 6)} for k, v in by_ticker.items()),
                            key=lambda x: -x["cost_usd"]),
    }

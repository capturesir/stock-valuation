"""AI connection, budgets and the price table, kept in the database."""
from __future__ import annotations

import datetime as dt
import statistics
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import pricing
from ..models import AiTask, Price, Setting
from ..security import decrypt, encrypt, mask

AI_KEY = "ai"
DEFAULT_AI = {
    "provider": "deepseek",
    "base_url": "",
    "model": "",
    "api_key_enc": None,
    "budget_per_run_usd": 0.50,
    "budget_monthly_usd": 5.00,
    "over_budget": "fallback",      # fallback: run without AI | ask: refuse and let the user decide
    "notify_at_pct": 80,
}


def _get(db: Session, key: str, default: dict) -> dict:
    row = db.get(Setting, key)
    return {**default, **(row.value if row else {})}


def _put(db: Session, key: str, value: dict) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


def ai_settings(db: Session) -> dict:
    """Stored settings with the key decrypted (server side only — never return this to a client)."""
    s = _get(db, AI_KEY, DEFAULT_AI)
    s["api_key"] = decrypt(s.get("api_key_enc"))
    return s


def public_ai_settings(db: Session) -> dict:
    s = ai_settings(db)
    local = pricing.is_local(s["base_url"])
    return {
        "provider": s["provider"], "base_url": s["base_url"], "model": s["model"],
        "api_key": mask(s["api_key"]),
        "configured": bool(s["base_url"] and s["model"] and (s["api_key"] or local)),
        "local": local,
        "budget_per_run_usd": s["budget_per_run_usd"], "budget_monthly_usd": s["budget_monthly_usd"],
        "over_budget": s["over_budget"], "notify_at_pct": s["notify_at_pct"],
        "presets": list(pricing.PROVIDER_PRESETS),
    }


def update_ai_settings(db: Session, patch: dict) -> dict:
    s = _get(db, AI_KEY, DEFAULT_AI)
    for k in ("provider", "base_url", "model", "budget_per_run_usd", "budget_monthly_usd", "over_budget", "notify_at_pct"):
        if patch.get(k) is not None:
            s[k] = patch[k]
    s["base_url"] = (s["base_url"] or "").strip().rstrip("/")
    if s["base_url"] and not s["base_url"].startswith(("http://", "https://")):
        raise ValueError("API 位址必須以 http:// 或 https:// 開頭。")
    if patch.get("clear_api_key"):
        s["api_key_enc"] = None
    elif patch.get("api_key"):
        s["api_key_enc"] = encrypt(str(patch["api_key"]).strip())   # empty = keep the old key
    _put(db, AI_KEY, s)
    db.flush()
    return public_ai_settings(db)


def llm_cfg(db: Session) -> dict:
    """The cfg dict the v1 engine's LLMClient understands."""
    s = ai_settings(db)
    return {"llm": {"base_url": s["base_url"], "model": s["model"], "api_key": s["api_key"] or "",
                    "timeout_seconds": 120, "max_retries": 3, "json_mode": True}}


# ---------------------------------------------------------------- prices

def ensure_default_prices(db: Session) -> None:
    for p in pricing.DEFAULT_PRICES:
        if db.get(Price, p["model"]) is None:
            db.add(Price(**p))
    db.flush()


def price_table(db: Session) -> list[dict]:
    rows = db.scalars(select(Price).order_by(Price.model)).all()
    today = dt.date.today()
    out = []
    for r in rows:
        stale = False
        if r.checked_on:
            try:
                stale = (today - dt.date.fromisoformat(r.checked_on)).days > 90
            except ValueError:
                stale = False
        out.append({"model": r.model, "input": r.input, "cached_input": r.cached_input, "output": r.output,
                    "note": r.note, "checked_on": r.checked_on, "source_url": r.source_url, "stale": stale})
    return out


def upsert_price(db: Session, p: dict) -> dict:
    row = db.get(Price, p["model"])
    if row is None:
        row = Price(model=p["model"], input=p["input"], output=p["output"])
        db.add(row)
    for k in ("input", "cached_input", "output", "note", "checked_on", "source_url"):
        if k in p:
            setattr(row, k, p[k])
    db.flush()
    return next(x for x in price_table(db) if x["model"] == row.model)


# ---------------------------------------------------------------- usage

def calibration(db: Session, model: str) -> float:
    """Median of actual ÷ estimated tokens over the last 10 reported tasks of this model."""
    rows = db.scalars(
        select(AiTask).where(AiTask.model == model, AiTask.usage_reported.is_(True),
                             AiTask.est_tokens_in > 0)
        .order_by(AiTask.created_at.desc()).limit(10)).all()
    ratios = [(r.tokens_in + r.tokens_out) / (r.est_tokens_in + r.est_tokens_out) for r in rows
              if (r.est_tokens_in + r.est_tokens_out) > 0]
    if len(ratios) < 3:
        return 1.0
    return max(0.3, min(3.0, statistics.median(ratios)))


def month_spend(db: Session, now: Optional[dt.datetime] = None) -> float:
    now = now or dt.datetime.now(dt.timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = db.scalar(select(func.coalesce(func.sum(AiTask.cost_usd), 0.0)).where(AiTask.created_at >= start))
    return float(total or 0.0)

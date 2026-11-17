"""Helpers shared by the routers."""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from src.pipeline.tickers import normalize

from ..config import get_settings
from ..i18n import AppError
from ..models import Run


def ticker_or_400(raw: str) -> str:
    t = normalize(raw)          # raises InvalidTicker → 400 (see main.py)
    if not t:
        raise AppError("ticker_required")
    return t


def today() -> str:
    return dt.datetime.now(ZoneInfo(get_settings().timezone)).date().isoformat()


def iso(ts: dt.datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    return ts.astimezone(ZoneInfo(get_settings().timezone)).isoformat(timespec="seconds")


def run_out(r: Run) -> dict:
    return {
        "id": r.id, "ticker": r.ticker, "company": r.company, "run_at": iso(r.run_at),
        "valuation_date": r.valuation_date, "status": r.status, "trigger": r.trigger, "ai_mode": r.ai_mode,
        "source": r.source, "error": r.error, "price": r.price, "price_date": r.price_date,
        "trading_ccy": r.trading_ccy, "composite_value": r.composite_value, "weights": r.weights,
        "wacc": r.wacc, "terminal_g": r.terminal_g, "verify_pass": r.verify_pass,
        "assumption_source": r.assumption_source, "ai_cost_usd": r.ai_cost_usd or 0.0,
        "ai_tokens_in": r.ai_tokens_in or 0, "ai_tokens_out": r.ai_tokens_out or 0,
        "has_excel": bool(r.xlsx_path and Path(r.xlsx_path).is_file()),
        "methods": [{"method": m.method, "scenario": m.scenario, "value": m.value, "weight": m.weight}
                    for m in r.methods],
        "data_gaps": list(r.data_gaps or []),
    }


def scenario(r: Run, name: str):
    return next((m.value for m in r.methods if m.method == "dcf" and m.scenario == name), None)

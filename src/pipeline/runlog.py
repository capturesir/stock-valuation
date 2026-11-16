"""Append-only log of every valuation run.

Why a log and not a folder per run:

out/<date>_<ticker>/ is the "current result for that date" and is referenced by
the CLI, the workbook resolver and the README. Renaming it to make room for
repeats would break those paths for a cosmetic gain. Instead each run appends
one small record to out/history/<TICKER>.json, so:

- same-day re-runs stay separate (R17/R19/R20 need every valuation, not one per
  day), while a run still overwrites "today's" result as it always did;
- nothing that already exists moves, so the risk is limited to one new file;
- the history can be rebuilt for old dates from the folders, and the two
  sources are merged by value, so no pre-existing point is lost.

If the log is deleted the chart simply falls back to one point per date.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / "out" / "history"
MAX_POINTS = 500  # per ticker; enough for years of daily re-runs


def _path(ticker: str) -> Path:
    safe = ticker.replace("/", "_").replace("..", "_")
    return LOG_DIR / f"{safe}.json"


def read(ticker: str) -> list[dict]:
    try:
        data = json.loads(_path(ticker).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def append(ticker: str, record: dict) -> dict:
    """Append one run and return the stored record. Never raises on write I/O."""
    entry = dict(record)
    entry.setdefault("run_id", uuid.uuid4().hex[:12])
    entry["ticker"] = ticker
    points = read(ticker)
    points.append(entry)
    points = points[-MAX_POINTS:]
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _path(ticker).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(points, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, _path(ticker))
    except Exception:
        pass
    return entry


def record_from_calc(calc: dict, *, verify_pass: Optional[bool] = None,
                     llm_total_tokens: Optional[int] = None,
                     adjustments: Optional[int] = None) -> dict:
    """Build the log record from a finished calc JSON (the same numbers the UI shows)."""
    import datetime as dt

    scenarios = {
        s.get("name"): s.get("fair_value") for s in (calc.get("scenarios") or [])
        if s.get("name")
    }
    return {
        "valuation_date": calc.get("valuation_date"),
        "run_at": dt.datetime.now().isoformat(timespec="seconds"),
        "company": calc.get("asset"),
        "currency": calc.get("currency"),
        "price": calc.get("price"),
        "price_date": calc.get("price_date"),
        "fair_value": calc.get("fair_value_per_share"),
        "wacc": ((calc.get("discount") or {}).get("wacc") or {}).get("value"),
        "g": (calc.get("terminal") or {}).get("g"),
        "verify_pass": verify_pass,
        "assumption_source": calc.get("assumption_source"),
        "scenarios": scenarios,
        "llm_total_tokens": llm_total_tokens,
        "adjustments_applied": adjustments,
    }

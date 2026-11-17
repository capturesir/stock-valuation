"""Import valuations made with v1 (out/<date>_<ticker>/ and out/history/) into the database once."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ROOT
from ..models import Run, Setting
from .valuation import persist_run

FOLDER_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(.+)$")
MARK = "legacy_import"


def _key(ticker, date, fair, price) -> tuple:
    return (ticker, str(date)[:10], round(float(fair or 0), 4), round(float(price or 0), 4))


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def import_legacy(db: Session, out_dir: Path = ROOT / "out", force: bool = False) -> int:
    """Returns the number of runs imported. Idempotent: values already present are skipped."""
    if not force and db.get(Setting, MARK) is not None:
        return 0
    seen = {_key(r.ticker, r.valuation_date, r.composite_value, r.price)
            for r in db.scalars(select(Run).where(Run.status == "done"))}
    added = 0

    # 1. date folders: full calc JSON, Excel and verify result
    for folder in sorted(p for p in out_dir.glob("*_*") if p.is_dir()):
        m = FOLDER_RE.match(folder.name)
        if not m:
            continue
        date, ticker = m.groups()
        calc = _read(folder / f"{date}_{ticker}.json")
        if not isinstance(calc, dict) or calc.get("fair_value_per_share") is None:
            continue
        meta = _read(folder / "run_meta.json") or {}
        k = _key(calc.get("ticker", ticker), date, calc["fair_value_per_share"], calc.get("price"))
        if k in seen:
            continue
        calc = dict(calc, ticker=calc.get("ticker") or ticker, valuation_date=date)
        xlsx = folder / f"{date}_{ticker}_FCFF.xlsx"
        persist_run(db, calc, source="legacy", trigger="legacy", verify_pass=meta.get("verify_pass"),
                    calc_path=str(folder / f"{date}_{ticker}.json"), xlsx_path=str(xlsx) if xlsx.is_file() else None,
                    run_at=dt.datetime.fromisoformat(f"{date}T12:00:00+08:00"))
        seen.add(k)
        added += 1

    # 2. v1.1 run log: one record per run, including same-day re-runs
    for log in sorted((out_dir / "history").glob("*.json")):
        records = _read(log)
        if not isinstance(records, list):
            continue
        for r in records:
            if r.get("fair_value") is None or r.get("price") is None:
                continue
            ticker = r.get("ticker") or log.stem
            k = _key(ticker, r.get("valuation_date"), r["fair_value"], r["price"])
            if k in seen:
                continue
            calc = {
                "ticker": ticker, "asset": r.get("company"), "valuation_date": r.get("valuation_date"),
                "price": r["price"], "price_date": r.get("price_date"), "fair_value_per_share": r["fair_value"],
                # v1.1 logged the model currency, which can differ from the trading currency
                "currency": None, "assumption_source": r.get("assumption_source"),
                "discount": {"wacc": {"value": r.get("wacc")}}, "terminal": {"g": r.get("g")},
                "scenarios": [{"name": n, "fair_value": v} for n, v in (r.get("scenarios") or {}).items()],
            }
            run_at = None
            try:
                run_at = dt.datetime.fromisoformat(r["run_at"]).replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))
            except Exception:
                pass
            persist_run(db, calc, source="legacy", trigger="legacy", verify_pass=r.get("verify_pass"), run_at=run_at)
            seen.add(k)
            added += 1

    if db.get(Setting, MARK) is None:
        db.add(Setting(key=MARK, value={"at": dt.datetime.now(dt.timezone.utc).isoformat(), "runs": added}))
    db.flush()
    return added

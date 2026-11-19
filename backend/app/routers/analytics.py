"""Comparison chart, saved comparisons and portfolios (alpha.4). Comparisons and portfolios are private (D10)."""
from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import limits
from ..auth import require_user
from ..db import get_db
from ..i18n import AppError, http_error, lang_dep
from ..models import Comparison, Portfolio, Scenario, User
from ..schemas import (CompareIn, CompareOut, OptimizeIn, OptimizeOut, PortfolioIn, PortfolioOut, RecomputeOut, SavedComparisonIn,
                       SavedComparisonOut, ScenarioIn, ScenarioOut, ScenarioSummary)
from ..services import analytics
from .common import iso, ticker_or_400

router = APIRouter(tags=["analytics"])


def _date(v: str | None) -> dt.date | None:
    if not v:
        return None
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        raise AppError("bad_date", value=v)


def _run(body: CompareIn, user: User, db: Session) -> dict:
    lines = []
    for ln in body.lines:
        lines.append({"kind": ln.kind, "ref": ticker_or_400(ln.ref) if ln.kind == "ticker" else ln.ref})
    currency = (body.currency or (user.prefs or {}).get("display_currency") or "USD").upper()
    return analytics.compare(db, lines, body.benchmark, body.range, _date(body.start), _date(body.end), currency, body.rf,
                             user_id=user.id)


@router.post("/analytics/compare", response_model=CompareOut,
             summary="走勢比較：最多 10 條線（股票或已保存組合），以起點 100 的總回報指數、相對強弱與指標（顯示貨幣）")
def compare(body: CompareIn, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    lim = limits.get(db)
    limits.hit("prices", limits.who(request, user.id), lim["prices_per_minute"], 60)
    return _run(body, user, db)


@router.post("/analytics/compare/export", summary="匯出走勢比較：Excel（走勢、指標、期間回報）或 CSV")
def compare_export(body: CompareIn, format: Literal["xlsx", "csv"] = "xlsx", user: User = Depends(require_user),
                   db: Session = Depends(get_db), lang: str = Depends(lang_dep)) -> Response:
    from ..services import analytics_export

    data = _run(body, user, db)
    stem = f"compare_{dt.date.today().isoformat()}"
    if format == "csv":
        return Response(analytics_export.compare_csv(data, lang), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{stem}.csv"'})
    return Response(analytics_export.compare_xlsx(data, lang, db),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{stem}.xlsx"'})


# ------------------------------------------------------------------ saved comparisons

def _cmp_out(c: Comparison) -> dict:
    return {"id": c.id, "name": c.name, "settings": c.settings, "updated_at": iso(c.updated_at)}


def _own(db: Session, model, id_: str, user: User, code: str, lang: str):
    row = db.get(model, id_)
    if row is None or row.user_id != user.id:
        raise http_error(404, code, lang)
    return row


@router.get("/comparisons", response_model=list[SavedComparisonOut], summary="我保存的走勢比較")
def list_comparisons(user: User = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    return [_cmp_out(c) for c in db.scalars(select(Comparison).where(Comparison.user_id == user.id).order_by(Comparison.updated_at.desc()))]


@router.post("/comparisons", response_model=SavedComparisonOut, status_code=201, summary="保存走勢比較")
def save_comparison(body: SavedComparisonIn, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    c = Comparison(user_id=user.id, name=body.name.strip(), settings=body.settings.model_dump())
    db.add(c)
    db.flush()
    return _cmp_out(c)


@router.put("/comparisons/{cid}", response_model=SavedComparisonOut, summary="更新已保存的走勢比較")
def update_comparison(cid: str, body: SavedComparisonIn, user: User = Depends(require_user), db: Session = Depends(get_db),
                      lang: str = Depends(lang_dep)) -> dict:
    c = _own(db, Comparison, cid, user, "comparison_not_found", lang)
    c.name, c.settings = body.name.strip(), body.settings.model_dump()
    db.flush()
    return _cmp_out(c)


@router.delete("/comparisons/{cid}", status_code=204, summary="刪除已保存的走勢比較")
def delete_comparison(cid: str, user: User = Depends(require_user), db: Session = Depends(get_db),
                      lang: str = Depends(lang_dep)) -> None:
    db.delete(_own(db, Comparison, cid, user, "comparison_not_found", lang))


# ------------------------------------------------------------------ portfolios

def _pf_out(p: Portfolio) -> dict:
    return {"id": p.id, "name": p.name, "holdings": p.holdings, "rebalance": p.rebalance, "notes": p.notes,
            "updated_at": iso(p.updated_at)}


def _holdings(body: PortfolioIn) -> list[dict]:
    merged: dict[str, float] = {}
    for h in body.holdings:
        t = ticker_or_400(h.ticker)
        merged[t] = merged.get(t, 0.0) + h.weight
    total = sum(merged.values())
    return [{"ticker": t, "weight": round(w / total, 6)} for t, w in merged.items()]


@router.get("/portfolios", response_model=list[PortfolioOut], summary="我的投資組合")
def list_portfolios(user: User = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    return [_pf_out(p) for p in db.scalars(select(Portfolio).where(Portfolio.user_id == user.id).order_by(Portfolio.updated_at.desc()))]


@router.post("/portfolios", response_model=PortfolioOut, status_code=201, summary="保存投資組合（權重自動換算為合計 100%）")
def save_portfolio(body: PortfolioIn, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    p = Portfolio(user_id=user.id, name=body.name.strip(), holdings=_holdings(body), rebalance=body.rebalance, notes=body.notes)
    db.add(p)
    db.flush()
    return _pf_out(p)


@router.put("/portfolios/{pid}", response_model=PortfolioOut, summary="修改投資組合")
def update_portfolio(pid: str, body: PortfolioIn, user: User = Depends(require_user), db: Session = Depends(get_db),
                     lang: str = Depends(lang_dep)) -> dict:
    p = _own(db, Portfolio, pid, user, "portfolio_not_found", lang)
    p.name, p.holdings, p.rebalance, p.notes = body.name.strip(), _holdings(body), body.rebalance, body.notes
    db.flush()
    return _pf_out(p)


@router.delete("/portfolios/{pid}", status_code=204, summary="刪除投資組合")
def delete_portfolio(pid: str, user: User = Depends(require_user), db: Session = Depends(get_db),
                     lang: str = Depends(lang_dep)) -> None:
    db.delete(_own(db, Portfolio, pid, user, "portfolio_not_found", lang))


# ------------------------------------------------------------------ optimizer

def run_optimize(body: OptimizeIn, user: User, db: Session) -> dict:
    from ..services import optimizer

    assets = [{"ticker": ticker_or_400(a.ticker), "min": a.min, "max": a.max} for a in body.assets]
    currency = (body.currency or (user.prefs or {}).get("display_currency") or "USD").upper()
    return optimizer.run(db, assets, dict(body.objective), body.range, _date(body.start), _date(body.end), currency,
                         body.rebalance, body.rf, body.seed, body.samples, body.oos_fraction)


@router.post("/analytics/optimize", response_model=OptimizeOut,
             summary="投資組合最佳化：上下限、1% 單位、得分條件、隨機抽樣＋SLSQP（固定種子）、效率前緣、樣本外檢驗")
def optimize(body: OptimizeIn, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    lim = limits.get(db)
    limits.hit("prices", limits.who(request, user.id), lim["prices_per_minute"], 60)
    return run_optimize(body, user, db)


# ------------------------------------------------------------------ saved scenarios (reproducible optimisations)

def _sc_summary(s: Scenario) -> dict:
    return {"id": s.id, "name": s.name, "created_at": iso(s.created_at), "window_start": s.window_start.isoformat(),
            "window_end": s.window_end.isoformat(), "data_version": s.data_version, "system_version": s.system_version,
            "weights": s.result.get("weights", {}), "objective": s.inputs.get("objective", {})}


@router.get("/scenarios", response_model=list[ScenarioSummary], summary="我保存的最佳化場景")
def list_scenarios(user: User = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    return [_sc_summary(s) for s in db.scalars(select(Scenario).where(Scenario.user_id == user.id).order_by(Scenario.created_at.desc()))]


@router.post("/scenarios", response_model=ScenarioOut, status_code=201,
             summary="保存最佳化場景：伺服器重新計算一次並保存輸入、實際數據期間、數據指紋、種子、結果與系統版本")
def save_scenario(body: ScenarioIn, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    lim = limits.get(db)
    limits.hit("prices", limits.who(request, user.id), lim["prices_per_minute"], 60)
    result = run_optimize(body.inputs, user, db)
    inputs = {**body.inputs.model_dump(), "currency": result["currency"]}
    s = Scenario(user_id=user.id, name=body.name.strip(), inputs=inputs,
                 window_start=dt.date.fromisoformat(result["window"]["start"]), window_end=dt.date.fromisoformat(result["window"]["end"]),
                 data_version=result["data_version"], system_version=result["system_version"], result=result)
    db.add(s)
    db.flush()
    return {**_sc_summary(s), "inputs": s.inputs, "result": s.result}


@router.get("/scenarios/{sid}", response_model=ScenarioOut, summary="一個已保存場景的完整輸入與結果")
def get_scenario(sid: str, user: User = Depends(require_user), db: Session = Depends(get_db), lang: str = Depends(lang_dep)) -> dict:
    s = _own(db, Scenario, sid, user, "scenario_not_found", lang)
    return {**_sc_summary(s), "inputs": s.inputs, "result": s.result}


@router.delete("/scenarios/{sid}", status_code=204, summary="刪除場景")
def delete_scenario(sid: str, user: User = Depends(require_user), db: Session = Depends(get_db), lang: str = Depends(lang_dep)) -> None:
    db.delete(_own(db, Scenario, sid, user, "scenario_not_found", lang))


@router.post("/scenarios/{sid}/recompute", response_model=RecomputeOut,
             summary="以保存的輸入、同一數據期間與種子重算，核對結果是否完全相同（數據若被修訂會指出）")
def recompute_scenario(sid: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db),
                       lang: str = Depends(lang_dep)) -> dict:
    lim = limits.get(db)
    limits.hit("prices", limits.who(request, user.id), lim["prices_per_minute"], 60)
    s = _own(db, Scenario, sid, user, "scenario_not_found", lang)
    inputs = OptimizeIn(**{**s.inputs, "range": "custom", "start": s.window_start.isoformat(), "end": s.window_end.isoformat()})
    now = run_optimize(inputs, user, db)
    old_w, new_w = s.result.get("weights", {}), now["weights"]
    changes = {t: [old_w.get(t, 0), new_w.get(t, 0)] for t in sorted(set(old_w) | set(new_w)) if old_w.get(t, 0) != new_w.get(t, 0)}
    pick = lambda r: {"weights": r["weights"], "score": r["score"], "metrics": r["metrics"],          # noqa: E731
                      "data_version": r["data_version"], "system_version": r["system_version"], "window": r["window"]}
    return {"identical": not changes and now["score"] == s.result.get("score"),
            "same_data": now["data_version"] == s.data_version, "same_system": now["system_version"] == s.system_version,
            "original": pick(s.result), "recomputed": pick(now), "weight_changes": changes}

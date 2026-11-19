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
from ..models import Comparison, Portfolio, User
from ..schemas import CompareIn, CompareOut, PortfolioIn, PortfolioOut, SavedComparisonIn, SavedComparisonOut
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

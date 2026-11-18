"""Watchlist with each stock's latest valuation."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_user
from ..db import get_db
from ..i18n import http_error, lang_dep
from ..models import Run, SecTicker, Security, User, WatchlistItem
from ..schemas import WatchlistEntry, WatchlistIn
from ..services import market, market_jobs
from .common import iso, run_out, scenario, ticker_or_400

router = APIRouter(tags=["watchlist"])


def company_name(db: Session, ticker: str) -> str | None:
    """Best known name for a ticker that has never been valued: valuation records, the security master,
    the cached company profile (no network call), then SEC's ticker list."""
    from .. import cache

    name = db.scalar(select(Run.company).where(Run.ticker == ticker, Run.company.is_not(None)).limit(1))
    sec = db.get(Security, ticker)
    name = name or (sec.name if sec else None)
    if not name:
        cached = cache.get(f"profile:{ticker}") or (cache.get_stale(f"profile:{ticker}") or {}).get("value")
        name = cached.get("name") if isinstance(cached, dict) else None
    if not name:
        row = db.get(SecTicker, ticker)
        name = row.title if row else None
    return name or None


def _entry(db: Session, w: WatchlistItem) -> dict:
    latest = db.scalars(select(Run).where(Run.ticker == w.ticker, Run.status == "done")
                        .order_by(Run.run_at.desc()).limit(1)).first()
    status = "never_valued"
    if w.company is None:
        w.company = company_name(db, w.ticker)       # remembered once found
    if latest is not None:
        status = "ok"
        bear, bull = scenario(latest, "bear"), scenario(latest, "bull")
        if latest.price is not None and bear is not None and latest.price < bear:
            status = "below_bear"
        elif latest.price is not None and bull is not None and latest.price > bull:
            status = "above_bull"
    return {"ticker": w.ticker, "company": w.company or (latest.company if latest else None),
            "added_at": iso(w.added_at), "latest": run_out(latest) if latest else None, "status": status}


@router.get("/watchlist", response_model=list[WatchlistEntry], summary="觀察名單")
def list_watchlist(user: User = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    items = db.scalars(select(WatchlistItem).where(WatchlistItem.user_id == user.id)
                       .order_by(WatchlistItem.added_at)).all()
    return [_entry(db, w) for w in items]


@router.post("/watchlist", response_model=WatchlistEntry, status_code=201, summary="加入觀察名單")
def add(body: WatchlistIn, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    t = ticker_or_400(body.ticker)
    w = db.scalar(select(WatchlistItem).where(WatchlistItem.user_id == user.id, WatchlistItem.ticker == t))
    if w is None:
        w = WatchlistItem(user_id=user.id, ticker=t, company=company_name(db, t))
        db.add(w)
        sec = market.track(db, t)            # every ticker in use accumulates daily prices
        db.flush()
        if sec.backfilled_at is None:
            db.commit()                      # the background sync runs in its own transaction
            market_jobs.submit_sync(t)
    return _entry(db, w)


@router.delete("/watchlist/{ticker}", status_code=204, summary="移出觀察名單（不刪除估值紀錄）")
def remove(ticker: str, user: User = Depends(require_user), db: Session = Depends(get_db),
           lang: str = Depends(lang_dep)) -> None:
    t = ticker_or_400(ticker)
    w = db.scalar(select(WatchlistItem).where(WatchlistItem.user_id == user.id, WatchlistItem.ticker == t))
    if w is None:
        raise http_error(404, "not_in_watchlist", lang, ticker=t)
    db.delete(w)

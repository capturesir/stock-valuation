"""Point-in-time valuation backtest using as-first-reported ('original') statements & split-adjusted prices."""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.sv_valuation import BacktestPoint, compute_backtest_scorecard

from ..models import CorporateAction, Filer, PriceBar, Run, SecTicker
from . import statements


def _adj_price_series(db: Session, ticker: str) -> list[tuple[dt.date, float]]:
    """Return split-adjusted daily close series ``[(date, adj_close), ...]`` sorted ascending."""
    bars = db.execute(
        select(PriceBar.date, PriceBar.close).where(PriceBar.ticker == ticker).order_by(PriceBar.date)
    ).all()
    if not bars:
        return []
    splits = db.execute(
        select(CorporateAction.date, CorporateAction.value)
        .where(CorporateAction.ticker == ticker, CorporateAction.kind == "split")
        .order_by(CorporateAction.date)
    ).all()
    out: list[tuple[dt.date, float]] = []
    for d, close in bars:
        if not close or close <= 0:
            continue
        cum = 1.0
        for sd, sv in splits:
            if sd > d and sv and float(sv) > 0:
                cum *= float(sv)
        out.append((d, float(close) / cum))
    return out


def _price_on_or_after(series: list[tuple[dt.date, float]], target: dt.date, max_lag_days: int = 20) -> Optional[tuple[dt.date, float]]:
    for d, px in series:
        if d >= target:
            if (d - target).days <= max_lag_days:
                return d, px
            return None
    return None


def _point_in_time_fv(std: dict, year_idx: int, template: str) -> Optional[float]:
    """Conservative point-in-time intrinsic value using ONLY figures known on that filing date."""
    rows = {r["code"]: r.get("cells") or [] for r in (std.get("rows") or [])}

    def _v(code: str) -> Optional[float]:
        cells = rows.get(code)
        if not cells or year_idx >= len(cells) or not cells[year_idx] or cells[year_idx].get("value") is None:
            return None
        try:
            return float(cells[year_idx]["value"])
        except (TypeError, ValueError):
            return None

    sh = _v("shares_diluted_weighted") or _v("shares_outstanding")
    if not sh or sh <= 0:
        return None
    eps = _v("eps_diluted") or _v("eps_basic")
    eq = _v("common_equity") or _v("equity_parent") or _v("total_equity")
    bvps = (eq / sh) if (eq and eq > 0) else None
    fcf = _v("free_cash_flow")
    fcf_ps = (fcf / sh) if (fcf and fcf > 0) else None

    if template in ("bank", "insurance") and bvps and eps and eps > 0:
        roe = min(0.22, max(0.04, eps / bvps))
        ke = 0.095
        g = 0.025
        sust_roe = ke + 0.5 * min(max(roe - ke, -0.05), 0.05) if roe > ke else roe
        justified_pb = max(0.4, (sust_roe - g) / (ke - g))
        return bvps * justified_pb

    # General corporates: blend conservative FCF yield (14x FCF/sh) + earnings yield (15x EPS) + BVPS anchor
    estimates = []
    if fcf_ps and fcf_ps > 0:
        estimates.append(fcf_ps * 14.0)
    if eps and eps > 0:
        estimates.append(eps * 15.0)
    if not estimates:
        return None
    return sum(estimates) / len(estimates)


def run_backtest_scorecard(
    db: Session,
    *,
    tickers: Optional[list[str]] = None,
    horizon_days: int = 365,
    max_tickers: int = 15,
) -> dict[str, Any]:
    """Run point-in-time backtest over synced companies using 'original' statements (zero look-ahead)."""
    target_tickers: list[str] = []
    if tickers:
        target_tickers = [t.strip().upper() for t in tickers if t.strip()][:max_tickers]
    else:
        # Collect up to max_tickers synced filers that have stored price history
        filers = db.scalars(select(Filer).where(Filer.synced_at.is_not(None)).limit(40)).all()
        for f in filers:
            tk = (f.tickers or [None])[0] if isinstance(f.tickers, list) else None
            if not tk:
                tk = db.scalar(select(SecTicker.ticker).where(SecTicker.cik == f.cik).limit(1))
            if tk and tk not in target_tickers:
                target_tickers.append(tk)
            if len(target_tickers) >= max_tickers:
                break

    points: list[BacktestPoint] = []
    for tk in target_tickers:
        series = _adj_price_series(db, tk)
        if len(series) < 60:
            continue

        # 1. Point-in-time 'original' statements (no restatement look-ahead)
        filer = statements.filer_for(db, tk)
        if filer is not None and filer.synced_at is not None:
            try:
                std = statements.standard_statements(db, filer, view="original", years=6, ticker=tk, period="annual")
                template = std.get("template") or "general"
                years = std.get("years") or []
                for idx, y in enumerate(years):
                    if y.get("kind") != "annual" or not y.get("end"):
                        continue
                    try:
                        # Assume annual report filed ~75 days after fiscal year end when exact date absent
                        as_of = dt.date.fromisoformat(str(y["end"])[:10]) + dt.timedelta(days=75)
                    except ValueError:
                        continue
                    p0 = _price_on_or_after(series, as_of)
                    p1 = _price_on_or_after(series, as_of + dt.timedelta(days=horizon_days), max_lag_days=30)
                    if not p0 or not p1:
                        continue
                    fv = _point_in_time_fv(std, idx, template)
                    if fv and fv > 0:
                        points.append(BacktestPoint(
                            ticker=tk,
                            as_of_date=p0[0].isoformat(),
                            fair_value=fv,
                            price=p0[1],
                            fwd_price=p1[1],
                            fwd_date=p1[0].isoformat(),
                            source="original_statements",
                        ))
            except Exception:
                pass

        # 2. Archived historical valuation runs that already have realized forward prices
        runs = db.scalars(
            select(Run).where(Run.ticker == tk, Run.status == "done", Run.composite_value.is_not(None))
            .order_by(Run.valuation_date)
        ).all()
        seen_months: set[str] = set()
        for r in runs:
            if not r.valuation_date or not r.composite_value or r.composite_value <= 0:
                continue
            ym = r.valuation_date[:7]
            if ym in seen_months:
                continue
            try:
                d0 = dt.date.fromisoformat(r.valuation_date[:10])
            except ValueError:
                continue
            p0 = _price_on_or_after(series, d0)
            p1 = _price_on_or_after(series, d0 + dt.timedelta(days=horizon_days), max_lag_days=30)
            if p0 and p1:
                seen_months.add(ym)
                points.append(BacktestPoint(
                    ticker=tk,
                    as_of_date=p0[0].isoformat(),
                    fair_value=float(r.composite_value),
                    price=p0[1],
                    fwd_price=p1[1],
                    fwd_date=p1[0].isoformat(),
                    source="archived_run",
                ))

    return compute_backtest_scorecard(points, horizon_days=horizon_days)

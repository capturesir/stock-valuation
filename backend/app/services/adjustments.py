"""Adjustment plans (D12): one central plan per stock (admin), one personal plan per user per stock.

Every save is a new version holding the plan's complete set of adjustments; versions are never changed.
A personal plan is created from the central plan as it is then; later central versions are not pushed into
it: the user sees that the central plan was updated and merges what they choose (central_version records it).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.sv_data.adjust import Adjustment, AdjustmentError, validate

from ..i18n import AppError
from ..models import AdjustmentPlan, AdjustmentPlanVersion, AdjustmentRow, Filer, User
from . import statements

KINDS = ("central", "personal")
MAX_ADJUSTMENTS = 500


def get_plan(db: Session, ticker: str, kind: str, user: Optional[User]) -> Optional[AdjustmentPlan]:
    q = select(AdjustmentPlan).where(AdjustmentPlan.ticker == ticker, AdjustmentPlan.kind == kind)
    q = q.where(AdjustmentPlan.user_id.is_(None)) if kind == "central" else q.where(AdjustmentPlan.user_id == user.id)
    return db.scalar(q)


def version_row(db: Session, plan: AdjustmentPlan, version: Optional[int] = None) -> Optional[AdjustmentPlanVersion]:
    n = plan.current_version if version is None else version
    return db.scalar(select(AdjustmentPlanVersion).where(AdjustmentPlanVersion.plan_id == plan.id,
                                                        AdjustmentPlanVersion.version == n))


def rows(db: Session, version: Optional[AdjustmentPlanVersion]) -> list[AdjustmentRow]:
    if version is None:
        return []
    return list(db.scalars(select(AdjustmentRow).where(AdjustmentRow.version_id == version.id)
                           .order_by(AdjustmentRow.period.desc(), AdjustmentRow.item)))


def to_adjustments(rs: list[AdjustmentRow]) -> list[Adjustment]:
    return [Adjustment(r.item, r.period, r.value, r.reason, r.basis, r.adjusted_by,
                       r.adjusted_at.isoformat(timespec="seconds")) for r in rs]


def label(plan: AdjustmentPlan, owner: Optional[str] = None) -> str:
    return f"central v{plan.current_version}" if plan.kind == "central" else f"personal:{owner or ''} v{plan.current_version}"


def chosen(db: Session, ticker: str, user: User, which: str) -> Optional[dict]:
    """The plan the statements are shown with: ``auto`` = my personal plan, else the central plan, else none."""
    if which == "none":
        return None
    order = {"auto": ("personal", "central"), "personal": ("personal",), "central": ("central",)}[which]
    for kind in order:
        p = get_plan(db, ticker, kind, user)
        if p is not None and p.current_version > 0:
            return {"kind": kind, "id": p.id, "version": p.current_version, "label": label(p, user.display_name),
                    "adjustments": to_adjustments(rows(db, version_row(db, p)))}
    return None


def _parse(items: list[dict], rs) -> list[Adjustment]:
    if len(items) > MAX_ADJUSTMENTS:
        raise AppError("adjustments_too_many", n=MAX_ADJUSTMENTS)
    out = []
    for it in items:
        try:
            value = Decimal(str(it["value"]).replace(",", "").strip())
        except (InvalidOperation, KeyError):
            raise AppError("adjustment_invalid", detail=f"{it.get('item')} {it.get('period')}: value")
        if not value.is_finite():
            raise AppError("adjustment_invalid", detail=f"{it.get('item')} {it.get('period')}: value")
        out.append(Adjustment(str(it.get("item") or ""), str(it.get("period") or ""), value,
                              str(it.get("reason") or "").strip()[:2000], (str(it.get("basis") or "").strip()[:2000]) or None))
    try:
        return validate(out, rs)
    except AdjustmentError as exc:
        raise AppError("adjustment_invalid", detail=str(exc))


def save(db: Session, filer: Filer, ticker: str, kind: str, user: User, items: list[dict], note: Optional[str],
         base_version: int, central_version: Optional[int] = None) -> AdjustmentPlan:
    """Save a new version. ``base_version`` must be the version the editor started from (0 for a new plan):
    a save over someone else's newer version is refused rather than silently overwriting it."""
    std, rs, _ = statements.standardized(db, filer, ticker=ticker)
    adjs = _parse(items, rs)
    plan = get_plan(db, ticker, kind, user)
    if plan is None:
        central = get_plan(db, ticker, "central", user) if kind == "personal" else None
        plan = AdjustmentPlan(ticker=ticker, kind=kind, user_id=user.id if kind == "personal" else None, current_version=0,
                              source_plan_id=central.id if central else None,
                              source_version=central.current_version if central else None)
        db.add(plan)
        db.flush()
    if base_version != plan.current_version:
        raise AppError("plan_conflict", version=plan.current_version)
    if kind == "personal" and central_version is not None:
        central = get_plan(db, ticker, "central", user)
        if central is None or not 0 <= central_version <= central.current_version:
            raise AppError("adjustment_invalid", detail="central_version")
        plan.source_plan_id, plan.source_version = central.id, central_version
    # an adjustment unchanged from the plan's previous version, or copied unchanged from the central plan,
    # keeps who made it, when, and the standard value it replaced
    prev: dict[tuple[str, str], AdjustmentRow] = {}
    if kind == "personal" and plan.source_plan_id:
        src = db.get(AdjustmentPlan, plan.source_plan_id)
        if src is not None:
            prev.update({(r.item, r.period): r for r in rows(db, version_row(db, src, plan.source_version or 0))})
    prev.update({(r.item, r.period): r for r in rows(db, version_row(db, plan))})
    now = dt.datetime.now(dt.timezone.utc)
    ver = AdjustmentPlanVersion(plan_id=plan.id, version=plan.current_version + 1, created_by=user.id,
                                created_by_name=user.display_name, note=(note or "").strip()[:2000] or None,
                                central_version=plan.source_version if kind == "personal" else None, created_at=now)
    db.add(ver)
    db.flush()
    for a in adjs:
        old = prev.get((a.item, a.period))
        same = old is not None and (old.value, old.reason, old.basis) == (a.value, a.reason, a.basis)
        cur = std.get(a.item, a.period)
        db.add(AdjustmentRow(version_id=ver.id, item=a.item, period=a.period, value=a.value, reason=a.reason, basis=a.basis,
                             original_value=old.original_value if same else (cur.value if cur else None),
                             adjusted_by=old.adjusted_by if same else user.display_name,
                             adjusted_at=old.adjusted_at if same else now))
    plan.current_version = ver.version
    plan.updated_at = now
    db.flush()
    return plan


def version_out(db: Session, plan: AdjustmentPlan, ver: Optional[AdjustmentPlanVersion]) -> dict:
    return {"plan_id": plan.id, "kind": plan.kind, "version": ver.version if ver else 0,
            "created_by_name": ver.created_by_name if ver else None,
            "created_at": ver.created_at.isoformat(timespec="seconds") if ver else None,
            "note": ver.note if ver else None, "central_version": ver.central_version if ver else None,
            "adjustments": [{"item": r.item, "period": r.period, "value": format(r.value, "f"),
                             "original_value": format(r.original_value, "f") if r.original_value is not None else None,
                             "reason": r.reason, "basis": r.basis, "adjusted_by": r.adjusted_by,
                             "adjusted_at": r.adjusted_at.isoformat(timespec="seconds")} for r in rows(db, ver)]}


def plan_out(db: Session, plan: Optional[AdjustmentPlan]) -> Optional[dict]:
    if plan is None:
        return None
    versions = db.scalars(select(AdjustmentPlanVersion).where(AdjustmentPlanVersion.plan_id == plan.id)
                          .order_by(AdjustmentPlanVersion.version.desc())).all()
    counts = {v.id: 0 for v in versions}
    for vid in db.scalars(select(AdjustmentRow.version_id).where(AdjustmentRow.version_id.in_(list(counts)))):
        counts[vid] += 1
    cur = next((v for v in versions if v.version == plan.current_version), None)
    return {"id": plan.id, "kind": plan.kind, "current_version": plan.current_version, "source_version": plan.source_version,
            "versions": [{"version": v.version, "created_by_name": v.created_by_name,
                          "created_at": v.created_at.isoformat(timespec="seconds"), "note": v.note,
                          "central_version": v.central_version, "adjustments": counts[v.id]} for v in versions],
            "current": version_out(db, plan, cur)}


def overview(db: Session, ticker: str, user: User) -> dict:
    central = get_plan(db, ticker, "central", user)
    personal = get_plan(db, ticker, "personal", user)
    updated = bool(central and personal and (personal.source_version or 0) < central.current_version)
    return {"ticker": ticker, "central": plan_out(db, central), "personal": plan_out(db, personal), "central_updated": updated}

"""Operations for administrators: request limits and the state of each external data source."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import limits
from ..auth import Principal, require_admin
from ..db import get_db
from ..jobs import store
from ..schemas import LimitsIn, LimitsOut, OpsStatus

router = APIRouter(tags=["admin"])


@router.get("/admin/limits", response_model=LimitsOut, summary="請求與估值佇列的限制（管理員）")
def get_limits(_: Principal = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    return {**limits.get(db), "defaults": limits.DEFAULTS}


@router.put("/admin/limits", response_model=LimitsOut, summary="修改限制（管理員；超出範圍的值會被收窄）")
def put_limits(body: LimitsIn, _: Principal = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    return {**limits.update(db, body.model_dump(exclude_none=True)), "defaults": limits.DEFAULTS}


@router.get("/admin/ops", response_model=OpsStatus, summary="數據源熔斷狀態與估值佇列（管理員）")
def ops_status(_: Principal = Depends(require_admin)) -> dict:
    from src.fetch import guard

    active = store().active()
    return {"providers": guard.status(), "queue": {"active": len(active),
                                                   "running": sum(1 for j in active if j["status"] == "running"),
                                                   "queued": sum(1 for j in active if j["status"] == "queued")}}

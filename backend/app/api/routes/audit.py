from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.connectors.base import QueryResult
from app.core.database import get_db
from app.models.audit import AuditLog
from app.models.user import User
from app.schemas.audit import AuditLogOut
from app.services import audit_service, result_service

router = APIRouter(prefix="/audit", tags=["audit"])


def _query(user_id: int | None, action: str | None, cap: int):
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(cap)
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    return stmt


@router.get("/logs", response_model=list[AuditLogOut])
def list_logs(
    user_id: int | None = None,
    action: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    return list(db.scalars(_query(user_id, action, min(limit, 1000))))


@router.get("/logs/export")
def export_logs(
    request: Request,
    user_id: int | None = None,
    action: str | None = None,
    limit: int = 50000,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """按当前筛选导出审计日志 CSV(管理端合规能力)。导出动作本身也会被审计。"""
    rows = list(db.scalars(_query(user_id, action, min(limit, 200000))))
    result = QueryResult(
        columns=["id", "时间", "用户ID", "用户名", "动作", "资源类型", "资源ID", "IP", "详情"],
        rows=[
            (
                r.id,
                r.created_at.isoformat(sep=" ") if r.created_at else "",
                r.user_id, r.user_name, r.action, r.resource_type, r.resource_id, r.ip,
                json.dumps(r.detail or {}, ensure_ascii=False),
            )
            for r in rows
        ],
    )
    audit_service.log(
        db, user=admin, action="export_audit", resource_type="audit_log",
        detail={"count": len(rows), "filter": {"user_id": user_id, "action": action}},
        ip=request.client.host if request.client else None,
    )
    # 复用统一 CSV 序列化(含 UTF-8 BOM + None→空,便于 Excel 直接打开)
    return Response(
        content=result_service.to_csv_bytes(result),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=audit_logs.csv"},
    )

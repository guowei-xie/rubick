from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, require_admin
from app.connectors.base import QueryResult
from app.core.database import get_db
from app.models.audit import (
    ACTION_EXPORT_AUDIT,
    ACTION_META,
    action_label,
    RESOURCE_AUDIT_LOG,
    RESOURCE_META,
    AuditLog,
)
from app.models.user import User
from app.schemas.audit import AuditLogOut, AuditLogPage, AuditMetaOut
from app.services import audit_service, result_service

router = APIRouter(prefix="/audit", tags=["audit"])


def _naive(dt: datetime | None) -> datetime | None:
    """审计表 created_at 是朴素本地时间;带时区的入参统一转本地再去掉 tzinfo,
    否则「带时区 vs 朴素列」的比较会静默出错。"""
    return dt if dt is None or dt.tzinfo is None else dt.astimezone().replace(tzinfo=None)


def _conditions(
    user_id: int | None,
    action: str | None,
    resource_type: str | None,
    start: datetime | None,
    end: datetime | None,
) -> list:
    """筛选条件,列表/计数/导出三处共用 —— 保证「导出=当前筛选」不漂移。"""
    conds = []
    if user_id:
        conds.append(AuditLog.user_id == user_id)
    if action:
        conds.append(AuditLog.action == action)
    if resource_type:
        conds.append(AuditLog.resource_type == resource_type)
    if start:
        conds.append(AuditLog.created_at >= _naive(start))
    if end:
        conds.append(AuditLog.created_at <= _naive(end))
    return conds


@router.get("/meta", response_model=AuditMetaOut)
def audit_meta(_: User = Depends(require_admin)):
    """动作码与资源类型的中文标签。本身是静态元数据,不记审计。"""
    return AuditMetaOut(
        actions=[
            {"code": code, "label": label, "group": group}
            for code, (label, group) in ACTION_META.items()
        ],
        resource_types=[{"code": code, "label": label} for code, label in RESOURCE_META.items()],
    )


@router.get("/logs", response_model=AuditLogPage)
def list_logs(
    user_id: int | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    offset: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    conds = _conditions(user_id, action, resource_type, start, end)
    total = db.scalar(select(func.count()).select_from(AuditLog).where(*conds)) or 0
    # 按 id 而非 created_at 排序:id 单调唯一,同秒内多条(submit_query + run_query)
    # 翻页时不会重复或漏行
    stmt = (
        select(AuditLog)
        .where(*conds)
        .order_by(AuditLog.id.desc())
        .offset(max(offset, 0))
        .limit(min(max(limit, 1), 1000))
    )
    return AuditLogPage(
        total=total, items=[AuditLogOut.model_validate(r) for r in db.scalars(stmt)]
    )


@router.get("/logs/export")
def export_logs(
    user_id: int | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 50000,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    ip: str | None = Depends(client_ip),
):
    """按当前筛选导出审计日志 CSV(管理端合规能力)。导出动作本身也会被审计。"""
    conds = _conditions(user_id, action, resource_type, start, end)
    rows = list(
        db.scalars(
            select(AuditLog).where(*conds).order_by(AuditLog.id.desc()).limit(min(limit, 200000))
        )
    )
    result = QueryResult(
        columns=[
            "id", "时间", "用户ID", "用户名", "动作", "动作(中文)",
            "资源类型", "资源ID", "资源名称", "IP", "详情",
        ],
        rows=[
            (
                r.id,
                r.created_at.isoformat(sep=" "),
                r.user_id, r.user_name, r.action, action_label(r.action),
                r.resource_type, r.resource_id, r.resource_name, r.ip,
                json.dumps(r.detail or {}, ensure_ascii=False),
            )
            for r in rows
        ],
    )
    audit_service.log(
        db, user=admin, action=ACTION_EXPORT_AUDIT, resource_type=RESOURCE_AUDIT_LOG,
        detail={
            "count": len(rows),
            "filter": {
                "user_id": user_id, "action": action, "resource_type": resource_type,
                "start": start.isoformat() if start else None,
                "end": end.isoformat() if end else None,
            },
        },
        ip=ip,
    )
    # 复用统一 CSV 序列化(含 UTF-8 BOM + None→空,便于 Excel 直接打开)
    return Response(
        content=result_service.to_csv_bytes(result),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=audit_logs.csv"},
    )

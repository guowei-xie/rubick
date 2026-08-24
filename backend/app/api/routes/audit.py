from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, require_admin
from app.core.database import SessionLocal, get_db
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


_EXPORT_COLUMNS = [
    "id", "时间", "用户ID", "用户名", "用户邮箱(当前)", "动作", "动作(中文)",
    "资源类型", "资源ID", "资源名称", "IP", "详情",
]
# 一次从游标取多少行进内存。峰值内存只与它有关,与总行数无关 —— 这正是流式的全部意义。
_EXPORT_BATCH = 500
_EXPORT_HARD_CAP = 200000


def _export_rows(conds, limit: int):
    """按筛选**流式**产出导出行。limit 由 export_logs 夹取过硬上限,这里不再重复夹。
    自管独立会话:响应体是惰性生成的,而 Depends(get_db) 那个会话在响应开始发送之前
    就已经被拆解关掉了,借用它会在生成到一半时炸。"""
    db = SessionLocal()
    try:
        # 邮箱按 user_id 现查现拼,**不在 audit_logs 上加快照列**:audit_logs 是全库写入量
        # 最大的表,为一个可推导字段加一列 VARCHAR(255) 不划算;用户表是个位数,一次查完。
        # 代价是它是「当前值」而非写入时快照(user_name 是快照),故列名里已写明。
        emails = dict(db.execute(select(User.id, User.email)).all())
        stmt = (
            select(AuditLog)
            .where(*conds)
            .order_by(AuditLog.id.desc())
            .limit(limit)
            .execution_options(yield_per=_EXPORT_BATCH)
        )
        for r in db.scalars(stmt):
            yield (
                r.id,
                r.created_at.isoformat(sep=" "),
                r.user_id, r.user_name, emails.get(r.user_id), r.action, action_label(r.action),
                r.resource_type, r.resource_id, r.resource_name, r.ip,
                json.dumps(r.detail or {}, ensure_ascii=False),
            )
    finally:
        db.close()


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
    """按当前筛选导出审计日志 CSV(管理端合规能力)。导出动作本身也会被审计。

    **流式产出**,峰值内存与行数无关:这个表可以有二十万行、每行 detail 里还带着一段最长
    20000 字符的 SQL 原文,而后端是单进程 uvicorn(同时托管 SPA 与 /api)—— 一次性拼装
    等于让一次正常的管理员点击去赌整个站点的内存。

    **但一次导出仍有行数上限**:最多 `limit` 行(默认 50000,硬上限 200000),超出时只给
    按 id 倒序的**最新那批**,而 CSV 里不会有任何截断标记。管理端**显式传** limit=50000
    (AuditPage.tsx 的 EXPORT_MAX_ROWS,与这里的默认值刻意同数;改导出规模两处要一起改),
    所以点一次「导出 CSV」拿到的就是最新 5 万条 —— 要全量请缩小时间范围分批导。
    这一句是给合规读的:把 5 万行当「这段时间的全部审计」会判错。
    """
    conds = _conditions(user_id, action, resource_type, start, end)
    capped = min(limit, _EXPORT_HARD_CAP)  # 硬上限只夹这一次,审计 detail 与查询共用同一个数
    # 计数与审计都在**开始吐数据之前**做完:响应体是惰性的,把合规记录写进生成器
    # 就等于把它押在「客户端有没有读完」上。
    total = db.scalar(select(func.count()).select_from(AuditLog).where(*conds)) or 0
    audit_service.log(
        db, user=admin, action=ACTION_EXPORT_AUDIT, resource_type=RESOURCE_AUDIT_LOG,
        detail={
            "count": min(total, capped),
            "filter": {
                "user_id": user_id, "action": action, "resource_type": resource_type,
                "start": start.isoformat() if start else None,
                "end": end.isoformat() if end else None,
            },
        },
        ip=ip,
    )
    # 复用统一 CSV 序列化(含 UTF-8 BOM + None→空,便于 Excel 直接打开)
    return StreamingResponse(
        result_service.iter_csv_bytes(_EXPORT_COLUMNS, _export_rows(conds, capped)),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=audit_logs.csv"},
    )

"""审计写入。所有可审计动作统一走这里,只追加不更新/删除(append-only 为应用层约定;
如需 DB 层强制不可篡改,应另加只写账号/触发器/哈希链,见 PRD「实现现状」)。

**埋点位置约定**:审计写入放在**路由层**——那里同时拿得到操作人(Depends)、
`Request`(→ `client_ip`)和请求体(→ 变更前后),且业务变更此时已 commit,
`log()` 内部的 commit 不会误提交半成品。

唯一例外是「请求外发生」的动作:`run_query` / `run_query_failed` 由 worker
(`app/worker.py`)写入,那里根本没有 Request,只能留在 query_service。

审计写入失败会向上抛错(不吞异常):审计是合规控制项,静默丢一条记录比返回 500 更糟。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.audit import ACTION_DOWNLOAD, RESOURCE_JOB, AuditLog, DownloadEvent
from app.models.user import User

# 绝不允许进入审计 detail 的字段名。
# 注意 DataSource.password 是 EncryptedText —— 读属性即拿到**明文**,而 detail 是
# 未加密的 JSON 列且会被导出成 CSV,因此必须在此拦死。
_SECRET_FIELDS = frozenset(
    {"password", "passwd", "secret", "token", "feishu_token", "feishu_refresh_token"}
)

_REDACTED = "***"


def _redact(value):
    """递归把敏感键的值替换成 ***。在 log() 内无条件调用,
    这样任何调用点(包括以后新加的)都不可能把凭证写进审计表。"""
    if isinstance(value, dict):
        return {
            k: (_REDACTED if k.lower() in _SECRET_FIELDS else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def snapshot(obj, fields: tuple[str, ...]) -> dict:
    """取对象若干字段的浅快照,用于「变更前后」对比。

    **必须在业务修改之前调用**:SQLAlchemy 是就地改对象,commit 后再读拿到的是新值;
    对将被删除的对象更要先快照(delete+commit 之后属性访问会抛异常)。
    detail 是 JSON 列,故非标量一律 str() 兜底。
    """
    out: dict = {}
    for f in fields:
        if f.lower() in _SECRET_FIELDS:  # 防御:调用方误传也不落密
            continue
        v = getattr(obj, f, None)
        out[f] = v if v is None or isinstance(v, (str, int, float, bool, list, dict)) else str(v)
    return out


def diff(before: dict, after: dict) -> dict:
    """{字段: {"from": 旧值, "to": 新值}},只保留真正变化的字段;无变化返回 {}。"""
    return {
        k: {"from": before.get(k), "to": after.get(k)}
        for k in {*before, *after}
        if before.get(k) != after.get(k) and k.lower() not in _SECRET_FIELDS
    }


def log(
    db: Session,
    *,
    user: User | None,
    action: str,
    resource_type: str | None = None,
    resource_id: str | int | None = None,
    resource_name: str | None = None,
    detail: dict | None = None,
    ip: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        user_id=user.id if user else None,
        user_name=user.name if user else None,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        resource_name=resource_name,
        detail=_redact(detail or {}),
        ip=ip,
    )
    db.add(entry)
    db.commit()
    return entry


def log_download(
    db: Session, *, user: User, job_id: int, filename: str | None, row_count: int | None, ip: str | None
) -> None:
    db.add(
        DownloadEvent(
            user_id=user.id, job_id=job_id, filename=filename, row_count=row_count, ip=ip
        )
    )
    log(
        db,
        user=user,
        action=ACTION_DOWNLOAD,
        resource_type=RESOURCE_JOB,
        resource_id=job_id,
        resource_name=filename,
        detail={"filename": filename, "row_count": row_count},
        ip=ip,
    )

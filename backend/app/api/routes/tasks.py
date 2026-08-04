"""统一「任务列表」:任务(SqlTemplate)+ 按角色收窄 + 能力标记,以及任务的运行记录。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user
from app.core.database import get_db
from app.core.exceptions import NotFoundError, PermissionDeniedError, RubicError
from app.models.audit import ACTION_TASK_ENUM_REFRESH
from app.models.permission import ACTION_RUN, ACTION_VIEW, RESOURCE_TEMPLATE
from app.models.query_job import QueryJob
from app.models.template import STATUS_PUBLISHED, SqlTemplate, TemplateVersion
from app.models.user import User
from app.schemas.common import ParamDef
from app.schemas.query import JobOut, TaskOut
from app.schemas.template import EnumRefreshIn, SharedEnumValuesOut
from app.services import audit_service, enum_cache_service, permission_service

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=list[TaskOut])
def list_tasks(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """管理者(管理员/开发者)看全部(彼此可见可编辑);普通用户只看被授权的已上线任务。"""
    stmt = select(SqlTemplate).order_by(SqlTemplate.id.desc())
    if permission_service.is_manager(user):
        rows = list(db.scalars(stmt))
    else:
        visible = permission_service.visible_template_ids(db, user) or set()
        published_visible = and_(SqlTemplate.status == STATUS_PUBLISHED, SqlTemplate.id.in_(visible or {-1}))
        rows = list(db.scalars(stmt.where(published_visible)))

    runnable = permission_service.action_template_ids(db, user, ACTION_RUN)  # None=管理员(全部)
    ids = [t.id for t in rows]
    authorized = permission_service.authorized_run_users(db, ids)  # 一次批量查
    # 各任务最后一次运行时间(含试跑),一次批量聚合,避免 N+1
    last_runs: dict[int, object] = {}
    if ids:
        for tid, ts in db.execute(
            select(QueryJob.template_id, func.max(QueryJob.created_at))
            .where(QueryJob.template_id.in_(ids))
            .group_by(QueryJob.template_id)
        ):
            last_runs[tid] = ts
    out: list[TaskOut] = []
    for t in rows:
        can_manage = permission_service.is_template_owner(user, t)
        can_run = bool(
            t.status == STATUS_PUBLISHED
            and t.published_version_id is not None
            and (can_manage or runnable is None or t.id in runnable)
        )
        out.append(
            TaskOut(
                id=t.id, name=t.name, description=t.description,
                status=t.status, datasource_id=t.datasource_id,
                datasource_name=t.datasource_name, engine=t.engine,
                author_id=t.author_id, author_name=t.author_name,
                published_version_id=t.published_version_id, created_at=t.created_at,
                updated_at=t.updated_at, last_run_at=last_runs.get(t.id),
                timeout_seconds=t.timeout_seconds,
                can_manage=can_manage, can_run=can_run,
                authorized_users=authorized.get(t.id, []),
            )
        )
    return out


@router.get("/{template_id}/jobs", response_model=list[JobOut])
def task_run_records(
    template_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """任务的运行记录。管理者(管理员/开发者)与作者看全部;其他人只看自己跑过的。"""
    tmpl = db.get(SqlTemplate, template_id)
    if tmpl is None:
        raise NotFoundError("任务不存在")
    stmt = (
        select(QueryJob).where(QueryJob.template_id == template_id)
        .order_by(QueryJob.id.desc()).limit(200)
    )
    if not permission_service.is_template_owner(user, tmpl):
        stmt = stmt.where(QueryJob.user_id == user.id)
    return list(db.scalars(stmt))


def _published_param(
    db: Session, user: User, template_id: int, variable: str, *, action: str
) -> tuple[SqlTemplate, dict]:
    """读/更新共享候选值的共同前置:任务 → 权限 → 已上线版本 → 该变量的定义。"""
    tmpl = db.get(SqlTemplate, template_id)
    if tmpl is None:
        raise NotFoundError("任务不存在")
    # can() 内部已给管理者与作者放行,不必再叠一次 owner 判定
    if not permission_service.can(db, user, action, RESOURCE_TEMPLATE, template_id):
        raise PermissionDeniedError("无权访问该任务")
    ver = db.get(TemplateVersion, tmpl.published_version_id) if tmpl.published_version_id else None
    if ver is None:
        raise NotFoundError("任务未上线")
    pdef = next((p for p in (ver.params or []) if p.get("name") == variable), None)
    if pdef is None:
        raise NotFoundError("变量不存在")
    # 判定口径与服务层同源,避免两处漂移(见 ParamDef.has_enum_candidates)
    if not ParamDef.dict_has_enum_candidates(pdef):
        raise RubicError("该变量未配置枚举值获取 SQL,请直接输入或上传列表")
    return tmpl, pdef


@router.get("/{template_id}/enum-values", response_model=SharedEnumValuesOut)
def task_enum_values(
    template_id: int,
    variable: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """读该变量的共享候选值。**纯读缓存,不执行任何 SQL** —— 打开填参抽屉靠它秒开。

    没有候选或候选已作废(作者改了 enum_sql / 数据源)都不是错误,回空列表 + cached=False,
    由业务用户点「获取枚举值」触发一次真实取数(见 refresh_task_enum_values)。
    """
    tmpl, pdef = _published_param(db, user, template_id, variable, action=ACTION_VIEW)
    return enum_cache_service.read(db, tmpl, pdef)


@router.post("/{template_id}/enum-values/refresh", response_model=SharedEnumValuesOut)
def refresh_task_enum_values(
    template_id: int,
    data: EnumRefreshIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    ip: str | None = Depends(client_ip),
):
    """业务用户手动更新枚举候选值:真跑一次 enum_sql,结果回写并对该任务所有人生效。

    要 run 权限(按钮只出现在取数抽屉里,能打开就意味着能跑)。
    """
    tmpl, pdef = _published_param(db, user, template_id, data.variable, action=ACTION_RUN)
    out = enum_cache_service.refresh(db, tmpl, pdef, user)
    # 候选值本身不进审计 detail:审计可导出 CSV,没必要把业务维度值再抄一份
    audit_service.log(
        db, user=user, action=ACTION_TASK_ENUM_REFRESH,
        resource_type=RESOURCE_TEMPLATE, resource_id=tmpl.id, resource_name=tmpl.name,
        detail={
            "variable": data.variable,
            "value_count": len(out.values),
            "truncated": out.truncated,
            "duration_ms": out.duration_ms,
            "reused": out.reused,
        },
        ip=ip,
    )
    return out

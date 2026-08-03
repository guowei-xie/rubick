"""统一「任务列表」:项目(模板)+ 按角色收窄 + 能力标记,以及项目的运行记录。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.exceptions import NotFoundError, PermissionDeniedError, RubicError
from app.models.permission import ACTION_RUN
from app.models.query_job import QueryJob
from app.models.template import STATUS_PUBLISHED, SqlTemplate, TemplateVersion
from app.models.user import User
from app.schemas.query import JobOut, TaskOut
from app.schemas.template import ValueListOut
from app.services import permission_service, template_service

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=list[TaskOut])
def list_tasks(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """管理者(管理员/开发者)看全部(彼此可见可编辑);普通用户只看被授权的已发布。"""
    stmt = select(SqlTemplate).order_by(SqlTemplate.id.desc())
    if permission_service.is_manager(user):
        rows = list(db.scalars(stmt))
    else:
        visible = permission_service.visible_template_ids(db, user) or set()
        published_visible = and_(SqlTemplate.status == STATUS_PUBLISHED, SqlTemplate.id.in_(visible or {-1}))
        rows = list(db.scalars(stmt.where(published_visible)))

    runnable = permission_service.action_template_ids(db, user, ACTION_RUN)  # None=管理员(全部)
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
                timeout_seconds=t.timeout_seconds,
                can_manage=can_manage, can_run=can_run,
            )
        )
    return out


@router.get("/{template_id}/jobs", response_model=list[JobOut])
def task_run_records(
    template_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """项目运行记录。管理员/作者看全部;其他人只看自己跑过的。"""
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


@router.get("/{template_id}/enum-values", response_model=ValueListOut)
def task_enum_values(
    template_id: int,
    variable: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """业务填参时「获取枚举值」:跑该变量在已发布版本里配置的 enum_sql,返回候选值。"""
    tmpl = db.get(SqlTemplate, template_id)
    if tmpl is None:
        raise NotFoundError("任务不存在")
    if not (
        permission_service.is_template_owner(user, tmpl)
        or permission_service.can(db, user, "view", "template", template_id)
    ):
        raise PermissionDeniedError("无权访问该任务")
    ver = db.get(TemplateVersion, tmpl.published_version_id) if tmpl.published_version_id else None
    if ver is None:
        raise NotFoundError("任务未发布")
    pdef = next((p for p in (ver.params or []) if p.get("name") == variable), None)
    if pdef is None:
        raise NotFoundError("变量不存在")
    if not pdef.get("enum_sql"):
        raise RubicError("该变量未配置枚举值获取 SQL,请直接输入或上传列表")
    return template_service.run_value_query(db, tmpl.datasource_id, pdef["enum_sql"])

"""统一「任务列表」:任务(SqlTemplate)+ 按团队收窄 + 能力标记,以及任务的运行记录、
任务的「编辑人」与所属团队维护。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, require_admin
from app.core.database import get_db
from app.core.exceptions import NotFoundError, PermissionDeniedError, RubicError
from app.models.audit import (
    ACTION_TASK_EDIT_GRANT,
    ACTION_TASK_EDIT_REVOKE,
    ACTION_TASK_ENUM_REFRESH,
    ACTION_TASK_TEAM_TRANSFER,
)
from app.models.permission import ACTION_RUN, ACTION_VIEW, RESOURCE_TEMPLATE
from app.models.query_job import QueryJob
from app.models.template import SqlTemplate, TemplateVersion
from app.models.user import User
from app.schemas.common import ParamDef
from app.schemas.query import JobOut, TaskOut
from app.schemas.team import EditorIn, TaskEditorOut, TaskTeamIn
from app.schemas.template import EnumRefreshIn, SharedEnumValuesOut
from app.services import (
    audit_service,
    credential_service,
    enum_cache_service,
    permission_service,
    team_service,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _load(db: Session, template_id: int) -> SqlTemplate:
    """取任务或 404。本文件所有按 id 取任务的入口都走它。"""
    tmpl = db.get(SqlTemplate, template_id)
    if tmpl is None:
        raise NotFoundError("任务不存在")
    return tmpl


@router.get("", response_model=list[TaskOut])
def list_tasks(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """平台管理员看全部;开发者看**所属团队**的全部任务(含草稿/已下线);
    普通用户只看被授权的已上线任务。

    可见性下推到 SQL(visible_condition),逐行的能力标记走内存里的 scope —— 落地页要对
    几百行逐行判定,逐行查库必然 N+1(见 permission_service.TeamScope)。
    """
    scope = permission_service.team_scope(db, user)
    stmt = select(SqlTemplate).order_by(SqlTemplate.id.desc())
    cond = permission_service.visible_condition(scope)
    rows = list(db.scalars(stmt if cond is None else stmt.where(cond)))

    ids = [t.id for t in rows]
    authorized = permission_service.authorized_run_users(db, ids)  # 一次批量查
    # 任务所属团队登记过该数据源的取数账号吗,一次批量算完(逐个查会 N+1)。
    # 传已加载的行而不是 id —— 团队与数据源都在手上,不必让服务再查一遍
    cred_ready = credential_service.ready_template_ids(db, rows)
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
        out.append(
            TaskOut(
                id=t.id, name=t.name, description=t.description,
                status=t.status, datasource_id=t.datasource_id,
                datasource_name=t.datasource_name, engine=t.engine,
                author_id=t.author_id, author_name=t.author_name,
                team_id=t.team_id, team_name=t.team_name,
                published_version_id=t.published_version_id, created_at=t.created_at,
                updated_at=t.updated_at, last_run_at=last_runs.get(t.id),
                timeout_seconds=t.timeout_seconds,
                can_manage=permission_service.can_edit(scope, t),
                can_run=permission_service.can_run(scope, t),
                credential_ready=t.id in cred_ready,
                authorized_users=authorized.get(t.id, []),
            )
        )
    return out


@router.get("/{template_id}/jobs", response_model=list[JobOut])
def task_run_records(
    template_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """任务的运行记录。**团队内部人**(同团队成员 / 平台管理员)看全部;
    被授权的业务使用者只看自己跑过的。"""
    tmpl = _load(db, template_id)
    scope = permission_service.team_scope(db, user)
    if not permission_service.can_view(scope, tmpl):
        raise PermissionDeniedError("无权查看该任务")
    stmt = (
        select(QueryJob).where(QueryJob.template_id == template_id)
        .order_by(QueryJob.id.desc()).limit(200)
    )
    if not permission_service.is_insider(scope, tmpl):
        stmt = stmt.where(QueryJob.user_id == user.id)
    return list(db.scalars(stmt))


def _published_param(
    db: Session, user: User, template_id: int, variable: str, *, action: str
) -> tuple[SqlTemplate, dict]:
    """读/更新共享候选值的共同前置:任务 → 权限 → 已上线版本 → 该变量的定义。"""
    tmpl = _load(db, template_id)
    # can() 内部已给平台管理员与团队内部人放行,不必再叠一次判定
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


# ---------------------------------------------------------------- 任务编辑权(团队内)
# 刻意不走 /api/permissions:那个入口面向业务使用者(view/run/download,从飞书通讯录选人),
# 这里面向团队成员(edit,只能从本团队成员里选)。合成一个入口会让业务授权变成一条提权后门。


@router.get("/{template_id}/editors", response_model=list[TaskEditorOut])
def list_task_editors(
    template_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """该任务的「编辑人」名单。含隐式(作者 / 团队管理员,不可撤销)与显式授权两类,
    每项带 source 供 UI 区分。"""
    tmpl = _load(db, template_id)
    if not permission_service.can_view(permission_service.team_scope(db, user), tmpl):
        raise PermissionDeniedError("无权查看该任务")
    return permission_service.editors_of(db, tmpl)


@router.post("/{template_id}/editors")
def grant_task_editor(
    template_id: int,
    data: EditorIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    ip: str | None = Depends(client_ip),
):
    """把某任务的编辑权授予一名**本团队成员**。仅团队管理员或平台管理员可操作。"""
    tmpl = _load(db, template_id)
    team = team_service.require_team_admin_of_template(db, user, tmpl)
    target = db.get(User, data.user_id)
    if target is None:
        raise NotFoundError("用户不存在")
    if not team_service.is_member(db, target, team.id):
        raise RubicError(f"{target.name} 不是团队《{team.name}》的成员,不能授予编辑权")
    created = permission_service.grant_edit(
        db, template_id=tmpl.id, user_id=target.id, granted_by=user.id
    )
    audit_service.log(
        db, user=user, action=ACTION_TASK_EDIT_GRANT,
        resource_type=RESOURCE_TEMPLATE, resource_id=tmpl.id, resource_name=tmpl.name,
        detail={
            "team_id": team.id, "team_name": team.name,
            "target_user_id": target.id, "target_user_name": target.name,
            # grant 幂等:重复授予不新建行。false 即说明这次是重复操作
            "created": created,
        },
        ip=ip,
    )
    return {"ok": True, "created": created}


@router.delete("/{template_id}/editors/{user_id}")
def revoke_task_editor(
    template_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    ip: str | None = Depends(client_ip),
):
    tmpl = _load(db, template_id)
    team = team_service.require_team_admin_of_template(db, user, tmpl)
    target = db.get(User, user_id)
    removed = permission_service.revoke_edit(db, template_id=tmpl.id, user_id=user_id)
    if not removed:
        # 什么都没发生就不记日志,否则留下一条误导性的「撤销」
        return {"ok": True, "removed": False}
    audit_service.log(
        db, user=user, action=ACTION_TASK_EDIT_REVOKE,
        resource_type=RESOURCE_TEMPLATE, resource_id=tmpl.id, resource_name=tmpl.name,
        detail={
            "team_id": team.id, "team_name": team.name,
            "target_user_id": user_id,
            "target_user_name": target.name if target else None,
        },
        ip=ip,
    )
    return {"ok": True, "removed": True}


# ---------------------------------------------------------------- 转移所属团队


@router.put("/{template_id}/team")
def transfer_task_team(
    template_id: int,
    data: TaskTeamIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    ip: str | None = Depends(client_ip),
):
    """把任务转移到另一个团队。**仅平台管理员** —— 这同时改变任务的可见范围与取数身份,
    是一次跨组织的治理动作,不是任务编辑的一部分(故也不从 PUT /templates/{id} 进)。
    """
    tmpl = _load(db, template_id)
    target = team_service.get_team(db, data.team_id)
    from_name = tmpl.team_name
    # 撤销原团队的编辑权授权由 transfer_template 一并完成(规则只在那里写一次)
    from_id, revoked = team_service.transfer_template(db, tmpl, target)
    audit_service.log(
        db, user=user, action=ACTION_TASK_TEAM_TRANSFER,
        resource_type=RESOURCE_TEMPLATE, resource_id=tmpl.id, resource_name=tmpl.name,
        detail={
            "from_team_id": from_id, "from_team_name": from_name,
            "to_team_id": target.id, "to_team_name": target.name,
            # 原团队里的「指定任务编辑权」随之失效:它们的前提(同团队)已不成立
            "revoked_editor_user_ids": revoked,
        },
        ip=ip,
    )
    return {"ok": True, "team_id": target.id, "team_name": target.name}

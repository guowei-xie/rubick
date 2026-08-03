"""授权判定与授予。

有效权限 = 个人授权。管理员全通;模板作者对自己的模板全通。
Phase 2 再加 RBAC 角色主体与拒绝优先。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.permission import (
    ACTION_VIEW,
    RESOURCE_TEMPLATE,
    SUBJECT_USER,
    Permission,
)  # noqa: F401
from app.models.template import SqlTemplate
from app.models.user import ROLE_ADMIN, User


def _subject_filters(user: User):
    """当前用户对应的所有授权主体 (type, id)。当前仅个人用户。"""
    return [(SUBJECT_USER, str(user.id))]


def can_access_job(user: User, job) -> bool:
    """任务结果的访问权:本人或管理员。集中此处避免各处重复写(含 admin 短路)。"""
    return user.role == ROLE_ADMIN or job.user_id == user.id


def is_template_owner(user: User, tmpl) -> bool:
    """管理员恒真;否则需为该模板作者。作用于已加载的模板对象,避免重复查询。"""
    return user.role == ROLE_ADMIN or bool(tmpl and tmpl.author_id == user.id)


def owns_template(db: Session, user: User, template_id: int | str) -> bool:
    """限定商分只能管自己的模板;管理员恒真。"""
    if user.role == ROLE_ADMIN:
        return True
    return is_template_owner(user, db.get(SqlTemplate, int(template_id)))


def owned_template_ids(db: Session, user: User) -> list[int]:
    return list(db.scalars(select(SqlTemplate.id).where(SqlTemplate.author_id == user.id)))


def can(db: Session, user: User, action: str, resource_type: str, resource_id: int | str) -> bool:
    if user.role == ROLE_ADMIN:
        return True
    # 模板作者对自己的模板放行
    if resource_type == RESOURCE_TEMPLATE:
        tmpl = db.get(SqlTemplate, int(resource_id))
        if tmpl and tmpl.author_id == user.id:
            return True

    for stype, sid in _subject_filters(user):
        exists = db.scalar(
            select(Permission.id).where(
                Permission.subject_type == stype,
                Permission.subject_id == sid,
                Permission.resource_type == resource_type,
                Permission.resource_id == str(resource_id),
                Permission.action == action,
            )
        )
        if exists:
            return True
    return False


def action_template_ids(db: Session, user: User, action: str) -> set[int] | None:
    """用户对模板可执行 action(view/run/download)的 id 集合;管理员返回 None 表示全部。
    作者对自己的模板拥有全部动作。"""
    if user.role == ROLE_ADMIN:
        return None
    ids: set[int] = set(owned_template_ids(db, user))
    for stype, sid in _subject_filters(user):
        rows = db.scalars(
            select(Permission.resource_id).where(
                Permission.subject_type == stype,
                Permission.subject_id == sid,
                Permission.resource_type == RESOURCE_TEMPLATE,
                Permission.action == action,
            )
        )
        for rid in rows:
            ids.add(int(rid))
    return ids


def visible_template_ids(db: Session, user: User) -> set[int] | None:
    """该用户可见(view)的模板 id 集合;管理员返回 None 表示全部。"""
    return action_template_ids(db, user, ACTION_VIEW)


def grant(
    db: Session,
    *,
    subject_type: str,
    subject_id: str,
    resource_type: str,
    resource_id: str,
    actions: list[str],
    granted_by: int | None,
) -> list[Permission]:
    created: list[Permission] = []
    for action in actions:
        exists = db.scalar(
            select(Permission).where(
                Permission.subject_type == subject_type,
                Permission.subject_id == subject_id,
                Permission.resource_type == resource_type,
                Permission.resource_id == resource_id,
                Permission.action == action,
            )
        )
        if exists:
            continue
        p = Permission(
            subject_type=subject_type,
            subject_id=subject_id,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            granted_by=granted_by,
        )
        db.add(p)
        created.append(p)
    db.commit()
    return created

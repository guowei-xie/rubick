"""授权判定与授予。

有效权限 = 个人授权。管理员全通;模板作者对自己的模板全通。
Phase 2 再加 RBAC 角色主体与拒绝优先。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.permission import (
    ACTION_RUN,
    ACTION_VIEW,
    RESOURCE_TEMPLATE,
    SUBJECT_USER,
    Permission,
)  # noqa: F401
from app.models.template import SqlTemplate
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, User
from app.services import user_service


def is_manager(user: User) -> bool:
    """管理者 = 管理员或开发者。二者对模板/运行记录拥有全通短路;
    区别仅在治理动作(用户角色赋权/数据源/审计),那三块由 require_admin 单独把守。"""
    return user.role in (ROLE_ADMIN, ROLE_DEVELOPER)


def _subject_filters(user: User):
    """当前用户对应的所有授权主体 (type, id)。当前仅个人用户。"""
    return [(SUBJECT_USER, str(user.id))]


def can_access_job(user: User, job) -> bool:
    """任务结果的访问权:本人或管理者(管理员/开发者)。集中此处避免各处重复写。"""
    return is_manager(user) or job.user_id == user.id


def is_template_owner(user: User, tmpl) -> bool:
    """管理者(管理员/开发者)恒真;否则需为该模板作者。作用于已加载的模板对象,避免重复查询。"""
    return is_manager(user) or bool(tmpl and tmpl.author_id == user.id)


def owns_template(db: Session, user: User, template_id: int | str) -> bool:
    """管理者(管理员/开发者)恒真;普通用户只能管自己作为作者的模板。"""
    if is_manager(user):
        return True
    return is_template_owner(user, db.get(SqlTemplate, int(template_id)))


def owned_template_ids(db: Session, user: User) -> list[int]:
    return list(db.scalars(select(SqlTemplate.id).where(SqlTemplate.author_id == user.id)))


def can(db: Session, user: User, action: str, resource_type: str, resource_id: int | str) -> bool:
    if is_manager(user):
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
    """用户对模板可执行 action(view/run/download)的 id 集合;管理者(管理员/开发者)返回 None 表示全部。
    作者对自己的模板拥有全部动作。"""
    if is_manager(user):
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


def authorized_run_users(db: Session, template_ids: list[int]) -> dict[int, list[dict]]:
    """每个模板「显式授权了 run 动作」的用户列表(id/name/avatar),按模板 id 分组。

    两步查询(仿 permissions.py::_enrich):先取授权行,再按 user.id 批量取用户,避免对
    subject_id(String) 做 cast join,MySQL/SQLite 皆安全,且 O(1) 次查询无 N+1。
    仅含显式授权用户;作者/管理者的隐式权限不入列(即卡片上的「参与者」语义)。
    """
    if not template_ids:
        return {}
    rid_strs = [str(i) for i in template_ids]
    rows = list(
        db.execute(
            select(Permission.resource_id, Permission.subject_id).where(
                Permission.resource_type == RESOURCE_TEMPLATE,
                Permission.action == ACTION_RUN,
                Permission.subject_type == SUBJECT_USER,
                Permission.resource_id.in_(rid_strs),
            )
        )
    )
    if not rows:
        return {}

    def _as_int(v) -> int | None:
        try:
            return int(v)
        except (TypeError, ValueError):
            return None  # subject_id 是自由字符串列(将来可能是组 id 等),非数字则跳过

    pairs = [(_as_int(rid), _as_int(sid)) for rid, sid in rows]
    uid_ints = {uid for _, uid in pairs if uid is not None}
    if not uid_ints:
        return {}
    users = {
        u.id: {"id": u.id, "name": u.name, "avatar": u.avatar}
        for u in db.execute(select(User.id, User.name, User.avatar).where(User.id.in_(uid_ints)))
    }
    grouped: dict[int, list[dict]] = {}
    for rid, uid in pairs:
        u = users.get(uid) if uid is not None else None
        if rid is not None and u:
            grouped.setdefault(rid, []).append(u)
    return grouped


def grant(
    db: Session,
    *,
    subject_type: str,
    resource_type: str,
    resource_id: str,
    actions: list[str],
    granted_by: int | None,
    subject_id: str | None = None,
    subject_open_id: str | None = None,
    subject_profile: dict | None = None,
) -> list[Permission]:
    # 主体解析集中在服务层(单一事务归属):传 open_id 时在此(而非搜索时)按 open_id upsert
    # 用户、拿其 id 作主体;否则用已知的 subject_id。
    if subject_open_id:
        profile = {"open_id": subject_open_id, **{k: v for k, v in (subject_profile or {}).items() if v}}
        subject = user_service.upsert_user(db, profile)
        db.flush()  # 拿到自增 id;与下方授权同一事务提交
        subject_id = str(subject.id)

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

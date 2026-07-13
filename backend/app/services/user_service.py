"""用户/部门的 upsert(按飞书 id)。中立位置,供 auth 登录与 feishu 通讯录同步共用,
避免二者相互 import 造成循环依赖。均不提交,由调用方统一 commit。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import Department, User


def upsert_user(db: Session, profile: dict) -> User:
    """按 open_id upsert 用户,合并可用的资料字段(空值不覆盖)。"""
    user = db.scalar(select(User).where(User.feishu_open_id == profile["open_id"]))
    if user is None:
        user = User(feishu_open_id=profile["open_id"], name=profile.get("name", "飞书用户"))
        db.add(user)
    user.union_id = profile.get("union_id") or user.union_id
    user.name = profile.get("name") or user.name
    user.email = profile.get("email") or user.email
    user.avatar = profile.get("avatar") or user.avatar
    return user


def upsert_department(db: Session, feishu_id: str, name: str, parent: str | None = None) -> Department:
    dep = db.scalar(select(Department).where(Department.feishu_dept_id == feishu_id))
    if dep is None:
        dep = Department(feishu_dept_id=feishu_id, name=name)
        db.add(dep)
    else:
        dep.name = name or dep.name
    if parent is not None:
        dep.parent_feishu_dept_id = parent
    return dep

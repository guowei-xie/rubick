"""授权时用的用户 / 部门查找(挑授权对象)。管理员可用。

用户搜索查的是「飞书通讯录目录」(同步进 users 表的全员,含未登录者),
和「用户管理」(只显示登录过的)是同表不同视图。空查询只回历史(之前授权过的人)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.database import get_db
from app.models.permission import Permission
from app.models.user import Department, User
from app.services import feishu_service, user_service

router = APIRouter(prefix="/lookup", tags=["lookup"])


def _local_search(db: Session, q: str) -> list[User]:
    stmt = (
        select(User)
        .where(or_(User.name.like(f"%{q}%"), User.email.like(f"%{q}%")))
        .order_by(User.name)
        .limit(50)
    )
    return list(db.scalars(stmt))


@router.get("/users")
def lookup_users(q: str | None = None, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    q = (q or "").strip()
    if not q:
        # 空查询:只显示历史 —— 之前被授权过的人,而不是 dump 一个部门
        granted = db.scalars(
            select(Permission.subject_id).where(Permission.subject_type == "user").distinct()
        )
        ids = [int(x) for x in granted if x and x.isdigit()]
        rows = list(db.scalars(select(User).where(User.id.in_(ids or [-1])).order_by(User.name).limit(50)))
        return [_user_row(u) for u in rows]

    # 有关键词:优先用当前管理员的 user_access_token 实时搜全公司;拿不到/失败则回退本地目录
    token = feishu_service.valid_user_token(db, user)
    if token:
        try:
            found = feishu_service.search_users(q, token)
            out = []
            for u in found:  # 命中的人 upsert 成「壳」用户(无 last_login → 不进用户管理),授权落到其 id
                row = user_service.upsert_user(db, {
                    "open_id": u["open_id"], "name": u.get("name"), "email": u.get("email"),
                    "avatar": u.get("avatar"),
                })
                db.flush()
                # 工号不落库,直接透传飞书搜索结果供前端展示;授权仍用 DB id
                out.append(_user_row(row, employee_id=u.get("employee_id")))
            db.commit()
            return out
        except Exception:  # noqa: BLE001 飞书失败 → 回退本地
            db.rollback()
    rows = _local_search(db, q)
    return [_user_row(u) for u in rows]


def _user_row(u: User, employee_id: str | None = None) -> dict:
    return {
        "id": u.id,
        "name": u.name,
        "email": u.email,
        "avatar": u.avatar,
        "employee_id": employee_id,
        "department_id": u.department_id,
    }


@router.get("/departments")
def lookup_departments(q: str | None = None, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    stmt = select(Department).order_by(Department.name).limit(100)
    if q:
        stmt = stmt.where(Department.name.like(f"%{q}%"))
    return [{"id": d.id, "name": d.name} for d in db.scalars(stmt)]

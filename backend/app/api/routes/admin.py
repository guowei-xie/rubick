from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.database import get_db
from app.core.exceptions import NotFoundError, RubicError
from app.models.user import ROLE_ADMIN, ROLE_USER, User
from app.schemas.common import UserOut

router = APIRouter(prefix="/admin", tags=["admin"])

_ROLES = {ROLE_USER, ROLE_ADMIN}


class RoleIn(BaseModel):
    role: str


@router.get("/users", response_model=list[UserOut])
def list_users(
    q: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    # 只展示登录过的用户(扫码进来才会出现);未登录的通讯录成员不显示
    stmt = select(User).where(User.last_login_at.is_not(None)).order_by(User.last_login_at.desc())
    if q:
        stmt = stmt.where(User.name.like(f"%{q}%"))
    return list(db.scalars(stmt))


@router.post("/users/{user_id}/role", response_model=UserOut)
def set_role(
    user_id: int,
    data: RoleIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    if data.role not in _ROLES:
        raise RubicError(f"非法角色:{data.role}")
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError("用户不存在")
    user.role = data.role
    db.commit()
    db.refresh(user)
    return user

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, require_admin
from app.core.database import get_db
from app.core.exceptions import NotFoundError, RubicError
from app.models.audit import ACTION_USER_ROLE_CHANGE, RESOURCE_USER
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER, User
from app.schemas.common import UserOut
from app.services import audit_service, user_service

router = APIRouter(prefix="/admin", tags=["admin"])

_ROLES = {ROLE_USER, ROLE_ADMIN, ROLE_DEVELOPER}


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
    # 姓名或邮箱:列表本来就显示邮箱列,只按姓名搜是明显的不一致。
    # 条件与团队候选人列表共用一份(user_service.name_or_email_like)
    cond = user_service.name_or_email_like(q)
    if cond is not None:
        stmt = stmt.where(cond)
    return list(db.scalars(stmt))


@router.post("/users/{user_id}/role", response_model=UserOut)
def set_role(
    user_id: int,
    data: RoleIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    ip: str | None = Depends(client_ip),
):
    if data.role not in _ROLES:
        raise RubicError(f"非法角色:{data.role}")
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError("用户不存在")
    old_role = user.role  # 必须在赋值之前取
    # 赋角色 + 降级连带清队是一条不变量,住在 user_service.change_role —— 路由只做
    # 解析、鉴权与审计,别让第二条改角色的路径有机会绕过清队
    removed = user_service.change_role(db, user, data.role)
    db.commit()
    db.refresh(user)
    # 角色没变也照记:管理员执行过一次提权操作这件事本身就该留痕
    audit_service.log(
        db, user=admin, action=ACTION_USER_ROLE_CHANGE,
        resource_type=RESOURCE_USER, resource_id=user_id, resource_name=user.name,
        detail={
            "role": {"from": old_role, "to": data.role},
            "target_user_name": user.name,
            "target_user_email": user.email,
            # 恒有键、三个取值各有各的意思:团队列表 = 清出去的那些;`[]` = 清了队但他本来就
            # 不在任何团队;`null` = 本次动作不涉及清队(提权 / 平调)。后两者必须分得开 ——
            # 查审计的人会拿这一项反推「此人当时在哪些团队」,而缺键又会被读成「这个版本还
            # 没这功能」。
            "removed_from_teams": removed,
        },
        ip=ip,
    )
    return user

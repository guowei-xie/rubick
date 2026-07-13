"""FastAPI 依赖:解析 JWT → 当前用户;角色校验。"""
from __future__ import annotations

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import PermissionDeniedError, UnauthorizedError
from app.core.security import decode_access_token
from app.models.user import ROLE_ADMIN, ROLE_ANALYST, User


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("缺少认证令牌")
    payload = decode_access_token(authorization.split(" ", 1)[1])
    if not payload:
        raise UnauthorizedError("令牌无效或已过期")
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise UnauthorizedError("用户不存在或已停用")
    return user


def require_analyst(user: User = Depends(get_current_user)) -> User:
    if user.role not in (ROLE_ANALYST, ROLE_ADMIN):
        raise PermissionDeniedError("需要商分或管理员权限")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != ROLE_ADMIN:
        raise PermissionDeniedError("需要管理员权限")
    return user


def client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None

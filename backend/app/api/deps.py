"""FastAPI 依赖:解析 JWT → 当前用户;角色校验。"""
from __future__ import annotations

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import PermissionDeniedError, UnauthorizedError
from app.core.security import decode_access_token
from app.models.user import ROLE_ADMIN, User
from app.services import permission_service


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


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != ROLE_ADMIN:
        raise PermissionDeniedError("需要管理员权限")
    return user


def require_manager(user: User = Depends(get_current_user)) -> User:
    """管理员或开发者:模板/授权等非治理写操作的守卫(治理仍用 require_admin)。
    「谁是管理者」的定义集中在 permission_service.is_manager,此处只复用不重列角色。"""
    if not permission_service.is_manager(user):
        raise PermissionDeniedError("需要开发者或管理员权限")
    return user


def client_ip(request: Request) -> str | None:
    """取真实客户端 IP(优先 X-Forwarded-For 首跳)。

    也可直接当依赖用:`ip: str | None = Depends(client_ip)` —— 这样路由只声明它真正需要的
    那一个字符串,不必把整个 Request 传进来。注意签名必须保持 `Request`(不能是
    `Request | None`),否则 FastAPI 在注册路由时就会报 Invalid args for response field。
    """
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None

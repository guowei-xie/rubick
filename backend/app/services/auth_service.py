"""登录:mock 或飞书 OAuth,统一产出平台 JWT。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import UnauthorizedError
from app.core.security import create_access_token
from app.models.user import ROLE_ADMIN, User
from app.services import feishu_service, user_service


def _bootstrap_admins() -> set[str]:
    return {x.strip().lower() for x in settings.BOOTSTRAP_ADMINS.split(",") if x.strip()}


def _upsert_user(db: Session, profile: dict) -> User:
    user = user_service.upsert_user(db, profile)

    # 引导管理员:命中邮箱/open_id 名单则自动提升(只升不降,手动调整仍生效)
    allow = _bootstrap_admins()
    if allow and user.role != ROLE_ADMIN:
        ident = {(user.email or "").lower(), user.feishu_open_id.lower()}
        if ident & allow:
            user.role = ROLE_ADMIN

    user.last_login_at = datetime.now(timezone.utc)  # 标记已登录 → 才会出现在用户管理
    # 存 user_access_token(用于按本人可见范围搜通讯录);exp 用 naive UTC 便于比较
    if profile.get("access_token"):
        user.feishu_token = profile["access_token"]
        user.feishu_refresh_token = profile.get("refresh_token")
        user.feishu_token_exp = datetime.utcnow() + timedelta(seconds=int(profile.get("expires_in", 7000)))
    db.commit()
    db.refresh(user)
    return user


def login_with_code(db: Session, code: str) -> tuple[str, User]:
    profile = feishu_service.exchange_code(code)
    user = _upsert_user(db, profile)
    return create_access_token(str(user.id)), user


def mock_login(db: Session, feishu_open_id: str) -> tuple[str, User]:
    if not settings.MOCK_AUTH:
        raise UnauthorizedError("mock 登录未启用")
    user = db.scalar(select(User).where(User.feishu_open_id == feishu_open_id))
    if user is None:
        raise UnauthorizedError(f"未找到用户:{feishu_open_id}(请先运行 seed)")
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return create_access_token(str(user.id)), user

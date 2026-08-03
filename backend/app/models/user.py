"""用户。首次飞书扫码登录时按需创建(JIT);授权选人时实时搜通讯录命中者也会 upsert 成壳用户。"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, tbl
from app.core.db_types import EncryptedText
from app.models.mixins import TimestampMixin

# 平台角色:只有两种。管理员可建/改/发布任意项目并互相可见;业务用户只填参取数。
ROLE_USER = "user"          # 业务使用者
ROLE_ADMIN = "admin"        # 管理员(含原商分职责)


class User(Base, TimestampMixin):
    __tablename__ = tbl("users")

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    feishu_open_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    union_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    avatar: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default=ROLE_USER, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    # 最近登录时间;仅登录过的用户才在「用户管理」里展示
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # 飞书 user_access_token(登录时获取),用于按本人可见范围搜通讯录;约 2h 过期,用 refresh 刷新。
    # 落库前透明加密(EncryptedText),读取时自动解密。
    feishu_token: Mapped[Optional[str]] = mapped_column(EncryptedText, nullable=True)
    feishu_refresh_token: Mapped[Optional[str]] = mapped_column(EncryptedText, nullable=True)
    feishu_token_exp: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

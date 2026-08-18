"""用户。两个入口按需创建(JIT):① 首次飞书扫码登录;② 授权选人时被**真正授权**的通讯录成员
(按 open_id upsert)。搜通讯录本身不落库,避免搜索即制造壳用户 —— 见 routes/lookup.py。"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, tbl
from app.core.db_types import BigIntPk, EncryptedText
from app.models.mixins import TimestampMixin

# 平台角色:三种。管理员可建/改/上线任意任务并互相可见,且独揽治理(用户角色赋权/数据源/审计);
# 开发者近似管理员但不含这三块治理;普通用户只填参取数。
ROLE_USER = "user"          # 普通用户(业务使用者)
ROLE_ADMIN = "admin"        # 管理员(含 SQL 编写与上线职责)
ROLE_DEVELOPER = "developer"  # 开发者:只在自己所属团队内取值(见 services/permission_service)


def is_platform_admin(user: "User") -> bool:
    """平台管理员:不受团队约束,可见且可编辑所有团队的所有任务。

    **唯一的「全通」来源**。放在角色常量旁边(而不是 permission_service)是因为它只是一条
    对 role 的判断,不依赖任何服务层 —— 搁在上层会逼着 team_service / credential_service
    为这一个谓词做延迟导入,把真实的依赖图藏起来。permission_service 会转出它,
    「全通判定只表述一次」的口径不变。
    """
    return user.role == ROLE_ADMIN


class User(Base, TimestampMixin):
    __tablename__ = tbl("users")

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
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

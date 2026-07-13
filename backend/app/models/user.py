"""用户与部门(由飞书通讯录同步而来)。"""
from __future__ import annotations
from typing import Optional
from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin

# 平台角色(粗粒度,Phase 1 用;细粒度 RBAC 在 Phase 2)
ROLE_USER = "user"          # 业务用户
ROLE_ANALYST = "analyst"    # 商分(SQL 作者)
ROLE_ADMIN = "admin"        # 管理员


class Department(Base, TimestampMixin):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    feishu_dept_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    parent_feishu_dept_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    feishu_open_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    union_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    avatar: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    department_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("departments.id"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(32), default=ROLE_USER, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

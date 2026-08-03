"""授权记录:主体 × 资源 × 动作。

当前支持主体 = user / department;资源 = template;动作 = view/run/download。
用户组/RBAC 角色主体与脱敏为后续扩展(见 PRD「实现现状」),尚未实现。
"""
from __future__ import annotations
from typing import Optional
from sqlalchemy import BigInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, tbl
from app.models.mixins import TimestampMixin

# 主体类型(当前支持个人用户 + 部门;用户组/角色见 PRD「实现现状」,未实现)
SUBJECT_USER = "user"
SUBJECT_DEPARTMENT = "department"

# 资源类型
RESOURCE_TEMPLATE = "template"

# 动作
ACTION_VIEW = "view"
ACTION_RUN = "run"
ACTION_DOWNLOAD = "download"


class Permission(Base, TimestampMixin):
    __tablename__ = tbl("permissions")
    __table_args__ = (
        UniqueConstraint(
            "subject_type", "subject_id", "resource_type", "resource_id", "action",
            name="uq_permission",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    subject_type: Mapped[str] = mapped_column(String(32), index=True)
    subject_id: Mapped[str] = mapped_column(String(64), index=True)  # user.id / department.id
    resource_type: Mapped[str] = mapped_column(String(32), index=True)
    resource_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(32))
    granted_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

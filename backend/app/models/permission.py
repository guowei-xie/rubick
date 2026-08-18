"""授权记录:主体 × 资源 × 动作。

当前支持主体 = user;资源 = template;动作 = view/run/download/edit。
用户组/RBAC 角色主体与脱敏为后续扩展,尚未实现。

动作分两类,**入口不同**:
  - view/run/download(BUSINESS_ACTIONS)—— 给业务使用者的授权,走 /api/permissions;
  - edit —— 团队内「指定任务的编辑权」,由团队管理员授予团队成员,只走 /api/tasks/{id}/editors。
两者刻意不混:业务授权入口若接受任意动作字符串,任何能授权的人都能塞一个 edit 给自己,
那是一条提权后门(见 schemas/permission.py 的 Literal 收口)。
"""
from __future__ import annotations
from typing import Optional
from sqlalchemy import BigInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, tbl
from app.models.mixins import TimestampMixin

# 主体类型(当前仅个人用户;用户组/角色未实现)
SUBJECT_USER = "user"

# 资源类型
RESOURCE_TEMPLATE = "template"

# 动作
ACTION_VIEW = "view"
ACTION_RUN = "run"
ACTION_DOWNLOAD = "download"
# 指定任务的编辑权:团队管理员授予团队成员。不属于业务授权,不从 /api/permissions 进出
ACTION_EDIT = "edit"

# 业务使用者可被授予的动作。/api/permissions 只处理这三种 —— 见模块 docstring
BUSINESS_ACTIONS = (ACTION_VIEW, ACTION_RUN, ACTION_DOWNLOAD)


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
    subject_id: Mapped[str] = mapped_column(String(64), index=True)  # user.id
    resource_type: Mapped[str] = mapped_column(String(32), index=True)
    resource_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(32))
    granted_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

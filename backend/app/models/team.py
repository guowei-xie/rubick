"""团队 —— 任务的归属边界与取数身份边界。

**为什么存在**:开发者角色原先是「近似管理员」——彼此的任务互相可见**且可编辑**,
取数身份挂在个人头上。团队把这两件事收成同一个边界:任务必属一个团队,同团队互为「内部人」
(可见、可运行),取数身份来自**团队账号**(见 models/credential.py)。

「谁能看 / 谁能改」这条规则只在 services/permission_service 表述一次;
「谁在哪个团队、是不是团队管理员」这条事实只在 services/team_service 表述一次。
本模块只负责承载这两条事实的存储,不含判定逻辑。

成员表刻意是**显式实体**而非多对多 secondary 表:is_team_admin 是成员关系上的属性,
且成员增删本身要审计(谁在什么时候把谁加进来 —— 那等于一次数据授权,见下)。

⚠️ **加入团队 = 一次数据授权**:团队账号是共享的,任何成员都能在任务编辑器里用它试跑任意 SQL。
所以团队能读到的数据,全体成员都能读到。数据隔离的粒度因此等于「团队」,而不是「人」。
"""
from __future__ import annotations
from typing import Optional

from sqlalchemy import BigInteger, Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, tbl
from app.core.db_types import BigIntPk
from app.models.mixins import TimestampMixin

# 存量任务并入的兜底团队名。migrate 与 seed 都引用它,不各自写字面量。
DEFAULT_TEAM_NAME = "默认团队"


class Team(Base, TimestampMixin):
    """一个团队。由平台管理员创建并指定团队管理员(见 routes/teams.py)。"""

    __tablename__ = tbl("teams")

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    # 唯一:团队名是人认它的唯一标识,重名会让「加错团队」无从察觉
    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 建团队的平台管理员。刻意不做 FK:用户行被清理掉不该拖累团队(同 TemplateEnumValues.updated_by)
    created_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class TeamMember(Base, TimestampMixin):
    """某人在某团队的成员身份。一人一团队恒定一行。"""

    __tablename__ = tbl("team_members")
    __table_args__ = (UniqueConstraint("team_id", "user_id", name="uq_team_member"),)

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(tbl("teams.id")), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(tbl("users.id")), index=True)
    # 团队管理员:可增删本团队成员、配团队取数账号、为成员授予「指定任务的编辑权」,
    # 并可编辑本团队全部任务(无需给自己授权)。
    # 刻意**不做成第四种平台角色**:一个人可以在 A 队当管理员、在 B 队当普通成员,
    # users.role 那一列表达不了这种关系。
    is_team_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 谁把他加进来的。同 created_by,软引用不做 FK
    added_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

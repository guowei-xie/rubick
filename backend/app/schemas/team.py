"""团队、成员与任务编辑人的入出参。"""
from __future__ import annotations
from datetime import datetime

from pydantic import BaseModel


class TeamIn(BaseModel):
    name: str
    description: str | None = None
    # 首批团队管理员(可空)。成员必须是开发者角色,由 team_service.add_member 校验
    admin_user_ids: list[int] = []


class TeamUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None


class TeamMemberOut(BaseModel):
    user_id: int
    name: str
    avatar: str | None = None
    role: str
    is_team_admin: bool = False
    joined_at: datetime | None = None


class TeamBriefOut(BaseModel):
    """团队列表的一行。任务数含回收站里已下线的 —— 删团队的卡点看的就是它。"""

    id: int
    name: str
    description: str | None = None
    member_count: int = 0
    template_count: int = 0
    admins: list[TeamMemberOut] = []
    created_at: datetime | None = None


class TeamDetailOut(TeamBriefOut):
    members: list[TeamMemberOut] = []


class MemberIn(BaseModel):
    user_id: int
    is_team_admin: bool = False


class CandidateOut(BaseModel):
    user_id: int
    name: str
    avatar: str | None = None
    email: str | None = None


class EditorIn(BaseModel):
    user_id: int


class TaskEditorOut(BaseModel):
    """任务的一名「编辑人」。source 区分能否撤销:
    author / team_admin 是身份的推论(隐式,不可撤销),granted 才是一条授权行。"""

    user_id: int
    name: str
    avatar: str | None = None
    source: str  # author | team_admin | granted


class TaskTeamIn(BaseModel):
    team_id: int

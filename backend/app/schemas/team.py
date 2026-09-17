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
    email: str | None = None
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


class TaskAuthorIn(BaseModel):
    """作者转移的接收人。**只能是该任务所属团队的在职成员**(服务端校验),
    与 EditorIn 同一个取值范围,故同住本文件而不在 schemas/template.py。

    刻意不进 TemplateUpdateIn:作者转移会同时改变编辑权归属与「我开发的」筛选,
    是一次治理动作而非任务编辑的一部分 —— 理由与 team_id 不进 TemplateUpdateIn
    完全相同(见 schemas/template.py 里 TemplateUpdateIn 的说明)。
    """

    user_id: int


# ---------------------------------------------------------------- 批量转移作者(多选交接)

#: 一次批量交接的任务数上限。无上限等于给自己留一条超时路径:一次请求要写 N 条审计
#: 并逐人推飞书。200 远高于「一个人名下的任务数」这个真实量级,够用且兜得住。
BATCH_AUTHOR_TRANSFER_LIMIT = 200


class BatchAuthorTransferIn(BaseModel):
    """多选任务 → 一个接手人。语义是**全成功才生效**,故这里不给任何「跳过失败项」的开关:
    「转了一半」在归属这件事上最难向审计解释。"""

    user_id: int
    template_ids: list[int]


class BlockedGroupOut(BaseModel):
    """某个候选人接不了的一组任务,按理由归并。

    **按理由分组而不是逐任务给一句话**:逐任务是「候选人数 × 任务数」条中文,
    payload 要翻十倍;而理由的取值空间极小(已是作者 / 不在某个团队)。前端悬停时
    找到含该任务 id 的那一组、读它的 message 即可 —— 文案仍然只由服务端出。
    """

    code: str                    # already_author | not_in_team | no_team | no_right
    message: str                 # 给人看的整句话,前端原样显示
    template_ids: list[int]


class AuthorTransferCandidateOut(BaseModel):
    """一个可能的接手人,以及「选了他之后哪些任务能勾、哪些要置灰」。"""

    user_id: int
    name: str
    avatar: str | None = None
    # 带邮箱:同名同事在名单里区分不开,而选错人的代价是把一批任务交给了另一个人
    email: str | None = None
    eligible_template_ids: list[int]
    blocked: list[BlockedGroupOut] = []


class AuthorTransferCandidatesOut(BaseModel):
    """「先选接手人,再按他过滤可勾选的任务」这一步所需的全部事实,一次取回。

    顶层 blocked 与接手人**无关**(我根本没有处分权、或任务无主),所以只算一次;
    每个候选人的 eligible + 他自己的 blocked 划分掉其余的。三截合起来恰好覆盖我看得见的
    全部任务,前端据此三态渲染,**每一句话都来自服务端** —— 它不必也不许自己再推一遍
    「同团队 ∧ 在职 ∧ 非当前作者」。
    """

    blocked: list[BlockedGroupOut] = []
    candidates: list[AuthorTransferCandidateOut]


class BatchAuthorTransferOut(BaseModel):
    """批量转移的回执。batch_id 一并回给前端 —— 拿它去审计页就能捞出「我刚才那一批」。

    刻意不回逐任务明细:那份事实在审计里(每个任务一条,共享 batch_id),
    多回一份只会变成第三个必须同步维护的行形状。
    """

    batch_id: str
    to_user_id: int
    to_user_name: str
    count: int

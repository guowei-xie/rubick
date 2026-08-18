"""团队与成员。

权限边界(需求已定):
- **平台管理员**建/改/删团队,并指定或取消团队管理员;
- **团队管理员**增删本团队成员(只能加开发者);
- **团队成员**可读本团队信息。

团队取数账号在 routes/credentials.py;任务的「编辑人」与「转移团队」在 routes/tasks.py
(那两件事的资源身份是任务,路径就该挂在任务下)。

**团队维度的守卫写在函数体内而不是 Depends**:FastAPI 的依赖拿不到路径参数,而本仓库的
测试直接调用路由函数 —— 守卫在体内才测得到 403。豁免规则(平台管理员恒放行)集中在
team_service.require_member / require_team_admin。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, require_admin, require_task_author
from app.core.database import get_db
from app.models.audit import (
    ACTION_TEAM_ADMIN_GRANT,
    ACTION_TEAM_ADMIN_REVOKE,
    ACTION_TEAM_CREATE,
    ACTION_TEAM_DELETE,
    ACTION_TEAM_MEMBER_ADD,
    ACTION_TEAM_MEMBER_REMOVE,
    ACTION_TEAM_UPDATE,
    RESOURCE_TEAM,
)
from app.models.team import Team
from app.models.template import SqlTemplate
from app.models.user import User
from app.schemas.team import (
    CandidateOut,
    MemberIn,
    TeamBriefOut,
    TeamDetailOut,
    TaskEditorOut,
    TeamIn,
    TeamMemberOut,
    TeamUpdateIn,
)
from app.services import audit_service, credential_service, permission_service, team_service

router = APIRouter(prefix="/teams", tags=["teams"])

# 进审计 diff 的团队元信息字段(同 templates.py 的 _TMPL_AUDIT_FIELDS 约定)
_TEAM_AUDIT_FIELDS = ("name", "description")


def _audit(
    db: Session, user: User, ip: str | None, action: str, team: Team, detail: dict | None = None
) -> None:
    """团队类审计的固定部分集中一处:资源恒为该团队(id + 名称快照)。"""
    audit_service.log(
        db, user=user, action=action, resource_type=RESOURCE_TEAM,
        resource_id=team.id, resource_name=team.name, detail=detail or {}, ip=ip,
    )


def _briefs(db: Session, teams: list[Team]) -> list[dict]:
    """一批团队的列表行。**固定 2 次查询**,与团队数无关。

    逐队查会变成 2N —— 而这条路径(团队列表页、团队管理页、平台管理员打开任务编辑器时的
    团队下拉)在管理员视角下 N 可能有几十,且每次增删成员都会重拉。
    """
    ids = [t.id for t in teams]
    members = team_service.members_by_team(db, ids)
    counts = team_service.template_counts(db, ids)
    return [
        {
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "member_count": len(members[t.id]),
            # 含回收站里已下线的任务 —— 删团队的卡点看的就是它
            "template_count": counts[t.id],
            "admins": [m for m in members[t.id] if m["is_team_admin"]],
            "created_at": t.created_at,
        }
        for t in teams
    ]


def _brief(db: Session, team: Team) -> dict:
    """单个团队的列表行(建/改团队后回给前端)。形状只在 _briefs 里定义一次。"""
    return _briefs(db, [team])[0]


# ---------------------------------------------------------------- 读
# 注意:任何字面量子路径都必须声明在 /{team_id} 之前,否则 team_id: int 会把它当路径参数
# 解析成 422。当前没有这类子路径,新增时务必守住这条。


@router.get("", response_model=list[TeamBriefOut])
def list_teams(db: Session = Depends(get_db), user: User = Depends(require_task_author)):
    """我可选的团队:平台管理员=全部;开发者=自己所属的。

    前端用它填任务编辑器的「所属团队」下拉与团队管理页。返回空列表即意味着
    「这个开发者还不能建任务」,前端要据此给出明确空态。
    """
    teams = (
        team_service.all_teams(db)
        if permission_service.is_platform_admin(user)
        else team_service.teams_of(db, user)
    )
    return _briefs(db, teams)


@router.get("/{team_id}", response_model=TeamDetailOut)
def get_team(
    team_id: int, db: Session = Depends(get_db), user: User = Depends(require_task_author)
):
    team = team_service.require_member(db, user, team_id)
    members = team_service.members_of(db, team.id)
    return {
        **_briefs(db, [team])[0],
        # _briefs 已经把成员查出来过一次,这里直接复用它的口径(而不是再查一遍)
        "members": members,
    }


@router.get("/{team_id}/candidates", response_model=list[CandidateOut])
def list_candidates(
    team_id: int,
    q: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_task_author),
):
    """可加入本团队的候选人:登录过的开发者且尚不在本团队。

    刻意不走飞书通讯录(那是给业务授权选人的):团队成员必须已经是平台上的开发者。
    """
    team_service.require_team_admin(db, user, team_id)
    return team_service.candidates(db, team_id, q)


@router.get("/{team_id}/task-editors", response_model=dict[int, list[TaskEditorOut]])
def team_task_editors(
    team_id: int, db: Session = Depends(get_db), user: User = Depends(require_task_author)
):
    """本团队每个任务的「编辑人」名单,按任务 id 分组。

    给团队页的「任务编辑权」面板用。**一次请求查完**——逐任务调 /tasks/{id}/editors 会变成
    N 个往返 × 每个 3 次查询,而这个面板一进就要显示整张表。
    """
    team_service.require_member(db, user, team_id)
    tmpls = list(db.scalars(select(SqlTemplate).where(SqlTemplate.team_id == team_id)))
    return permission_service.editors_by_template(db, tmpls)


# ---------------------------------------------------------------- 团队 CRUD(平台管理员)


@router.post("", response_model=TeamBriefOut)
def create_team(
    data: TeamIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    ip: str | None = Depends(client_ip),
):
    """建团队,可同时指定首批团队管理员(必须是开发者角色)。"""
    team = team_service.create_team(
        db, name=data.name, description=data.description, created_by=user.id
    )
    added: list[dict] = []
    for uid in data.admin_user_ids:
        target = team_service.add_member(
            db, team, uid, is_team_admin_flag=True, added_by=user.id
        )
        added.append({"user_id": target.id, "name": target.name})
    _audit(
        db, user, ip, ACTION_TEAM_CREATE, team,
        {"description": team.description, "team_admins": added},
    )
    return _brief(db, team)


@router.put("/{team_id}", response_model=TeamBriefOut)
def update_team(
    team_id: int,
    data: TeamUpdateIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    ip: str | None = Depends(client_ip),
):
    team = team_service.get_team(db, team_id)
    # 快照必须早于修改:SQLAlchemy 就地改对象,提交后再读拿到的是新值(同 templates.py)
    before = audit_service.snapshot(team, _TEAM_AUDIT_FIELDS)
    team = team_service.update_team(db, team, name=data.name, description=data.description)
    _audit(
        db, user, ip, ACTION_TEAM_UPDATE, team,
        {"changes": audit_service.diff(before, audit_service.snapshot(team, _TEAM_AUDIT_FIELDS))},
    )
    return _brief(db, team)


@router.delete("/{team_id}")
def delete_team(
    team_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    ip: str | None = Depends(client_ip),
):
    """删除团队。团队下还有任务(**含回收站里已下线的**)时拒绝 —— 与「数据源被任务引用时
    不可删」同一口径。通过后连带清掉成员行与团队取数账号。"""
    team = team_service.get_team(db, team_id)
    # 快照必须早于删除:提交后这些属性就读不到了
    name, member_count = team.name, len(team_service.members_of(db, team.id))
    revoked_credentials = team_service.delete_team(db, team)
    audit_service.log(
        db, user=user, action=ACTION_TEAM_DELETE, resource_type=RESOURCE_TEAM,
        resource_id=team_id, resource_name=name,
        detail={
            "member_count": member_count,
            "revoked_credentials": revoked_credentials,
        },
        ip=ip,
    )
    return {"ok": True}


# ---------------------------------------------------------------- 成员(团队管理员)


@router.post("/{team_id}/members", response_model=TeamMemberOut)
def add_team_member(
    team_id: int,
    data: MemberIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_task_author),
    ip: str | None = Depends(client_ip),
):
    """把一名开发者加入团队。

    ⚠️ 这是一次**数据授权**:团队账号是共享的,成员能在任务编辑器里用它试跑任意 SQL,
    因此该团队能读到的数据他全都能读到(见 models/team.py)。故必须留痕。
    """
    team = team_service.require_team_admin(db, user, team_id)
    target = team_service.add_member(
        db, team, data.user_id, is_team_admin_flag=data.is_team_admin, added_by=user.id
    )
    _audit(
        db, user, ip, ACTION_TEAM_MEMBER_ADD, team,
        {
            "target_user_id": target.id,
            "target_user_name": target.name,
            "as_team_admin": data.is_team_admin,
        },
    )
    return team_service.member_of(db, team.id, target.id)


@router.delete("/{team_id}/members/{user_id}")
def remove_team_member(
    team_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_task_author),
    ip: str | None = Depends(client_ip),
):
    """把成员移出团队。他在本团队任务上的「编辑权」授权随之撤销。

    级联撤销的任务清单进 detail,而**只发一条审计行** —— 一次操作发 N 条会让治理阅读变差,
    也会打断「一次写操作对应一条日志」这个断言风格。
    """
    team = team_service.require_team_admin(db, user, team_id)
    target, revoked = team_service.remove_member(db, team, user_id)
    _audit(
        db, user, ip, ACTION_TEAM_MEMBER_REMOVE, team,
        {
            "target_user_id": user_id,
            "target_user_name": target.name if target else None,
            "revoked_edit_template_ids": revoked,
        },
    )
    return {"ok": True, "revoked_edit_template_ids": revoked}


@router.post("/{team_id}/members/{user_id}/admin", response_model=TeamMemberOut)
def grant_team_admin(
    team_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    ip: str | None = Depends(client_ip),
):
    """指定团队管理员。**仅平台管理员** —— 需求里团队管理员是由管理员指派的。"""
    team = team_service.get_team(db, team_id)
    target = team_service.set_team_admin(db, team, user_id, True)
    _audit(
        db, user, ip, ACTION_TEAM_ADMIN_GRANT, team,
        {"target_user_id": user_id, "target_user_name": target.name if target else None},
    )
    return team_service.member_of(db, team.id, user_id)


@router.delete("/{team_id}/members/{user_id}/admin", response_model=TeamMemberOut)
def revoke_team_admin(
    team_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    ip: str | None = Depends(client_ip),
):
    """取消团队管理员。允许清零(平台管理员能兜底),团队列表会标「无团队管理员」告警。"""
    team = team_service.get_team(db, team_id)
    target = team_service.set_team_admin(db, team, user_id, False)
    _audit(
        db, user, ip, ACTION_TEAM_ADMIN_REVOKE, team,
        {
            "target_user_id": user_id,
            "target_user_name": target.name if target else None,
            "remaining_team_admins": len(team_service.team_admin_ids(db, team.id)),
        },
    )
    return team_service.member_of(db, team.id, user_id)

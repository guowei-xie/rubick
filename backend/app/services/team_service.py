"""团队与成员:「谁在哪个团队、是不是团队管理员」这条事实的唯一出口。

本模块只回答**事实**,不做资源判定 —— 「谁能看/能改哪个任务」住在 permission_service,
两边职责不重叠(否则同一条规则会在两处漂移)。

平台管理员在本模块的 require_* 里**恒放行**:他不受团队约束(需求已定)。这条豁免只在这里
表述一次,调用方不要各自再写 `or user.role == ROLE_ADMIN`。

⚠️ **加成员 = 一次数据授权**:团队账号是共享的,成员能在任务编辑器里用它试跑任意 SQL
(见 models/team.py 与 credential_service)。所以成员只能是**开发者**——业务使用者拿的是
任务级授权,不该进团队。
"""
from __future__ import annotations

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, PermissionDeniedError, RubicError
from app.models.team import Team, TeamMember
from app.models.template import SqlTemplate
from app.models.user import ROLE_DEVELOPER, User, is_platform_admin


# ---------------------------------------------------------------- 事实查询


def get_team(db: Session, team_id: int) -> Team:
    team = db.get(Team, team_id)
    if team is None:
        raise NotFoundError("团队不存在")
    return team


def membership(db: Session, user: User) -> list[tuple[int, bool]]:
    """该用户的全部成员关系:[(team_id, is_team_admin)]。一次查询,供 TeamScope 构造。"""
    return list(
        db.execute(
            select(TeamMember.team_id, TeamMember.is_team_admin).where(
                TeamMember.user_id == user.id
            )
        ).all()
    )


def admin_team_ids_of(db: Session, user: User) -> set[int]:
    return {tid for tid, is_admin in membership(db, user) if is_admin}


def is_member(db: Session, user: User, team_id: int) -> bool:
    return (
        db.scalar(
            select(TeamMember.id).where(
                TeamMember.team_id == team_id, TeamMember.user_id == user.id
            )
        )
        is not None
    )


def is_team_admin(db: Session, user: User, team_id: int) -> bool:
    return bool(
        db.scalar(
            select(TeamMember.is_team_admin).where(
                TeamMember.team_id == team_id, TeamMember.user_id == user.id
            )
        )
    )


def team_admin_ids(db: Session, team_id: int) -> list[int]:
    """该团队全部团队管理员的 user_id。团队账号出问题时要通知的正是他们。"""
    return list(
        db.scalars(
            select(TeamMember.user_id).where(
                TeamMember.team_id == team_id, TeamMember.is_team_admin.is_(True)
            )
        )
    )


def my_teams(db: Session, user: User) -> list[tuple[Team, bool]]:
    """我所属的团队 + 我在其中是不是团队管理员。一次 join 查完。

    /auth/me 要的正是这两件事;分两次查(teams_of + admin_team_ids_of)是白跑一趟。
    """
    return [
        (team, bool(is_admin))
        for team, is_admin in db.execute(
            select(Team, TeamMember.is_team_admin)
            .join(TeamMember, TeamMember.team_id == Team.id)
            .where(TeamMember.user_id == user.id)
            .order_by(Team.id)
        )
    ]


def teams_of(db: Session, user: User) -> list[Team]:
    """我所属的团队(按 id)。"""
    return list(
        db.scalars(
            select(Team)
            .join(TeamMember, TeamMember.team_id == Team.id)
            .where(TeamMember.user_id == user.id)
            .order_by(Team.id)
        )
    )


def all_teams(db: Session) -> list[Team]:
    return list(db.scalars(select(Team).order_by(Team.id)))


def members_by_team(db: Session, team_ids) -> dict[int, list[dict]]:
    """一批团队的成员名单(展示用),**一次 join 查完**。

    团队列表、平台管理员的取数账号总览都要「每个团队的成员/管理员」——逐队查会变成 N 次
    查询,而管理员视角下 N 可能有几十。故批量入口才是主入口,单队的 members_of 转调它。
    """
    ids = list(team_ids)
    if not ids:
        return {}
    rows = db.execute(
        select(
            TeamMember.team_id,
            User.id, User.name, User.avatar, User.role,
            TeamMember.is_team_admin, TeamMember.created_at,
        )
        .join(TeamMember, TeamMember.user_id == User.id)
        .where(TeamMember.team_id.in_(ids))
        # 团队管理员排前,便于「谁能配账号」一眼可见
        .order_by(TeamMember.team_id, TeamMember.is_team_admin.desc(), User.id)
    ).all()
    out: dict[int, list[dict]] = {tid: [] for tid in ids}
    for team_id, uid, name, avatar, role, is_admin, joined in rows:
        out[team_id].append(
            {
                "user_id": uid, "name": name, "avatar": avatar, "role": role,
                "is_team_admin": bool(is_admin), "joined_at": joined,
            }
        )
    return out


def members_of(db: Session, team_id: int) -> list[dict]:
    """单个团队的成员名单。批量版见 members_by_team —— 名单的形状只在那里定义一次。"""
    return members_by_team(db, [team_id])[team_id]


def member_of(db: Session, team_id: int, user_id: int) -> dict | None:
    """名单里的某一行。给「刚改完这个人,把他这一行回给前端」用 ——
    比取回整份名单再 next(...) 挑一个诚实。"""
    return next(
        (m for m in members_of(db, team_id) if m["user_id"] == user_id), None
    )


def candidates(db: Session, team_id: int, q: str | None = None) -> list[dict]:
    """可加入本团队的候选人:**登录过的开发者**且尚不在本团队。

    刻意不走飞书通讯录(那是给业务授权选人的):团队成员必须已经是平台上的开发者,
    否则「只能添加开发者」这条规则会变成「先造个壳用户再改角色」。
    """
    joined = select(TeamMember.user_id).where(TeamMember.team_id == team_id)
    stmt = select(User.id, User.name, User.avatar, User.email).where(
        User.role == ROLE_DEVELOPER,
        User.is_active.is_(True),
        User.last_login_at.is_not(None),
        User.id.not_in(joined),
    )
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(User.name.like(like) | User.email.like(like))
    return [
        {"user_id": uid, "name": name, "avatar": avatar, "email": email}
        for uid, name, avatar, email in db.execute(stmt.order_by(User.id).limit(50)).all()
    ]


def template_counts(db: Session, team_ids) -> dict[int, int]:
    """一批团队各自的任务数,**含回收站里已下线的**,一次 GROUP BY 查完。
    删团队的卡点看的就是它。"""
    ids = list(team_ids)
    if not ids:
        return {}
    rows = db.execute(
        select(SqlTemplate.team_id, func.count())
        .where(SqlTemplate.team_id.in_(ids))
        .group_by(SqlTemplate.team_id)
    ).all()
    counts = dict(rows)
    return {tid: counts.get(tid, 0) for tid in ids}


def count_templates(db: Session, team_id: int) -> int:
    """单个团队的任务数。批量版见 template_counts。"""
    return template_counts(db, [team_id])[team_id]


# ---------------------------------------------------------------- 守卫
# 平台管理员恒放行:他不受团队约束。这条豁免只在这两个函数里表述一次。


def can_admin_team(db: Session, user: User, team_id: int) -> bool:
    """能不能治理这个团队(配团队账号、增删成员、授任务编辑权)。

    **不抛异常的孪生体**:require_team_admin 判的就是它。凡是「要不要显示写操作 / 要不要
    返回半机密字段」这类问题都问它,而不要各自再写一遍 `is_platform_admin(...) or ...`
    —— 平台管理员的豁免只在这里表述一次。
    """
    return is_platform_admin(user) or is_team_admin(db, user, team_id)


def require_team_admin_of_template(db: Session, user: User, tmpl) -> Team:
    """任务维度的团队管理员守卫:把「无主任务」这个特例收在一处。

    任务的 team_id 在 DB 层可空(见 models/template.py),所以每个「按任务找团队管理员」的
    入口都要先处理它为空的情形。写在这里,新增入口自动继承,不必各自记得补一遍。
    """
    if tmpl.team_id is None:
        raise RubicError("该任务还没有所属团队,请先联系平台管理员为它指定团队")
    return require_team_admin(db, user, tmpl.team_id)


def require_member(db: Session, user: User, team_id: int) -> Team:
    team = get_team(db, team_id)
    if is_platform_admin(user) or is_member(db, user, team_id):
        return team
    raise PermissionDeniedError(f"你不是团队《{team.name}》的成员")


def require_team_admin(db: Session, user: User, team_id: int) -> Team:
    team = get_team(db, team_id)
    if can_admin_team(db, user, team_id):
        return team
    raise PermissionDeniedError(f"只有团队《{team.name}》的团队管理员可以做这件事")


# ---------------------------------------------------------------- 维护


def create_team(
    db: Session, *, name: str, description: str | None, created_by: int | None
) -> Team:
    name = (name or "").strip()
    if not name:
        raise RubicError("请填写团队名称")
    if db.scalar(select(Team.id).where(Team.name == name)) is not None:
        raise RubicError(f"团队《{name}》已存在")
    team = Team(name=name, description=(description or None), created_by=created_by)
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


def update_team(
    db: Session, team: Team, *, name: str | None, description: str | None
) -> Team:
    if name is not None:
        name = name.strip()
        if not name:
            raise RubicError("团队名称不能为空")
        clash = db.scalar(select(Team.id).where(Team.name == name, Team.id != team.id))
        if clash is not None:
            raise RubicError(f"团队《{name}》已存在")
        team.name = name
    if description is not None:
        team.description = description or None
    db.commit()
    db.refresh(team)
    return team


def delete_team(db: Session, team: Team) -> int:
    """删除团队。团队下**还有任务(含回收站里已下线的)时拒绝** —— 与「数据源被任务引用时
    不可删」同一口径。任务是有价值的资产,不能因为删了个组织单元就连带失去归属。

    通过校验后连带清掉成员行与团队取数账号(它们脱离团队后毫无意义)。返回被清掉的账号数。
    """
    from app.services import credential_service

    n = count_templates(db, team.id)
    if n:
        raise RubicError(
            f"团队《{team.name}》下还有 {n} 个任务(含回收站中已下线的),"
            "请先转移到其他团队后再删除团队"
        )
    creds = credential_service.delete_for_team(db, team.id)
    db.execute(sa_delete(TeamMember).where(TeamMember.team_id == team.id))
    db.delete(team)
    db.commit()
    return creds


def add_member(
    db: Session, team: Team, user_id: int, *, is_team_admin_flag: bool, added_by: int | None
) -> User:
    """把一名**开发者**加入团队。

    只允许开发者:加成员等于把该团队取数账号的全部数据权限交给他,而业务使用者拿的是
    任务级授权,不该进团队(见模块 docstring)。
    """
    target = db.get(User, user_id)
    if target is None:
        raise NotFoundError("用户不存在")
    if target.role != ROLE_DEVELOPER:
        raise RubicError("只能添加「开发者」角色的用户为团队成员")
    if is_member(db, target, team.id):
        raise RubicError(f"{target.name} 已经是团队《{team.name}》的成员")
    db.add(
        TeamMember(
            team_id=team.id,
            user_id=target.id,
            is_team_admin=is_team_admin_flag,
            added_by=added_by,
        )
    )
    db.commit()
    return target


def remove_member(db: Session, team: Team, user_id: int) -> tuple[User | None, list[int]]:
    """把成员移出团队。返回 (被移除的用户, 被连带撤销编辑权的任务 id)。

    离队要连带撤销他在本团队任务上的 edit 授权:permission_service.can_edit 已经叠加了
    「仍在团队内」,残留行本身是惰性的;但不清掉的话,他重新入队时权限会**静默复活**,
    而且授权列表会长出一堆查不到主的行。
    """
    from app.services import permission_service

    row = db.scalar(
        select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == user_id)
    )
    if row is None:
        raise NotFoundError("该用户不是本团队成员")
    target = db.get(User, user_id)
    revoked = permission_service.revoke_edit_for_member(db, team_id=team.id, user_id=user_id)
    db.delete(row)
    db.commit()
    return target, revoked


def set_team_admin(db: Session, team: Team, user_id: int, flag: bool) -> User | None:
    """指定/取消团队管理员。

    刻意**允许清零**:平台管理员本就不受团队约束、能兜底配账号与授权,造一个
    「不能移除最后一名团队管理员」的不变量只会在组织调整时挡路。团队列表上标一个
    「无团队管理员」告警即可。
    """
    row = db.scalar(
        select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == user_id)
    )
    if row is None:
        raise NotFoundError("该用户不是本团队成员")
    row.is_team_admin = flag
    db.commit()
    return db.get(User, user_id)


def transfer_template(
    db: Session, tmpl: SqlTemplate, team: Team
) -> tuple[int | None, list[int]]:
    """把任务转移到另一个团队。返回 (原团队 id, 被撤销编辑权的 user_id 列表)。

    转移会同时改变任务的**可见范围**与**取数身份**(新团队的账号),所以:
      - 只有平台管理员可以做(路由层守卫);
      - 原团队里那些「指定任务编辑权」必须撤销 —— 它们的前提(同团队)已经不成立。
        这条连带规则只在这里写一次,路由只负责把返回值记进审计。
    """
    from app.services import permission_service

    old = tmpl.team_id
    if old == team.id:
        raise RubicError(f"任务已经属于团队《{team.name}》")
    revoked = permission_service.revoke_edit_for_template(db, tmpl.id)
    tmpl.team_id = team.id
    db.commit()
    return old, revoked

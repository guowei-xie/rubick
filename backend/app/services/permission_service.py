"""授权判定与授予 —— 「谁能看 / 能跑 / 能改哪个任务」只在本模块表述一次。

有效权限 = 平台角色 × 团队身份 × 显式授权行。三层各管一件事,不互相替代:

  平台角色 —— ROLE_ADMIN 全通(不受团队约束);ROLE_ADMIN/ROLE_DEVELOPER 才能建任务。
  团队身份 —— 「可见」与「可运行」的边界:同团队互为**内部人**(含草稿与他人的运行记录)。
  授权行   —— 两种用途:业务使用者的 view/run/download;团队内「指定任务的编辑权」edit。

**「开发者 ≈ 管理员」的旧口径已废除**:ROLE_DEVELOPER 一律在团队内取值。历史上
is_manager/is_template_owner 对任何开发者恒返回 True,于是开发者之间互相可见可编辑、
还能下载彼此的取数结果。那几个函数被**删除**(而非改写)是刻意的:只改函数体的话,
漏改的调用点不会有任何测试失败,而每个漏改点都是一次越权读数据;删掉名字能让每个调用点
在导入期就报错。

**为什么作者身份本身不授予任何权限**:需求已定「成员离队后任务留在团队,离队者不再可见/
可编辑」。若给作者一条独立的短路,离了队的人仍能看到自己写过的任务,与该需求直接冲突。
所以作者的权利派生自「他仍是该团队成员」。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, delete as sa_delete, false, or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import PermissionDeniedError
from app.models.permission import (
    ACTION_DOWNLOAD,
    ACTION_EDIT,
    ACTION_RUN,
    ACTION_VIEW,
    BUSINESS_ACTIONS,
    RESOURCE_TEMPLATE,
    SUBJECT_USER,
    Permission,
)  # noqa: F401
from app.models.query_job import QueryJob
from app.models.template import STATUS_PUBLISHED, SqlTemplate
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, User, is_platform_admin  # noqa: F401
from app.services import team_service, user_service

# 能建任务的角色(能进任务编辑器)。需要在 SQL 里按角色筛人时引用它,不另列一份。
# 刻意不再叫 MANAGER_ROLES:旧名字暗示「管理者对任务全通」,而那正是本次拆掉的短路。
AUTHOR_ROLES = (ROLE_ADMIN, ROLE_DEVELOPER)


def _as_int(v) -> int | None:
    """Permission 的 subject_id / resource_id 是自由字符串列(将来可能是组 id 等),
    非数字一律跳过。解析规则只在这里写一次。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def can_author(user: User) -> bool:
    """能不能进任务编辑器(建/改任务、试跑、读数据源下拉)。

    注意它只回答「有没有这项职能」,**不回答「能不能动这个任务」**——后者看 can_edit。
    """
    return user.role in AUTHOR_ROLES


# ---------------------------------------------------------------- 权限视角快照


@dataclass(frozen=True)
class TeamScope:
    """某个用户的权限视角快照:**一次请求算一次**,之后所有判定都是纯内存运算。

    任务列表是落地页,要对几百行逐行判可见/可编辑/可运行,逐行查库必然 N+1。故把
    「团队身份 + 授权行」两次批量查询的结果冻结在这里;判定函数只吃 scope 与已加载的
    任务行、不再碰 db —— 签名上就杜绝了「在循环里查库」的写法。
    """

    user_id: int
    is_admin: bool                                       # 平台管理员
    # 以下均给空集默认值:平台管理员全通,一个都用不到,构造时不必逐个传
    team_ids: frozenset[int] = frozenset()
    admin_team_ids: frozenset[int] = frozenset()         # ⊆ team_ids
    view_ids: frozenset[int] = frozenset()               # 以下四项都是**显式授权行**命中的任务 id
    run_ids: frozenset[int] = frozenset()
    download_ids: frozenset[int] = frozenset()
    edit_ids: frozenset[int] = frozenset()


def team_scope(db: Session, user: User) -> TeamScope:
    """算出该用户的权限视角。固定 2 次查询;平台管理员 0 次(全通,四个集合都用不到)。"""
    if is_platform_admin(user):
        return TeamScope(user.id, is_admin=True)

    rows = team_service.membership(db, user)
    by_action: dict[str, set[int]] = {}
    for action, rid in db.execute(
        select(Permission.action, Permission.resource_id).where(
            Permission.subject_type == SUBJECT_USER,
            Permission.subject_id == str(user.id),
            Permission.resource_type == RESOURCE_TEMPLATE,
        )
    ):
        tid = _as_int(rid)
        if tid is not None:
            by_action.setdefault(action, set()).add(tid)

    return TeamScope(
        user_id=user.id,
        is_admin=False,
        team_ids=frozenset(tid for tid, _ in rows),
        admin_team_ids=frozenset(tid for tid, is_admin in rows if is_admin),
        view_ids=frozenset(by_action.get(ACTION_VIEW, ())),
        run_ids=frozenset(by_action.get(ACTION_RUN, ())),
        download_ids=frozenset(by_action.get(ACTION_DOWNLOAD, ())),
        edit_ids=frozenset(by_action.get(ACTION_EDIT, ())),
    )


# ---------------------------------------------------------------- 单个任务的判定(纯内存)


def is_insider(scope: TeamScope, tmpl) -> bool:
    """团队内视角:平台管理员,或该任务所属团队的成员。

    它决定「能不能看到团队内部信息」—— 草稿与已下线任务、SQL 原文/最新版本、
    该任务下**他人的**运行记录。可见 ≠ 内部人:业务使用者被授权后只看得到已上线的那一面。
    """
    return scope.is_admin or (tmpl.team_id is not None and tmpl.team_id in scope.team_ids)


def can_view(scope: TeamScope, tmpl) -> bool:
    """可见 = 内部人(同团队,含草稿),或被授予 view 的**已上线**任务。"""
    if is_insider(scope, tmpl):
        return True
    return tmpl.status == STATUS_PUBLISHED and tmpl.id in scope.view_ids


def can_edit(scope: TeamScope, tmpl) -> bool:
    """可编辑(改 SQL / 上下线 / 对业务方授权)。四条口径,顺序即优先级:

      1. 平台管理员 —— 不受团队约束;
      2. 该任务所属团队的**团队管理员** —— 本团队全部任务,无需给自己授权;
      3. 任务作者,**且仍在该团队内**;
      4. 被授予该任务 edit 的成员,**且仍在该团队内**。

    3、4 都叠加「仍在团队内」:否则 team_members 行删了而 Permission 行还在时,
    权限会僵在那里。team_id 为空的「无主任务」fail-closed,只有平台管理员能动 ——
    宁可少给,也不要在数据没迁完时静默放行。
    """
    if scope.is_admin:
        return True
    if tmpl.team_id is None:
        return False
    if tmpl.team_id in scope.admin_team_ids:
        return True
    if tmpl.team_id not in scope.team_ids:
        return False
    return tmpl.author_id == scope.user_id or tmpl.id in scope.edit_ids


def can_run(scope: TeamScope, tmpl) -> bool:
    """可填参取数。已上线且有已发布版本是前提(未上线谁都跑不了,含作者)。

    内部人天然可运行:团队就是取数身份的边界,同团队跑的是同一个团队账号,不存在越权
    —— 拦住「运行」只是形式,成员照样能在编辑器里用同一个账号跑出同样的数据。
    团队外的人要显式 run 授权。
    """
    if not (tmpl.status == STATUS_PUBLISHED and tmpl.published_version_id is not None):
        return False
    return is_insider(scope, tmpl) or tmpl.id in scope.run_ids


def can_download(scope: TeamScope, tmpl) -> bool:
    return is_insider(scope, tmpl) or tmpl.id in scope.download_ids


def can_subscribe(scope: TeamScope, tmpl) -> bool:
    """能不能订阅这个任务(资格判定;任务是否开启了订阅计划由路由层叠加,那是业务状态
    不是权限)。口径 = can_view 且已上线:订阅的产出是运行结果,而 can_access_job 对
    非发起人的判据就是 can_view —— 用同一把尺,订阅者天然下载得到推送给他的结果。"""
    return tmpl.status == STATUS_PUBLISHED and can_view(scope, tmpl)


# ---------------------------------------------------------------- 列表收窄(SQL 谓词)


def visible_condition(scope: TeamScope):
    """任务列表的可见性谓词。None = 不加限制(**仅平台管理员**;`None = 全部` 这条约定
    自本次起只对平台管理员成立)。

    刻意返回谓词而不是 id 集合:开发者的可见集是「我所属团队的全部任务」,materialize 成
    id 再 IN(...) 会在落地页上多一次全表扫 + 一条巨长 SQL。谓词让数据库用 team_id 索引。
    什么都看不到时返回 false(),比 id.in_({-1}) 诚实。
    """
    if scope.is_admin:
        return None
    clauses = []
    if scope.team_ids:
        clauses.append(SqlTemplate.team_id.in_(scope.team_ids))
    if scope.view_ids:
        clauses.append(
            and_(SqlTemplate.status == STATUS_PUBLISHED, SqlTemplate.id.in_(scope.view_ids))
        )
    return or_(*clauses) if clauses else false()


def job_visibility_condition(scope: TeamScope):
    """运行记录列表的可见口径:本人发起的 + 我所属团队任务下的全部运行。None = 全部。

    与 tasks.task_run_records 同源 —— 否则会出现「抽屉里看得到、列表里看不到」。
    """
    if scope.is_admin:
        return None
    own = QueryJob.user_id == scope.user_id
    if not scope.team_ids:
        return own
    return or_(
        own,
        QueryJob.template_id.in_(
            select(SqlTemplate.id).where(SqlTemplate.team_id.in_(scope.team_ids))
        ),
    )


def manageable_template_ids(db: Session, scope: TeamScope) -> set[int] | None:
    """我可以对之授权/撤销的任务 id;None = 全部(平台管理员)。

    这里必须是 id 集合而非谓词:Permission.resource_id 是 VARCHAR,与 templates 做 cast join
    在 MySQL/SQLite 上都不安全(同 authorized_run_users 的理由)。授权页不是落地页,
    一次额外查询可以接受。
    """
    if scope.is_admin:
        return None
    if not scope.team_ids:
        return set()
    # 列级 Row 恰好带着 can_edit 需要的三个字段,直接喂给它 —— 编辑权的规则只在 can_edit
    # 里写一次,免得这里再抄一遍、日后改一处漏一处
    rows = db.execute(
        select(SqlTemplate.id, SqlTemplate.team_id, SqlTemplate.author_id).where(
            SqlTemplate.team_id.in_(scope.team_ids)
        )
    ).all()
    return {r.id for r in rows if can_edit(scope, r)}


# ---------------------------------------------------------------- 便捷入口(自取 scope)


def can(db: Session, user: User, action: str, resource_type: str, resource_id: int | str) -> bool:
    """业务侧动作(view/run/download)的判定,给只有 id 在手的调用点。

    **edit 不走这里**:编辑权是团队内治理动作,入口是 can_edit / can_edit_template。
    """
    if is_platform_admin(user):
        return True
    if resource_type != RESOURCE_TEMPLATE:
        return False
    tmpl = db.get(SqlTemplate, int(resource_id))
    if tmpl is None:
        return False
    scope = team_scope(db, user)
    if action == ACTION_VIEW:
        return can_view(scope, tmpl)
    if action == ACTION_RUN:
        return can_run(scope, tmpl)
    if action == ACTION_DOWNLOAD:
        return can_download(scope, tmpl)
    return False


def can_edit_template(db: Session, user: User, template_id: int | str) -> bool:
    """按 id 判编辑权。平台管理员在**取任务之前**就短路:他的权限不取决于这一行是否存在,
    而调用方(如授权接口)对不存在的任务本就该报 404 而不是 403 —— 别让「无权」掩盖「不存在」。
    """
    if is_platform_admin(user):
        return True
    tmpl = db.get(SqlTemplate, int(template_id))
    if tmpl is None:
        return False
    return can_edit(team_scope(db, user), tmpl)


def can_access_job(db: Session, user: User, job) -> bool:
    """一次运行记录/结果的可见性:发起人本人,或对该任务可见的人。

    **唯一入口** —— query.get_job / preview / download 与 tasks.task_run_records 都走它。
    签名从 (user, job) 改成带 db 是必须的:团队判定得知道任务归属,拿不到 db 就只能退回
    「管理者全通」,而那正是本次要拆掉的短路(历史上任何开发者都能下载任何人的结果)。
    """
    if is_platform_admin(user) or job.user_id == user.id:
        return True
    tmpl = db.get(SqlTemplate, job.template_id)
    if tmpl is None:
        return False
    return can_view(team_scope(db, user), tmpl)


def require_can_create_in_team(db: Session, user: User, team_id: int | None) -> None:
    """建任务 / 编辑器试跑时校验团队:开发者只能用自己所属的团队,平台管理员可用任意存在的团队。

    「开发者必须先有团队才能建任务」这条需求就落在这里,所以报错要直接告诉他找谁。
    """
    if team_id is None:
        raise PermissionDeniedError("请选择任务所属团队")
    if is_platform_admin(user):
        team_service.get_team(db, int(team_id))
        return
    if not team_service.teams_of(db, user):
        raise PermissionDeniedError(
            "你还不属于任何团队,请联系平台管理员把你加入一个团队后再建任务"
        )
    team = team_service.get_team(db, int(team_id))
    if not team_service.is_member(db, user, team.id):
        raise PermissionDeniedError(f"你不是团队《{team.name}》的成员,不能使用该团队建任务")


# ---------------------------------------------------------------- 展示辅助


def authorized_run_users(db: Session, template_ids: list[int]) -> dict[int, list[dict]]:
    """每个模板「显式授权了 run 动作」的用户列表(id/name/avatar),按模板 id 分组。

    两步查询(仿 permissions.py::_enrich):先取授权行,再按 user.id 批量取用户,避免对
    subject_id(String) 做 cast join,MySQL/SQLite 皆安全,且 O(1) 次查询无 N+1。
    仅含显式授权用户;团队内部人的隐式权限不入列(即卡片上的「参与者」语义)。
    """
    if not template_ids:
        return {}
    rid_strs = [str(i) for i in template_ids]
    rows = list(
        db.execute(
            select(Permission.resource_id, Permission.subject_id).where(
                Permission.resource_type == RESOURCE_TEMPLATE,
                Permission.action == ACTION_RUN,
                Permission.subject_type == SUBJECT_USER,
                Permission.resource_id.in_(rid_strs),
            )
        )
    )
    if not rows:
        return {}

    pairs = [(_as_int(rid), _as_int(sid)) for rid, sid in rows]
    uid_ints = {uid for _, uid in pairs if uid is not None}
    if not uid_ints:
        return {}
    users = {
        u.id: {"id": u.id, "name": u.name, "avatar": u.avatar}
        for u in db.execute(select(User.id, User.name, User.avatar).where(User.id.in_(uid_ints)))
    }
    grouped: dict[int, list[dict]] = {}
    for rid, uid in pairs:
        u = users.get(uid) if uid is not None else None
        if rid is not None and u:
            grouped.setdefault(rid, []).append(u)
    return grouped


# ---------------------------------------------------------------- 授予 / 撤销


def grant(
    db: Session,
    *,
    subject_type: str,
    resource_type: str,
    resource_id: str,
    actions: list[str],
    granted_by: int | None,
    subject_id: str | None = None,
    subject_open_id: str | None = None,
    subject_profile: dict | None = None,
) -> list[Permission]:
    """业务授权(view/run/download)。edit 不从这里进 —— 见 grant_edit。

    动作白名单在此再兜一道:入参层已用 Literal 收口(schemas/permission.py),但这个函数
    也被其它调用点用,漏一处就是一条提权路径。
    """
    bad = [a for a in actions if a not in BUSINESS_ACTIONS]
    if bad:
        raise PermissionDeniedError(
            f"不支持的授权动作:{'、'.join(bad)}。任务编辑权请在任务的「编辑人」里授予"
        )
    # 主体解析集中在服务层(单一事务归属):传 open_id 时在此(而非搜索时)按 open_id upsert
    # 用户、拿其 id 作主体;否则用已知的 subject_id。
    if subject_open_id:
        # 客户端传来的资料只取「展示用」这两项(正向白名单:塞别的进来也进不了库)。
        # **email 绝不采信客户端**:它是 BOOTSTRAP_ADMINS 的匹配键,可写就是一条提权路径
        # (见 schemas/permission.py),只能由通讯录写 —— 与登录走同一条 sync 路径。
        display = {k: v for k in ("name", "avatar") if (v := (subject_profile or {}).get(k))}
        subject = user_service.upsert_user(db, {"open_id": subject_open_id, **display})
        if not subject.email:  # 已有可信邮箱就不必再打一次飞书
            user_service.sync_profiles_from_feishu([subject])
        db.flush()  # 拿到自增 id;与下方授权同一事务提交
        subject_id = str(subject.id)

    created = _add_rows(
        db,
        subject_type=subject_type,
        subject_id=subject_id,
        resource_type=resource_type,
        resource_id=resource_id,
        actions=actions,
        granted_by=granted_by,
    )
    db.commit()
    return created


def _add_rows(
    db: Session, *, subject_type, subject_id, resource_type, resource_id, actions, granted_by
) -> list[Permission]:
    """幂等写入授权行(已存在的动作跳过)。不提交,跟随调用方事务。"""
    created: list[Permission] = []
    for action in actions:
        exists = db.scalar(
            select(Permission).where(
                Permission.subject_type == subject_type,
                Permission.subject_id == subject_id,
                Permission.resource_type == resource_type,
                Permission.resource_id == resource_id,
                Permission.action == action,
            )
        )
        if exists:
            continue
        p = Permission(
            subject_type=subject_type,
            subject_id=subject_id,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            granted_by=granted_by,
        )
        db.add(p)
        created.append(p)
    return created


# ---- 指定任务的编辑权(团队内,只由 /api/tasks/{id}/editors 进出)


def _edit_where(*, user_id: int | None = None, template_id: int | None = None, template_ids=None):
    """定位 edit 授权行的谓词。「哪些行算是任务编辑权」只在这里写一次 ——
    下面五个增删查都引用它,免得某处漏一个条件就误删了业务授权。"""
    clauses = [
        Permission.subject_type == SUBJECT_USER,
        Permission.resource_type == RESOURCE_TEMPLATE,
        Permission.action == ACTION_EDIT,
    ]
    if user_id is not None:
        clauses.append(Permission.subject_id == str(user_id))
    if template_id is not None:
        clauses.append(Permission.resource_id == str(template_id))
    if template_ids is not None:
        clauses.append(Permission.resource_id.in_([str(i) for i in template_ids]))
    return and_(*clauses)


def grant_edit(db: Session, *, template_id: int, user_id: int, granted_by: int | None) -> bool:
    """授予某成员某任务的编辑权。幂等,返回是否新建了行。"""
    created = _add_rows(
        db,
        subject_type=SUBJECT_USER,
        subject_id=str(user_id),
        resource_type=RESOURCE_TEMPLATE,
        resource_id=str(template_id),
        actions=[ACTION_EDIT],
        granted_by=granted_by,
    )
    db.commit()
    return bool(created)


def revoke_edit(db: Session, *, template_id: int, user_id: int) -> bool:
    n = db.execute(
        sa_delete(Permission).where(_edit_where(user_id=user_id, template_id=template_id))
    ).rowcount or 0
    db.commit()
    return bool(n)


def _edit_rows_for(db: Session, *, user_id: int, template_ids: list[int]) -> list[int]:
    if not template_ids:
        return []
    rows = db.scalars(
        select(Permission.resource_id).where(
            _edit_where(user_id=user_id, template_ids=template_ids)
        )
    )
    return [tid for tid in map(_as_int, rows) if tid is not None]


def revoke_edit_for_member(db: Session, *, team_id: int, user_id: int) -> list[int]:
    """成员离队时清掉他在该团队全部任务上的 edit 授权。返回被清的任务 id(进审计 detail)。

    can_edit 已叠加「仍在团队内」,残留行本身是惰性的;但不清掉的话,他重新入队时
    权限会**静默复活**,而且授权列表会长出一堆查不到主的行。不提交,跟随调用方事务。
    """
    tids = list(
        db.scalars(select(SqlTemplate.id).where(SqlTemplate.team_id == team_id))
    )
    hit = _edit_rows_for(db, user_id=user_id, template_ids=tids)
    if hit:
        db.execute(
            sa_delete(Permission).where(_edit_where(user_id=user_id, template_ids=hit))
        )
    return sorted(hit)


def revoke_edit_for_template(db: Session, template_id: int) -> list[int]:
    """任务转移团队时清掉它的全部 edit 授权(前提「同团队」已不成立)。
    返回被撤销的 user_id 列表。不提交,跟随调用方事务。"""
    rows = db.scalars(select(Permission.subject_id).where(_edit_where(template_id=template_id)))
    uids = [uid for uid in map(_as_int, rows) if uid is not None]
    if uids:
        db.execute(sa_delete(Permission).where(_edit_where(template_id=template_id)))
    return sorted(uids)


def editors_by_template(db: Session, tmpls: list[SqlTemplate]) -> dict[int, list[dict]]:
    """一批任务各自的「编辑人」名单,**固定 3 次查询**,与任务数无关。

    团队页的「任务编辑权」面板要给每个任务显示这份名单 —— 逐个调 editors_of 会变成
    N 次调用 × 每次 2 次查询,再加 N 个 HTTP 往返。批量入口才是主入口。
    """
    if not tmpls:
        return {}
    team_admins: dict[int, list[int]] = {}
    for team_id in {t.team_id for t in tmpls if t.team_id is not None}:
        team_admins[team_id] = team_service.team_admin_ids(db, team_id)

    granted: dict[int, list[int]] = {}
    for rid, sid in db.execute(
        select(Permission.resource_id, Permission.subject_id).where(
            _edit_where(template_ids=[t.id for t in tmpls])
        )
    ):
        tid, uid = _as_int(rid), _as_int(sid)
        if tid is not None and uid is not None:
            granted.setdefault(tid, []).append(uid)

    # 每个任务的 (uid → source),同一人命中多条口径时保留最先的那个
    per_task: dict[int, dict[int, str]] = {}
    for t in tmpls:
        first: dict[int, str] = {t.author_id: "author"}
        for uid in team_admins.get(t.team_id, ()):
            first.setdefault(uid, "team_admin")
        for uid in granted.get(t.id, ()):
            first.setdefault(uid, "granted")
        per_task[t.id] = first

    uids = {uid for m in per_task.values() for uid in m}
    users = {
        u.id: u
        for u in db.execute(
            select(User.id, User.name, User.avatar).where(User.id.in_(uids or {-1}))
        )
    }
    return {
        tid: [
            {"user_id": u.id, "name": u.name, "avatar": u.avatar, "source": source}
            for uid, source in m.items()
            if (u := users.get(uid)) is not None
        ]
        for tid, m in per_task.items()
    }


def editors_of(db: Session, tmpl: SqlTemplate) -> list[dict]:
    """单个任务的「编辑人」名单。名单的形状只在 editors_by_template 里定义一次。"""
    return editors_by_template(db, [tmpl])[tmpl.id]

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

from app.core.exceptions import PermissionDeniedError, RubicError
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
from app.models.query_job import SOURCE_SUBSCRIBE, QueryJob
from app.models.team import TeamMember
from app.models.template import STATUS_PUBLISHED, SqlTemplate
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, User, is_platform_admin  # noqa: F401
from app.services import team_service, user_service

# 能建任务的角色(能进任务编辑器)。需要在 SQL 里按角色筛人时引用它,不另列一份。
# 刻意不再叫 MANAGER_ROLES:旧名字暗示「管理者对任务全通」,而那正是本次拆掉的短路。
AUTHOR_ROLES = (ROLE_ADMIN, ROLE_DEVELOPER)


def parse_subject_id(v) -> int | None:
    """Permission 的 subject_id / resource_id 是自由字符串列(将来可能是组 id 等),
    非数字一律跳过。解析规则只在这里写一次。

    公开而不是私有,是因为运营分析也要配对这两列:它算「授权了但从没跑过」时必须用
    **同一条**解析规则,否则两边对「哪些授权行算数」的判断会悄悄分家 ——
    一边跳过的脏行另一边当成了合法 id。
    """
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
    is_admin: bool                                       # 平台管理员;它是**叠加在下列事实之上的放行位**
    # 以下集合对**所有人**如实填(含平台管理员)。曾经为管理员省掉这两次查询,可 is_admin
    # 只该表达「他全通」,不该让这些集合对他变成谎话 —— 判定函数放行是因为 is_admin,
    # 而想知道「他本人被授予过什么」的读者(如 is_author_or_grantee)照样读得到真相
    team_ids: frozenset[int] = frozenset()
    admin_team_ids: frozenset[int] = frozenset()         # ⊆ team_ids
    view_ids: frozenset[int] = frozenset()               # 以下四项都是**显式授权行**命中的任务 id
    run_ids: frozenset[int] = frozenset()
    download_ids: frozenset[int] = frozenset()
    edit_ids: frozenset[int] = frozenset()


def team_scope(db: Session, user: User) -> TeamScope:
    """算出该用户的权限视角。固定 2 次查询,**平台管理员也一样**。

    不给管理员开「0 查询」的快路径:省下的两次索引查询换来的是一份对他失真的快照 ——
    集合空着,读的人分不清「没被授予过」还是「没查」。放行由 is_admin 一位负责就够了。
    """
    rows = team_service.membership(db, user)
    by_action: dict[str, set[int]] = {}
    for action, rid in db.execute(
        select(Permission.action, Permission.resource_id).where(
            Permission.subject_type == SUBJECT_USER,
            Permission.subject_id == str(user.id),
            Permission.resource_type == RESOURCE_TEMPLATE,
        )
    ):
        tid = parse_subject_id(rid)
        if tid is not None:
            by_action.setdefault(action, set()).add(tid)

    return TeamScope(
        user_id=user.id,
        is_admin=is_platform_admin(user),
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


def is_author_or_grantee(scope: TeamScope, tmpl) -> bool:
    """这个人对该任务有**本人的**编辑主张:我建的,或被显式授予了该任务的 edit。

    即 can_edit 阶梯的第 3、4 条(不含平台管理员 / 团队管理员那两条**治理**权限)。
    与 editors_by_template 的 source 分类同源:author / granted 算,team_admin 不算。
    刻意不叠「仍在团队内」—— 那是 can_edit 的前提,由它在调用前判完。
    任务列表的「我开发的」筛选也读这一条(见 TaskOut.developed_by_me)。
    """
    return tmpl.author_id == scope.user_id or tmpl.id in scope.edit_ids


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
    return is_author_or_grantee(scope, tmpl)


def can_transfer_author(scope: TeamScope, tmpl) -> bool:
    """能不能把这个任务的作者转给别人(离职交接)。

    口径 = can_edit **减去**「被授予该任务 edit」那一条:
      平台管理员 / 该任务所属团队的团队管理员 / 作者本人(且仍在团队内)。

    **为什么被授予 edit 的人不算**:edit 是「来帮着改这个 SQL」的委托,不是「这份资产归谁」
    的处分权。让它包含转移,等于任何一个被临时拉来改 SQL 的同事都能把作者改成自己,
    而授予他 edit 的团队管理员事后只能从审计里发现 —— 与 models/permission.py 把 edit
    和业务授权分成两个入口所防的是同一类提权。

    写成「can_edit 为前置 + 再挑一次」而不是重列四条阶梯:团队约束(仍在团队内、
    team_id 为空 fail-closed)只在 can_edit 里表述一次,那里放宽或收紧,这里自动跟随。
    刻意不用 is_author_or_grantee —— 它把 edit_ids 也 OR 了进来,正是要排除的那一条。

    无主任务恒 False,**连平台管理员也是**:接收人被定义为「该任务所属团队的成员」,
    没有团队就没有任何合法接收人。与其让他点开一个空下拉,不如在守卫里说清下一步。
    """
    if tmpl.team_id is None:
        return False
    if not can_edit(scope, tmpl):
        return False
    return (
        scope.is_admin
        or tmpl.team_id in scope.admin_team_ids
        or tmpl.author_id == scope.user_id
    )


def transfer_author_role(scope: TeamScope, tmpl) -> str:
    """发起人是**以什么身份**转移这个作者的:author / team_admin / platform_admin。

    与 can_transfer_author 的三条放行条件一一对应,且**紧挨着它**:审计要靠这个标签分清
    「本人主动交接」与「管理员代办」,而阶梯一旦加条目(代管人、副管理员……),在别处
    重新数一遍的那份会静默把新身份记成 author —— 不报错、不挂测试,只在半年后的复盘里
    给出一个假答案。

    优先级是「最贴身的主张优先」,同 editors_by_template 的 source 分类:作者本人哪怕
    同时是团队管理员,他转自己的任务也是**本人交接**,不是代办。
    """
    if tmpl.author_id == scope.user_id:
        return "author"
    return "team_admin" if tmpl.team_id in scope.admin_team_ids else "platform_admin"


def author_transfer_denial(scope: TeamScope, tmpl) -> str | None:
    """不能由 scope 这个人处分该任务的理由;能处分则返回 None。**纯内存,不碰 db。**

    三种拒绝要给三句不同的话:「无权」若盖住了「这个任务还没有团队」或「你的编辑权不含
    处分权」,人只会反复重试同一个按钮。这条约定在批量里更要紧 —— 整批拒绝的清单上,
    12 条「无权修改该任务」等于什么都没说。

    单任务守卫(require_can_transfer_author)与批量计划器(template_service.author_transfer_plan)
    都转调它:**前端置灰时显示的那句话,与点下去会说的那句话,来自同一次调用**。
    无主任务的**判据**也收在这里(话术仍只在 team_service 里写一次),否则批量那边会另写
    一个 `tmpl.team_id is None`,哪天「无主」的定义变了要改两处。
    团队名取 tmpl.team_name(models/template.py 的 team 关系是 lazy="joined"),
    不额外查库,故批量里判几百条也是零查询。
    """
    if tmpl.team_id is None:
        return team_service.NO_TEAM_MESSAGE
    if can_transfer_author(scope, tmpl):
        return None
    if can_edit(scope, tmpl):
        return (
            f"任务编辑权不含「转移作者」;请让作者本人或团队《{tmpl.team_name}》的团队管理员操作"
        )
    return "无权修改该任务"


def require_can_transfer_author(db: Session, user: User, tmpl) -> tuple[object, str]:
    """作者转移的守卫。返回 (任务所属团队, 发起人身份)。

    身份一并回来,免得调用方为了拼审计标签再建一次 TeamScope(那是固定 2 次查询),
    更免得它在路由里把放行条件重新数一遍。

    无主任务由 team_of_template 先抛(400,且那句指引只在它那里写一次);其余两种拒绝的
    话术转调 author_transfer_denial —— 批量路径要把同样的话当成清单里的一行,不能两处各写。
    """
    team = team_service.team_of_template(db, tmpl)
    scope = team_scope(db, user)
    denial = author_transfer_denial(scope, tmpl)
    if denial is not None:
        raise PermissionDeniedError(denial)
    return team, transfer_author_role(scope, tmpl)


def visible_templates(db: Session, scope: TeamScope) -> list[SqlTemplate]:
    """我看得见的任务(整份实体,按 id 倒序)。「可见集怎么取」只在这里写一次。

    任务列表(routes.tasks.list_tasks)、/templates、开放 API 的 /v1/tasks 与批量交接的
    候选人接口都走它 —— 各写一遍 `select + visible_condition` 的话,任何加在可见集上的
    条件(软删、归档、scope 变形)只会改到其中一处,而另一处的产物正是前端的置灰依据。
    """
    stmt = select(SqlTemplate).order_by(SqlTemplate.id.desc())
    cond = visible_condition(scope)
    return list(db.scalars(stmt if cond is None else stmt.where(cond)))


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


def is_subscribable(tmpl) -> bool:
    """订阅资格里**与人无关**的那一半:任务得已上线。

    具名出来是因为代订阅要单独问它 —— 那条路径会先给对方补一条 view 授权,所以「人侧」
    那一半由它自己制造,只剩任务侧要判。路由层再抄一次 `status == PUBLISHED` 就会让
    这条判据有两个真相源:哪天它变成「已上线且未归档」,抄的那份会静默留在旧口径。
    """
    return tmpl.status == STATUS_PUBLISHED


def can_subscribe(scope: TeamScope, tmpl) -> bool:
    """能不能订阅这个任务(资格判定;任务是否开启了订阅计划由路由层叠加,那是业务状态
    不是权限)。口径 = can_view 且已上线:订阅的产出是运行结果,而结果的可见判据
    (can_access_job 对非发起人)就是 can_view —— 用同一把尺。

    「看得见」到此为止,取不取得走完整 CSV 是另一问,见 can_download_job。"""
    return is_subscribable(tmpl) and can_view(scope, tmpl)


def viewers_among(db: Session, user_ids: list[int], tmpl) -> set[int]:
    """这批人里,**此刻已经看得见这一个任务**的是哪些。固定 2 次查询,与人数无关。

    它是 can_view 的批量反问:can_view 问「这个人能看见哪些任务」(为此要 TeamScope,
    一个人的全景视角,固定 2 次查询),这里问「这 50 个人里谁看得见这一个任务」——
    拿前者回答后者就是 2N 次往返,正是 TeamScope 那段注释在禁的 N+1。
    形状仿 authorized_run_users:先批量取授权行,再在内存里配对,不对 String 列做 cast join。
    """
    if not user_ids:
        return set()
    users = list(db.scalars(select(User).where(User.id.in_(user_ids))))
    seen = {u.id for u in users}
    # ① 平台管理员全通
    ok = {u.id for u in users if is_platform_admin(u)}
    # ② 内部人:任务所属团队的成员(无主任务 fail-closed,同 is_insider)
    if tmpl.team_id is not None:
        ok |= set(
            db.scalars(
                select(TeamMember.user_id).where(
                    TeamMember.team_id == tmpl.team_id, TeamMember.user_id.in_(user_ids)
                )
            )
        )
    # ③ 被显式授予该任务 view 的人 —— 仅当任务已上线(同 can_view 对被授权人的叠加条件)
    if is_subscribable(tmpl):
        for sid in db.scalars(
            select(Permission.subject_id).where(
                Permission.subject_type == SUBJECT_USER,
                Permission.resource_type == RESOURCE_TEMPLATE,
                Permission.resource_id == str(tmpl.id),
                Permission.action == ACTION_VIEW,
                Permission.subject_id.in_([str(i) for i in user_ids]),
            )
        ):
            uid = parse_subject_id(sid)
            if uid is not None:
                ok.add(uid)
    return ok & seen


# ---------------------------------------------------------------- 列表收窄(SQL 谓词)


def visible_condition(scope: TeamScope):
    """任务列表的可见性谓词。None = 不加限制(**仅平台管理员**;`None = 全部` 这条约定
    自本次起只对平台管理员成立)。

    刻意返回谓词而不是 id 集合:开发者的可见集是「我所属团队的全部任务」,materialize 成
    id 再 IN(...) 会在落地页上多一次全表扫 + 一条巨长 SQL。谓词让数据库用 team_id 索引。
    什么都看不到时返回 false(),比 id.in_({-1}) 诚实。

    **它还是一条不变量的基准**:运营分析自己按团队收窄(analytics_service.job_conditions,
    刻意不复用本函数 —— 那边问的是「这一个团队的资产」,不是「这个人看得见什么」),
    但它的结果必须恒 ⊆ 本函数放行的集合,否则就成了一条绕过本模块的提权侧门。
    这条由 tests/test_analytics_scope.py 钉住;放宽本函数的语义前先看一眼那个测试。
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
    """运行记录列表的可见口径:本人发起的 + 我所属团队任务下的全部运行
    + **被授予 view 的任务下的定时运行**。None = 全部(仅平台管理员)。

    「谁能列到哪些运行」只在这里表述一次:/api/jobs 与 /api/tasks/{id}/jobs 都调它。
    从前路由层另写了一份等价谓词,放宽时漏改一边,订阅者就收得到通知却看不到结果 ——
    那次之后这里是唯一入口(单条记录的对应物是 can_access_job)。

    第三支是给订阅者的:定时运行挂在系统用户名下(query_service.enqueue_scheduled),
    既不是「本人发起」也不落在团队里,不单列出来,被授权的业务使用者就永远看不到
    推送给他的那份数据。**只补 view_ids 这一支**:团队任务下的定时运行已被第二支整个
    覆盖(它不挑 source),所以这里不能图省事直接套 visible_condition —— 那会把同一个
    模板子查询原样跑两遍,而对没有任何授权的人还多挂一条 WHERE false 的死子查询。
    """
    if scope.is_admin:
        return None
    clauses = [QueryJob.user_id == scope.user_id]
    if scope.team_ids:
        clauses.append(
            QueryJob.template_id.in_(
                select(SqlTemplate.id).where(SqlTemplate.team_id.in_(scope.team_ids))
            )
        )
    if scope.view_ids:
        clauses.append(
            and_(
                QueryJob.source == SOURCE_SUBSCRIBE,
                QueryJob.template_id.in_(
                    select(SqlTemplate.id).where(
                        SqlTemplate.status == STATUS_PUBLISHED,
                        SqlTemplate.id.in_(scope.view_ids),
                    )
                ),
            )
        )
    return or_(*clauses)


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


def can_access_job(db: Session, user: User, job, *, scope: TeamScope | None = None, tmpl=None) -> bool:
    """一次运行记录/结果的可见性:发起人本人,或对该任务可见的人。

    **唯一入口** —— query.get_job / preview / download 与 tasks.task_run_records 都走它。
    签名从 (user, job) 改成带 db 是必须的:团队判定得知道任务归属,拿不到 db 就只能退回
    「管理者全通」,而那正是本次要拆掉的短路(历史上任何开发者都能下载任何人的结果)。

    scope / tmpl 可由调用方注入:一条请求里若要连问两次(如下载路径先问可见、再问可下载),
    传进来就只算一次 TeamScope(它固定 2 次查询)。不传则自取,老调用点一个都不用改。
    """
    if is_platform_admin(user) or job.user_id == user.id:
        return True
    if tmpl is None:
        tmpl = db.get(SqlTemplate, job.template_id)
    if tmpl is None:
        return False
    return can_view(scope or team_scope(db, user), tmpl)


def can_download_job(db: Session, user: User, job, *, scope: TeamScope | None = None, tmpl=None) -> bool:
    """能不能把这一次运行的**完整结果文件**取走。与 can_access_job 并排:
    那条答「看得见这条记录吗」(不可见 → 404),这条答「拿得走文件吗」(没授权 → 403)。

    **刻意不继承 can_access_job 的发起人短路**(job.user_id == user.id):
    「能跑、能看前 50 行预览,取走完整结果另需授权」是产品口径(三份手册都这么写),
    给发起人开短路等于把这道闸架空 —— 任何有 run 权限的人自己跑一次就绕过去了。
    平台管理员与任务所属团队成员由 can_download 的 is_insider 放行,这里不重复一遍。

    订阅推送的那一期结果对在册订阅者放行:代订阅只补 view(见 subscription_service
    .subscribe_for),而订阅本身就是「把这份结果定期送给你」的承诺 —— 看得见却取不走
    是半截承诺(可见那半在 job_visibility_condition 里)。放行只限订阅跑出来的那些期,
    同任务下**他人手动跑**的结果仍要 download 授权。
    **判据是「此刻在册」,不是「这一期当初推给过他」**:今天订阅的人也取得走上个月那几期。
    两者的差别要紧到需要区分时,得按 TaskSubscription.created_at 与 job.created_at 比 ——
    那是一条新的产品口径,不要顺手加。

    scope / tmpl 同 can_access_job,可注入以免一条请求里重复算 TeamScope。
    """
    if is_platform_admin(user):
        return True
    if tmpl is None:
        tmpl = db.get(SqlTemplate, job.template_id)
    if tmpl is not None and can_download(scope or team_scope(db, user), tmpl):
        return True
    if job.source != SOURCE_SUBSCRIBE:
        return False
    # 延迟 import:订阅三张表的读写口只在 subscription_service,权限层不自己 select
    # (顶层 import 会与它的 permission_service 反向依赖撞上,仓库里这类破环都用延迟 import)
    from app.services import subscription_service

    return subscription_service.is_subscriber(db, job.template_id, user.id)


def require_can_download_job(
    db: Session, user: User, job, *, scope: TeamScope | None = None, tmpl=None
) -> None:
    """下载闸的**唯一守卫**。两个**带身份**的下载入口(界面的签名 URL 签发步、开放 API 的
    Bearer 直出)共用同一个 assert_downloadable,所以「被挡的人该找谁」这句话只写一次。
    """
    if tmpl is None:
        tmpl = db.get(SqlTemplate, job.template_id)
    if can_download_job(db, user, job, scope=scope, tmpl=tmpl):
        return
    which = f"《{tmpl.name}》(#{tmpl.id})" if tmpl is not None else f"#{job.template_id}"
    who = (
        f"任务作者或团队《{tmpl.team_name}》的团队管理员"
        if tmpl is not None and tmpl.team_name
        else "任务作者"
    )
    raise PermissionDeniedError(
        f"无权下载任务{which}的完整结果:你可以运行它、也可以预览前 50 行,"
        f"取走完整 CSV 另需「下载」授权 —— 请联系{who}在任务卡片的授权入口勾上「下载」"
    )


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

    pairs = [(parse_subject_id(rid), parse_subject_id(sid)) for rid, sid in rows]
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


def resolve_subject_users(db: Session, profiles: list[dict]) -> dict[str, User]:
    """把一批飞书 open_id 解析成已落库的 User(不存在则此刻建),补齐可信邮箱,
    返回 {open_id: User}。**不提交**:一次 flush 拿全部自增 id,跟随调用方事务 ——
    调用方回滚时连壳用户都不会留下。

    入参每项形如 {"open_id": ..., "name": ..., "avatar": ...}。落库推迟到「真正要用他」
    这一刻(而不是搜索时)的理由见 routes/lookup.py。

    **批量是原语,单个是它的一元特例**(见 resolve_subject_user):通讯录接口本来就按批取
    (feishu_service.fetch_contact_profiles,50 一批),逐人调等于把一次请求拆成 N 次外网往返。

    客户端传来的资料只取「展示用」这两项(正向白名单:塞别的进来也进不了库)。
    **email 绝不采信客户端**:它是 BOOTSTRAP_ADMINS 的匹配键,可写就是一条提权路径
    (见 schemas/permission.py),只能由通讯录写 —— 与登录走同一条 sync 路径。
    这条规则只在这里表述一次:业务授权(grant)与代订阅共用本函数。
    """
    resolved: dict[str, User] = {}
    for profile in profiles:
        open_id = profile["open_id"]
        if open_id in resolved:  # 同一个人被勾了两次
            continue
        display = {k: v for k in ("name", "avatar") if (v := profile.get(k))}
        resolved[open_id] = user_service.upsert_user(db, {"open_id": open_id, **display})
    # 已有可信邮箱的不必再打飞书;缺的一次批量补齐(内部按 50 分片、失败静默返回 0)
    missing = [u for u in resolved.values() if not u.email]
    if missing:
        user_service.sync_profiles_from_feishu(missing)
    if resolved:
        db.flush()  # 拿到自增 id;与调用方的写入同一事务提交
    return resolved


def resolve_subject_user(
    db: Session, *, subject_open_id: str, subject_profile: dict | None = None
) -> User:
    """resolve_subject_users 的一元特例(单个主体的授权入口用)。"""
    profile = {"open_id": subject_open_id, **(subject_profile or {})}
    return resolve_subject_users(db, [profile])[subject_open_id]


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
        subject = resolve_subject_user(
            db, subject_open_id=subject_open_id, subject_profile=subject_profile
        )
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


def grant_view(db: Session, *, template_id: int, user_id: int, granted_by: int | None) -> bool:
    """幂等补一条该任务的 view 业务授权行,返回是否新建。**不提交,跟随调用方事务。**

    与 grant() 的区别只有事务约定:代订阅要把「补 view 授权 + 建订阅行 + 写留痕」放进同一个
    事务(见 subscription_service.subscribe_for),而 grant() 末尾自带 commit。
    同 grant_edit(提交)/ discard_edit_grant(不提交)的分工 —— 一件事两种事务约定各给一个
    具名函数,别让调用方去猜。

    只开放 view 这一个动作:run / download 是「自己填参跑一次、把任意一期取走」才需要的,
    代订阅不该顺手给。订阅者取走推送给他那一期的资格另有出口,见 can_download_job。
    """
    return bool(
        _add_rows(
            db,
            subject_type=SUBJECT_USER,
            subject_id=str(user_id),
            resource_type=RESOURCE_TEMPLATE,
            resource_id=str(template_id),
            actions=[ACTION_VIEW],
            granted_by=granted_by,
        )
    )


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


def discard_edit_grant(db: Session, *, template_id: int, user_id: int) -> bool:
    """清掉某人在某任务上的 edit 授权行。**不提交,跟随调用方事务**(同
    revoke_edit_for_template 的约定)。「怎么删一条编辑权」只在这里写一次。
    """
    n = db.execute(
        sa_delete(Permission).where(_edit_where(user_id=user_id, template_id=template_id))
    ).rowcount or 0
    return bool(n)


def discard_edit_grants(db: Session, *, template_ids: list[int], user_id: int) -> set[int]:
    """批量版的 discard_edit_grant:一次查出命中的任务、一次删掉。**不提交。**

    返回命中的任务 id —— 调用方(批量转移作者)要按任务把「清掉了冗余授权吗」记进各自的
    审计行。逐条调单个版会发 N 条 DELETE 往返(上限 200 条),而谓词与批量删除本仓已有
    现成写法(_edit_where 支持 template_ids,同 revoke_edit_for_member)。
    """
    hit = set(_edit_rows_for(db, user_id=user_id, template_ids=template_ids))
    if hit:
        db.execute(sa_delete(Permission).where(_edit_where(user_id=user_id, template_ids=hit)))
    return hit


def revoke_edit(db: Session, *, template_id: int, user_id: int) -> bool:
    """「撤销编辑权」这个独立动作的入口:自成一个事务。

    作者转移走的是上面那支不提交的 —— 它要把这次删除与 author_id 的更新放进**同一个**
    事务,中途 commit 会让「授权已撤、作者没改」成为一种可能落库的中间态。
    两者的差别就只有这一个 commit,故删除语句不再各存一份。
    """
    n = discard_edit_grant(db, template_id=template_id, user_id=user_id)
    db.commit()
    return n


def _edit_rows_for(db: Session, *, user_id: int, template_ids: list[int]) -> list[int]:
    if not template_ids:
        return []
    rows = db.scalars(
        select(Permission.resource_id).where(
            _edit_where(user_id=user_id, template_ids=template_ids)
        )
    )
    return [tid for tid in map(parse_subject_id, rows) if tid is not None]


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
    uids = [uid for uid in map(parse_subject_id, rows) if uid is not None]
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
        tid, uid = parse_subject_id(rid), parse_subject_id(sid)
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

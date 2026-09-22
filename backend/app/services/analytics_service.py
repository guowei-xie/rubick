"""运营分析:可见范围(scope)、收窄谓词,以及四个板块的聚合口径。

**「谁能看到哪一片」在本项目只由 resolve_scope 表述一次**;所有聚合只从三个条件构造器
(job_conditions / template_conditions / scope_template_ids)拿收窄条件,不自己拼 where。
漏传一次收窄就是一次越权展示,所以入口必须少到能一眼数完。

与 permission_service 的分工:那边回答「**这个人**看得见什么」(含他所有团队 + 跨团队被
授权的任务),这边回答「**这一个团队**的资产表现如何」。两者口径不同,不能互相替代 ——
把别队授权给他的任务算进本团队的板子是错的。但有一条不变量必须永远成立:
analytics 的收窄结果 ⊆ 这个人的 visible set(见 tests/test_analytics_scope.py),
否则这里就成了一条绕过 permission_service 的提权侧门。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from app.core import timewindow
from app.core.config import settings
from app.core.exceptions import PermissionDeniedError
from app.models.audit import (
    ACTION_LOGIN, ACTION_META, ACTION_TASK_PUBLISH, VIA_API, AuditLog, DownloadEvent,
)
from app.models.datasource import DataSource
from app.models.permission import (
    ACTION_EDIT, ACTION_RUN, RESOURCE_TEMPLATE, SUBJECT_USER, Permission,
)
from app.models.query_job import (
    JOB_FAILED, JOB_QUEUED, JOB_RUNNING, JOB_SOURCES, JOB_SUCCESS,
    SOURCE_API, SOURCE_RUN, SOURCE_SUBSCRIBE, SOURCE_TEST, QueryJob,
)
from app.models.subscription import TaskSchedule, TaskSubscription
from app.models.team import Team, TeamMember
from app.models.template import (
    STATUS_ARCHIVED, STATUS_DRAFT, STATUS_PUBLISHED, SqlTemplate, TemplateVersion,
)
from app.models.user import (
    ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER, SYSTEM_SCHEDULER_OPEN_ID, User,
    is_platform_admin,
)
from app.services import (
    analytics_metrics as am, credential_service, permission_service, team_service,
    template_service,
)

LEVEL_PLATFORM = "platform"
LEVEL_TEAM = "team"


@dataclass(frozen=True)
class AnalyticsScope:
    """一次运营分析请求的可见范围。

    `level` 只有两种,刻意不做「多团队」那一档:团队管理员同时管两个队时看的是**某一个**队
    (默认第一个,可切换),而不是两队合并。合并出来的数字没有任何一个人负责,
    也答不了「我该去催谁」。
    """

    viewer_id: int
    level: str
    team_id: int | None
    team_name: str | None

    @property
    def is_platform(self) -> bool:
        return self.level == LEVEL_PLATFORM

def resolve_scope(db: Session, user: User, team_id: int | None = None) -> AnalyticsScope:
    """算出这次请求的可见范围。**权限判定只在这里发生一次。**

    「平台管理员传 team_id 就下钻、团队管理员锁死自己的团队」这件事完全由
    team_service.require_team_admin 完成 —— 平台管理员在它里面恒放行,团队管理员只对
    自己管的团队放行(它的豁免逻辑在 can_admin_team 里写过一次,这里不重写)。

    **刻意不做「静默收窄到他自己的团队」**:那会让界面上选了 A 队却显示 B 队的数,
    是比一个 403 糟得多的失败模式 —— 前者没人会发现,后者当场就知道。
    """
    if team_id is not None:
        team = team_service.require_team_admin(db, user, team_id)
        return AnalyticsScope(user.id, LEVEL_TEAM, team.id, team.name)
    if is_platform_admin(user):
        return AnalyticsScope(user.id, LEVEL_PLATFORM, None, None)
    admin_ids = sorted(team_service.admin_team_ids_of(db, user))
    if not admin_ids:
        raise PermissionDeniedError("运营分析仅对平台管理员与团队管理员开放")
    # 管多个队时取 id 最小的那个作为默认,保证同一个人每次进来看到的是同一个队
    team = team_service.get_team(db, admin_ids[0])
    return AnalyticsScope(user.id, LEVEL_TEAM, team.id, team.name)


def scope_options(db: Session, user: User) -> list[dict]:
    """这个人能选的范围列表,供前端渲染切换器。

    平台管理员拿到「全平台」+ 全部团队;团队管理员**只拿到自己管的团队**,没有「全平台」
    这一项 —— 选项本身就是权限的一部分,不该让界面先给出一个点了会 403 的选项。
    """
    if is_platform_admin(user):
        return [{"team_id": None, "name": "全平台"}] + [
            {"team_id": t.id, "name": t.name} for t in team_service.all_teams(db)
        ]
    ids = team_service.admin_team_ids_of(db, user)
    # 一次 IN 查名字,**不在循环里 db.get** —— 管三个队就是三次往返,而这是页面首屏
    return [
        {"team_id": tid, "name": name}
        for tid, name in db.execute(
            select(Team.id, Team.name).where(Team.id.in_(ids)).order_by(Team.id)
        ).all()
    ]


# ---------------------------------------------------------------- 收窄谓词
#
# 三个构造器是本模块收窄口径的**唯一**表述处。约定与 permission_service.visible_condition
# 一致:返回空列表 = 不加限制(仅平台视角),调用方直接 `select(...).where(*conds)`。


def template_conditions(scope: AnalyticsScope) -> list:
    """任务(以及经 template_id 挂靠的版本 / 订阅 / 计划)的收窄条件。"""
    if scope.is_platform:
        return []
    return [SqlTemplate.team_id == scope.team_id]


def job_conditions(scope: AnalyticsScope) -> list:
    """运行记录的收窄条件。

    **挂 sql_templates.team_id,不挂 query_jobs.run_as_team_id** —— 这是本模块最要紧的一条
    口径。后者是「用谁的账号跑的」,在四种情形下为空或过时:
      ① 身份解析之前就失败(参数校验、SQL 网关拒绝、模板缺失)—— 恒为 NULL;
      ② queued / running 的行还没解析身份 —— 也是 NULL;
      ③ 任务转移过团队后,历史行留着旧团队;
      ④ 试跑取的是编辑器里当前选中的团队,可能与已保存的不同。
    用它收窄会把 ①② 的**全部失败整个排除掉**,而那恰恰是运行健康板最该看见的一半 ——
    成功率会被系统性高估,且偏差永远朝着「看起来很健康」的方向,没人会去查。

    而 sql_templates.team_id 对所有 job 都非空(migrate._assert_every_template_has_team
    在部署期硬保证)、有索引,且回答的正是「本团队的资产被用得怎么样」。
    """
    return by_template(QueryJob.template_id, template_conditions(scope))


def by_template(column, tc: list) -> list:
    """任何**挂 template_id** 的表的收窄条件(版本 / 订阅 / 订阅事件 ……)。

    `tc` 传 template_conditions(scope) 的结果:空列表(平台视角)时返回空列表 = 不设限。
    每张表各写一遍 `in_(select(...))` 是这里最危险的复制粘贴 —— 漏掉一次 `if tc` 判断
    就是一次越权展示,而且看起来完全正常。收窄入口必须少到能一眼数完。
    """
    return [column.in_(select(SqlTemplate.id).where(*tc))] if tc else []


def by_job(column, jc: list) -> list:
    """挂 job_id 的表(下载事件)的收窄条件。同上,空 = 不设限。"""
    return [column.in_(select(QueryJob.id).where(*jc))] if jc else []


def scope_template_ids(db: Session, scope: AnalyticsScope) -> set[int] | None:
    """范围内的任务 id 集合。**None = 全平台(不设限)**,与 visible_condition 同一约定。

    只给需要在内存里做配对的地方用(如「授权了但从没跑过」——Permission 的 id 列是
    VARCHAR,与 templates 做 cast join 在 MySQL/SQLite 上都不安全)。
    能下推 SQL 的地方一律用 job_conditions / template_conditions,别 materialize。
    """
    if scope.is_platform:
        return None
    return set(
        db.scalars(select(SqlTemplate.id).where(SqlTemplate.team_id == scope.team_id))
    )


def system_user_ids(db: Session) -> frozenset[int]:
    """统计人头时要排除的系统账号(订阅定时运行的挂名人)。

    自己按 open_id 查,**不调 subscription_service.scheduler_user** —— 那个函数在用户不存在
    时抛 RubicError(刻意设计,它的调用方确实不该在缺账号时继续跑)。而看板不该因为某个
    环境没跑过迁移就整页 500,这里返回空集合即可。
    """
    return frozenset(
        db.scalars(
            select(User.id).where(User.feishu_open_id == SYSTEM_SCHEDULER_OPEN_ID)
        )
    )


# ---------------------------------------------------------------- 指标信封


def metric(
    value,
    *,
    windowed: bool,
    has_data: bool | None = None,
    last_event_at=None,
    prev_value=None,
) -> dict:
    """一个指标的完整形状。**不要在任何地方返回裸数字。**

    三件事必须由数据本身说清,靠文案补救来不及:

    · `value is None` ——「这个口径算不出来」(没有样本)。与 0 是两回事:0 是「跑了 100 次
      一次没失败」,None 是「一次都没跑过」。都渲染成 0,新部署的管理员会以为平台完美。
    · `has_data` ——「历史上有没有过」。窗口内为 0 但历史上有过,该说「近 30 天没有,
      最近一次在 8 月 3 日」;从来没有过,该说「还没有数据」并指路。
    · `windowed` ——「吃不吃时间范围」。任务总数、缺账号数这类是**此刻**的快照,切时间范围
      纹丝不动。不在数据结构上分开,用户第一次切范围就会当成 bug。
    """
    if has_data is None:
        has_data = value is not None and value != 0
    return {
        "value": value,
        "has_data": bool(has_data),
        "windowed": windowed,
        "last_event_at": last_event_at,
        "prev_value": prev_value,
    }


def ratio(numerator: int | None, denominator: int | None) -> float | None:
    """占比。分母为 0 或缺失时返回 None —— **不是 0**。

    「0 次取数里 0 次失败」的失败率不是 0%,是「没得算」。返回 0 会在页面上显示成
    一个漂亮的 0%,而那是凭空捏造的。
    """
    if not denominator:
        return None
    return round((numerator or 0) / denominator, 4)


def envelope(scope: AnalyticsScope, window, body: dict) -> dict:
    """板块响应的统一外壳:范围与时间窗永远跟着数字一起走。

    前端的口径行、板块标题后缀、以及「此刻/本区间」的区分全靠它 —— 数字与它的口径
    分两个接口下发,迟早会配错。
    """
    return {
        # 只下发**事实**(这是哪一档、哪个队),不下发面向用户的那句中文标签:
        # 板块标题在骨架屏阶段就要显示范围,那时数据还没到 —— 标签只能由前端自己拼,
        # 后端再给一份就成了第二种拼法,而且是永远对不上的那一种
        "scope": {
            "level": scope.level,
            "team_id": scope.team_id,
            "team_name": scope.team_name,
        },
        "window": {"start": window.start, "end": window.end, "days": window.days},
        **body,
    }


def in_window(column, window) -> list:
    """时间窗谓词:**半开区间** [start, end)。

    相邻两个窗口因此首尾相接、不重不漏,环比与留存才算得对(见 core/timewindow)。
    """
    return [column >= window.start, column < window.end]


# ---------------------------------------------------------------- 板块① 采纳与活跃


def adoption(db: Session, scope: AnalyticsScope, window) -> dict:
    """「平台有没有人用、用得深不深」。

    全篇最要紧的一条口径:**活跃取数人只算 source='run' 的自然人,不含试跑**。
    试跑是「做任务的人在验证自己的产品」,把它计入采纳率等于允许开发者自己刷高这块板的
    核心数字 —— 而这块板存在的意义恰恰是回答「业务方真的在自助用吗」。
    开发侧活跃(active_authors)并列单列,谁都不吃亏。
    """

    jc = job_conditions(scope)
    sys_ids = system_user_ids(db)
    # 排除定时运行的挂名人。不排的话「活跃取数人」会凭空多一个、而且永远活跃
    not_system = [QueryJob.user_id.notin_(sys_ids)] if sys_ids else []

    # ① 窗口内按 source 的全部计数,**一次查完**:次数、去重人数、去重任务数、成功数。
    #    后三者各自单查一次也能算,但那是对同一批行的三次重复扫描
    rows = db.execute(
        select(
            QueryJob.source,
            func.count(QueryJob.id),
            func.count(distinct(QueryJob.user_id)),
            func.count(distinct(QueryJob.template_id)),
            func.sum(case((QueryJob.status == JOB_SUCCESS, 1), else_=0)),
        )
        .where(*jc, *in_window(QueryJob.created_at, window))
        .group_by(QueryJob.source)
    ).all()
    counts = {src: (n, u, t, int(ok or 0)) for src, n, u, t, ok in rows}

    # ② 全期:同样按 source,用来回答 has_data 与「最近一次在什么时候」
    ever = {
        src: (n, last)
        for src, n, last in db.execute(
            select(QueryJob.source, func.count(QueryJob.id), func.max(QueryJob.created_at))
            .where(*jc)
            .group_by(QueryJob.source)
        ).all()
    }

    def _job_metric(src: str, *, users: bool = False):
        """某个 source 的窗口内次数(users=True 则是去重人数)。

        has_data 一律看**全期**有没有过 —— 窗口内为 0 要能说清是「这段时间没人跑」
        还是「从来没人跑过」,这两句话指向完全不同的下一步。
        """
        n = counts.get(src, (0, 0, 0, 0))[1 if users else 0]
        ever_n, ever_last = ever.get(src, (0, None))
        return metric(n, windowed=True, has_data=bool(ever_n), last_event_at=ever_last)

    # ③ 上一个等长窗口的活跃人集合 —— 留存与环比都用它
    def _active_user_ids(w) -> set[int]:
        return set(
            db.scalars(
                select(distinct(QueryJob.user_id)).where(
                    *jc, *not_system,
                    QueryJob.source == SOURCE_RUN,
                    *in_window(QueryJob.created_at, w),
                )
            )
        )

    cur_users = _active_user_ids(window)
    prev_window = timewindow.previous(window)
    prev_users = _active_user_ids(prev_window)

    # ④ 业务方(role=user)发起的正式取数次数 —— 自助率的分子
    biz_runs = db.scalar(
        select(func.count(QueryJob.id))
        .select_from(QueryJob)
        .join(User, User.id == QueryJob.user_id)
        .where(
            *jc, QueryJob.source == SOURCE_RUN, User.role == ROLE_USER,
            *in_window(QueryJob.created_at, window),
        )
    ) or 0

    # 复用倍数的分母(窗口内被正式跑过的去重任务数)与下载转化的分母(成功运行数),
    # 都从 ① 里取,不再单查
    distinct_templates = counts.get(SOURCE_RUN, (0, 0, 0, 0))[2]
    success_jobs = sum(c[3] for c in counts.values())

    # ⑤ 下载:那张从前只写不读的表第一次被读。按 job 收窄到本范围
    dl_scope = by_job(DownloadEvent.job_id, jc)
    dl_n = db.scalar(
        select(func.count(DownloadEvent.id))
        .where(*dl_scope, *in_window(DownloadEvent.created_at, window))
    ) or 0
    dl_ever = db.scalar(
        select(func.count(DownloadEvent.id)).where(*dl_scope)
    ) or 0

    # ⑥ 日趋势:按天 × source。func.date() 的返回形状两个库不同,统一过 am.to_date
    series_rows = db.execute(
        select(
            func.date(QueryJob.created_at),
            QueryJob.source,
            func.count(QueryJob.id),
        )
        .where(*jc, *in_window(QueryJob.created_at, window))
        .group_by(func.date(QueryJob.created_at), QueryJob.source)
    ).all()
    by_day: dict = {}
    for d, src, n in series_rows:
        key = am.to_date(d).isoformat()
        # 键集由 JOB_SOURCES 生成,不写字面量 —— 新增一种来源时,前端图例不会因为
        # 「有数据的那天有这个键、没数据的那天没有」而时有时无
        point = by_day.setdefault(key, {"date": key, **{s: 0 for s in JOB_SOURCES}})
        point[src] = point.get(src, 0) + n

    run_n = counts.get(SOURCE_RUN, (0, 0, 0, 0))[0]
    sub_n = counts.get(SOURCE_SUBSCRIBE, (0, 0, 0, 0))[0]

    body = {
        "run_jobs": _job_metric(SOURCE_RUN),
        "test_jobs": _job_metric(SOURCE_TEST),
        "scheduled_jobs": _job_metric(SOURCE_SUBSCRIBE),
        "active_users": metric(
            len(cur_users), windowed=True,
            has_data=bool(ever.get(SOURCE_RUN, (0, None))[0]),
            prev_value=len(prev_users),
        ),
        "active_authors": _job_metric(SOURCE_TEST, users=True),
        "download_events": metric(dl_n, windowed=True, has_data=bool(dl_ever)),
        # 跑了不下载 = 结果没用上。最容易被忽略的一个信号
        "download_per_success": metric(ratio(dl_n, success_jobs), windowed=True,
                                       has_data=bool(success_jobs)),
        # ---- 复用与自动化(刻意不做「节省 X 人天」:那需要一个拍脑袋的工时假设,
        #      而一旦写进看板就会变成 KPI,反过来污染数据)
        "self_service_ratio": metric(ratio(biz_runs, run_n), windowed=True,
                                     has_data=bool(run_n)),
        "reuse_multiple": metric(
            round(run_n / distinct_templates, 2) if distinct_templates else None,
            windowed=True, has_data=bool(distinct_templates),
        ),
        "automation_ratio": metric(ratio(sub_n, run_n + sub_n), windowed=True,
                                   has_data=bool(run_n + sub_n)),
        "daily_series": [by_day[k] for k in sorted(by_day)],
    }

    if scope.is_platform:
        # 平台视角限定:跨团队人头。团队管理员看不到这些 —— 键直接不存在,而不是给个 0
        first_logins = (
            select(AuditLog.user_id, func.min(AuditLog.created_at).label("first_at"))
            .where(AuditLog.action == ACTION_LOGIN)
            .group_by(AuditLog.user_id)
            .subquery()
        )
        new_users = db.scalar(
            select(func.count()).select_from(first_logins).where(
                *in_window(first_logins.c.first_at, window)
            )
        ) or 0
        body["new_users"] = metric(new_users, windowed=True)
        body["retention_rate"] = metric(
            ratio(len(cur_users & prev_users), len(prev_users)),
            windowed=True, has_data=bool(prev_users),
        )
    else:
        # 团队视角的替代品:窗口内**首次**跑过本团队任务的人。对团队有意义,且不泄漏全平台人头
        first_run = (
            select(QueryJob.user_id, func.min(QueryJob.created_at).label("first_at"))
            .where(*jc, *not_system, QueryJob.source == SOURCE_RUN)
            .group_by(QueryJob.user_id)
            .subquery()
        )
        body["new_task_users"] = metric(
            db.scalar(
                select(func.count()).select_from(first_run).where(
                    *in_window(first_run.c.first_at, window)
                )
            ) or 0,
            windowed=True,
        )

    return envelope(scope, window, body)


# ---------------------------------------------------------------- 板块② 运行健康

# 拉明细的两处上限。超了就截断并在响应里说明(truncated),而不是静默算一个偏的数。
FAILURE_SAMPLE_CAP = 5_000
# other 桶回传多少条样例。这是失败归因规则表能演进的唯一机制 —— 没有它,other 会永远是
# 最大的桶,且没人知道该往里加什么规则。
UNBUCKETED_SAMPLE_LIMIT = 20
UNBUCKETED_SAMPLE_CHARS = 200


def health(db: Session, scope: AnalyticsScope, window) -> dict:
    """「跑得顺不顺」。

    成功率**必须按 source 拆三份**:试跑失败是正常的研发过程(写 SQL 就是试错),
    定时失败才是事故。合并成一个数会让平台看起来一团糟,然后所有人学会忽略这个数字。
    """

    jc = job_conditions(scope)
    win = in_window(QueryJob.created_at, window)
    TERMINAL = (JOB_SUCCESS, JOB_FAILED)

    # ① 窗口内 source × status 的次数,一次查完 —— 成功率的分子分母、以及「成功但 0 行」
    #    都从这里来。每个数各查一次是对同一批行的重复扫描
    grid: dict[tuple[str, str], int] = {}
    zero_rows = 0
    for src, st, n, zero in db.execute(
        select(
            QueryJob.source, QueryJob.status, func.count(QueryJob.id),
            func.sum(case(
                ((QueryJob.status == JOB_SUCCESS) & (QueryJob.row_count == 0), 1),
                else_=0,
            )),
        )
        .where(*jc, *win)
        .group_by(QueryJob.source, QueryJob.status)
    ).all():
        grid[(src, st)] = n
        zero_rows += int(zero or 0)

    def _rate(src: str) -> dict:
        ok = grid.get((src, JOB_SUCCESS), 0)
        bad = grid.get((src, JOB_FAILED), 0)
        return {
            "total": ok + bad,
            "success": ok,
            "failed": bad,
            # 分母**只含终态**:queued/running 还没有结论,算进去会让刚入队的一批
            # 把成功率生生拉低,而那不是质量问题
            "success_rate": ratio(ok, ok + bad),
        }

    by_source = {src: _rate(src) for src in (SOURCE_RUN, SOURCE_TEST, SOURCE_SUBSCRIBE)}

    # ② 全期有没有过 —— has_data
    ever_terminal = db.scalar(
        select(func.count(QueryJob.id)).where(*jc, QueryJob.status.in_(TERMINAL))
    ) or 0

    # ③ 日趋势:成功/失败
    daily: dict = {}
    for d, st, n in db.execute(
        select(func.date(QueryJob.created_at), QueryJob.status, func.count(QueryJob.id))
        .where(*jc, *win, QueryJob.status.in_(TERMINAL))
        .group_by(func.date(QueryJob.created_at), QueryJob.status)
    ).all():
        key = am.to_date(d).isoformat()
        point = daily.setdefault(key, {"date": key, "success": 0, "failed": 0})
        point["success" if st == JOB_SUCCESS else "failed"] += n

    # ④ 耗时:只取成功行(失败分支不写 duration_ms)。**按引擎分开** ——
    #    Hive 默认超时 3600s、MySQL 120s,混在一起的分位数没有可比性
    # engine 只有几种取值,却会在每一行上重复一遍 —— 拉一张 id→engine 的小表在内存里配,
    # 比把 data_sources 联进几万行明细便宜
    engines = dict(db.execute(select(DataSource.id, DataSource.engine)).all())
    dur_rows = db.execute(
        select(QueryJob.datasource_id, QueryJob.duration_ms)
        .where(*jc, *win, QueryJob.status == JOB_SUCCESS, QueryJob.duration_ms.isnot(None))
        .limit(am.PERCENTILE_SAMPLE_CAP)
    ).all()
    by_engine: dict[str, list[int]] = {}
    for did, ms in dur_rows:
        by_engine.setdefault(engines.get(did) or "unknown", []).append(ms)
    duration = {
        engine: {
            "samples": len(vals),
            **{f"p{p}_ms": v for p, v in am.percentiles(vals).items()},
            "buckets": [
                {"label": lbl, "count": n} for lbl, n in am.bucketize(vals)
            ],
        }
        for engine, vals in by_engine.items()
    }

    # ⑤ 排队:started_at - created_at。**排除试跑** —— 它同步执行不入队,
    #    一堆 0 会把分位数拉平(见 QueryJob.started_at 的注释)
    #    只拉**盖过章**的行:历史行 started_at 为空,拉回来也只会被下面丢掉,
    #    白占 PERCENTILE_SAMPLE_CAP 的名额(早期历史占多数时会把真样本挤掉)
    queue_rows = db.execute(
        select(QueryJob.created_at, QueryJob.started_at)
        .where(
            *jc, *win, QueryJob.source != SOURCE_TEST,
            QueryJob.status.in_(TERMINAL), QueryJob.started_at.isnot(None),
        )
        .limit(am.PERCENTILE_SAMPLE_CAP)
    ).all()
    waits = [
        int((s - c).total_seconds() * 1000)
        for c, s in queue_rows
        if c is not None and s >= c
    ]
    # 有排队记录的样本占比。历史行没有 started_at,**不拿 0 充数** ——
    # 那会把全部历史算成「零排队」,指标一上线就在说谎,而且看起来很健康
    queue_stats_since = db.scalar(
        select(func.min(QueryJob.created_at)).where(*jc, QueryJob.started_at.isnot(None))
    )
    # 分母是窗口内会排队的终态行(试跑同步执行、不入队,不进分母),它已经在 ① 里数过了
    queue_eligible = sum(
        v["total"] for k, v in by_source.items() if k != SOURCE_TEST
    )
    queue = {
        "samples": len(waits),
        "coverage": ratio(len(waits), queue_eligible),
        "stats_since": queue_stats_since,
        **{f"p{p}_ms": v for p, v in am.percentiles(waits).items()},
        # 等过一分钟以上的次数 —— 直指并发不足,比分位数更好向人解释
        "over_60s": sum(1 for w in waits if w >= 60_000),
    }

    # ⑥ 失败归因:拉 error 文本在内存里分桶(规则要频繁迭代,且 LIKE 的大小写敏感性
    #    在 MySQL 与 SQLite 上不同 —— 放 SQL 里两边会算出不同的分类)。
    #    读 job.error 而不是审计 detail:前者已过 credential_service.redact 脱敏,
    #    后者是原文,团队视角下会漏库账号名
    err_rows = db.scalars(
        select(QueryJob.error)
        .where(*jc, *win, QueryJob.status == JOB_FAILED)
        .order_by(QueryJob.id.desc())
        .limit(FAILURE_SAMPLE_CAP + 1)
    ).all()
    truncated = len(err_rows) > FAILURE_SAMPLE_CAP
    err_rows = err_rows[:FAILURE_SAMPLE_CAP]

    bucket_counts: dict[str, int] = {}
    unbucketed: list[str] = []
    for text in err_rows:
        code = am.classify_error(text)
        bucket_counts[code] = bucket_counts.get(code, 0) + 1
        if code == am.BUCKET_OTHER and text:
            snippet = text[:UNBUCKETED_SAMPLE_CHARS]
            if snippet not in unbucketed and len(unbucketed) < UNBUCKETED_SAMPLE_LIMIT:
                unbucketed.append(snippet)

    failure_buckets = sorted(
        (
            {"code": c, "label": am.FAILURE_LABELS.get(c, c), "count": n}
            for c, n in bucket_counts.items()
        ),
        key=lambda b: -b["count"],
    )

    # ⑦ 按数据源:常能一眼看出「是某个库不行」而不是「任务写得烂」
    ds_rows = db.execute(
        select(
            DataSource.id, DataSource.name, DataSource.engine,
            QueryJob.status, func.count(QueryJob.id),
        )
        .select_from(QueryJob)
        .join(DataSource, DataSource.id == QueryJob.datasource_id)
        .where(*jc, *win, QueryJob.status.in_(TERMINAL))
        .group_by(DataSource.id, DataSource.name, DataSource.engine, QueryJob.status)
    ).all()
    ds_acc: dict[int, dict] = {}
    for did, name, engine, st, n in ds_rows:
        row = ds_acc.setdefault(
            did,
            {"datasource_id": did, "name": name, "engine": engine, "success": 0, "failed": 0},
        )
        row["success" if st == JOB_SUCCESS else "failed"] += n
    for row in ds_acc.values():
        row["total"] = row["success"] + row["failed"]
        row["fail_rate"] = ratio(row["failed"], row["total"])
    by_datasource = sorted(ds_acc.values(), key=lambda r: -r["total"])

    # ⑧ 此刻的队列 —— **不吃时间窗**。切时间范围它不该变。
    #    「排队中有几个」与「最早那个等了多久」是同一批行上的两个聚合,一次 GROUP BY 拿齐
    in_flight: dict[str, int] = {}
    oldest_queued = None
    for st, n, oldest in db.execute(
        select(QueryJob.status, func.count(QueryJob.id), func.min(QueryJob.created_at))
        .where(*jc, QueryJob.status.in_((JOB_QUEUED, JOB_RUNNING)))
        .group_by(QueryJob.status)
    ).all():
        in_flight[st] = n
        if st == JOB_QUEUED:
            oldest_queued = oldest

    # 刻意**不返回**一个合并的总成功率:它把「试跑失败」和「定时失败」加在一起,
    # 正是本板块开头那条口径要避免的读法
    run = by_source[SOURCE_RUN]
    return envelope(scope, window, {
        "by_source": by_source,
        # 顶部那几张卡各自是一个完整的指标信封。**不要让前端拿 by_source / queue 里的裸数字
        # 自己拼 {value, has_data, windowed}** —— has_data 的口径是「**全期**有没有过」,
        # 前端手拼时只看得见本区间,于是「从来没跑过」会被渲染成一个绿色的 0
        "run_success_rate": metric(
            run["success_rate"], windowed=True, has_data=bool(ever_terminal)),
        "run_failed": metric(run["failed"], windowed=True, has_data=bool(ever_terminal)),
        "queue_p50_ms": metric(
            queue["p50_ms"], windowed=True, has_data=bool(queue["stats_since"])),
        "queue_over_60s": metric(
            queue["over_60s"] if waits else None,
            windowed=True, has_data=bool(queue["stats_since"]),
        ),
        "queued_now": metric(in_flight.get(JOB_QUEUED, 0), windowed=False, has_data=True),
        "daily_series": [daily[k] for k in sorted(daily)],
        "duration_by_engine": duration,
        "queue": queue,
        "zero_row_jobs": metric(zero_rows, windowed=True, has_data=bool(ever_terminal)),
        "failure_buckets": failure_buckets,
        "unbucketed_samples": unbucketed,
        "failure_truncated": truncated,
        "in_flight": {
            "queued": in_flight.get(JOB_QUEUED, 0),
            "running": in_flight.get(JOB_RUNNING, 0),
            "oldest_queued_at": oldest_queued,
        },
        "by_datasource": by_datasource,
    })


# ---------------------------------------------------------------- meta

# 时间范围预设(天)。前端的快捷档与这里同源,改这里前端跟着变。
PRESET_DAYS = [7, 30, 90]

# 每条指标的口径说明。**单一真源** —— 前端不自己维护一份中文解释,否则口径改了文案不改,
# 页面上那句「什么算活跃」会变成一句错话,而且没人会发现。
# 四个板块的 note 都必须从这里取(前端 notes[key]?.note);在 JSX 里直接写中文,
# 等于把这条约定破掉一半 —— 而破掉的那一半不会报错,只会慢慢说假话。
METRIC_NOTES: list[dict] = [
    {"key": "run_jobs", "label": "正式取数", "windowed": True,
     "note": "业务方填参跑的次数。不含作者试跑,也不含定时运行。"},
    {"key": "test_jobs", "label": "作者试跑", "windowed": True,
     "note": "开发者在编辑器里测的次数。失败率天然偏高，写 SQL 本就是试错。"},
    {"key": "scheduled_jobs", "label": "定时运行", "windowed": True,
     "note": "平台按订阅计划自动跑的次数，挂在系统账号名下。"},
    {"key": "active_users", "label": "活跃取数人", "windowed": True,
     "note": "发起过正式取数的不同的人。**不含试跑** —— 否则开发者调试很勤会被读成业务很活跃。"},
    {"key": "active_authors", "label": "开发侧活跃", "windowed": True,
     "note": "试跑过任务的不同的人，反映建任务这一侧的活跃度。"},
    {"key": "download_per_success", "label": "下载转化", "windowed": True,
     "note": "下载次数 ÷ 成功运行次数。偏低说明跑出来的结果没被真正用上。"},
    {"key": "self_service_ratio", "label": "业务自助率", "windowed": True,
     "note": "普通用户发起的正式取数占比。上升即意味着取数真的从数据同学手里转移出去了。"},
    {"key": "reuse_multiple", "label": "复用倍数", "windowed": True,
     "note": "正式取数次数 ÷ 被跑过的去重任务数。一次开发被复用了几次。"},
    {"key": "automation_ratio", "label": "免人工率", "windowed": True,
     "note": "定时运行 ÷（正式取数 + 定时运行）。定时推送全程不需要人在场。"},
    {"key": "success_rate", "label": "成功率", "windowed": True,
     "note": "成功 ÷（成功 + 失败）。排队中与运行中不进分母 —— 它们还没有结论。"},
    {"key": "duration", "label": "执行耗时", "windowed": True,
     "note": "只算真正执行的那一段，**不含排队**。按引擎分开看：Hive 与 MySQL 的超时上限差一个量级。"},
    {"key": "queue", "label": "排队等待", "windowed": True,
     "note": "入队到开始执行之间的时长。试跑不入队，不计入。早于该功能上线的历史运行没有记录。"},
    {"key": "zero_row_jobs", "label": "成功但 0 行", "windowed": True,
     "note": "跑通了却没有数据，多半是参数填错或那天确实没数。"},
    {"key": "in_flight", "label": "此刻队列", "windowed": False,
     "note": "当前排队中与运行中的数量。反映此刻的状态，不随时间范围变化。"},
    {"key": "idle", "label": "闲置任务", "windowed": False,
     "note": "已上线却长期没人跑。口径与任务列表页的「闲置」筛选片完全一致。"},
    {"key": "never_run", "label": "从没跑过", "windowed": False,
     "note": "已上线、但一次都没跑过——「做了没人用」最硬的信号。"},
    {"key": "schedules_enabled", "label": "定时计划", "windowed": False,
     "note": "计划开着且任务已上线。下线即暂停，所以下线的不计入。"},
    {"key": "at_risk_subscriptions", "label": "濒临清退的订阅", "windowed": False,
     "note": "连续多期没看结果的订阅。到阈值平台会自动清退并通知本人。"},
    {"key": "dormant_grants", "label": "授权后从没跑过", "windowed": False,
     "note": "授了权却一次都没用过的（人 × 任务）。这是**全期**口径，不随上方时间范围变化"
             "——授权是存量事实，套时间窗会把三个月前用过的人误报成僵尸。"},
    {"key": "stale_edit_grants", "label": "失效的编辑权", "windowed": False,
     "note": "授过编辑权、人却已不在该任务所属团队里。权限实际已经失效，但这行还留着，该撤掉。"},
    {"key": "credential_not_ready", "label": "缺账号的已上线任务", "windowed": False,
     "note": "这些任务现在就跑不动——业务同学点运行会直接失败。"},
    {"key": "download_concentration", "label": "下载集中度", "windowed": True,
     "note": "下载最多的那个人占了多少。偏高通常意味着一个人在替全组取数——不是问题，但值得知道。"},
    {"key": "teams_without_admin", "label": "没有团队管理员", "windowed": False,
     "note": "没人能给它配取数账号、加成员、授编辑权——治理黑洞。"},
]


def meta(db: Session, user: User) -> dict:
    """页面初始化需要的一切:能选的范围、预设、口径说明、以及「平台有没有开张」。"""

    options = scope_options(db, user)
    scope = resolve_scope(db, user)
    jc = job_conditions(scope)
    tc = template_conditions(scope)

    templates = db.scalar(select(func.count(SqlTemplate.id)).where(*tc)) or 0
    runs = db.scalar(select(func.count(QueryJob.id)).where(*jc)) or 0
    teams = db.scalar(select(func.count(Team.id))) or 0

    return {
        "scope_options": options,
        "default_team_id": scope.team_id,
        "presets": PRESET_DAYS,
        "metric_notes": METRIC_NOTES,
        # 「开张了没有」= 有任务**且**有人跑过。只有任务没人跑过,四屏「—」比一句话难看得多
        "bootstrapped": bool(templates and runs),
        "counts": {"teams": teams, "templates": templates, "runs": runs},
    }


# ---------------------------------------------------------------- 板块③ 任务资产治理

TOP_N = 10


def assets(db: Session, scope: AnalyticsScope, window) -> dict:
    """「资产健康不健康」—— 任务被复用了还是在腐烂。

    闲置判定**完全复用 template_service.idle_days / is_idle**(含它「只判已上线」「从未跑过
    从创建时间起算」「阈值 0 即关闭」的全部规则)。运营板与任务列表页对同一批任务给出不同的
    闲置数,是这类看板最快失信的方式 —— 一致性优先于「这里再严格一点」。
    """

    tc = template_conditions(scope)
    jc = job_conditions(scope)
    win = in_window(QueryJob.created_at, window)

    # ① 状态分布(存量)
    by_status = {
        st: n
        for st, n in db.execute(
            select(SqlTemplate.status, func.count(SqlTemplate.id))
            .where(*tc).group_by(SqlTemplate.status)
        ).all()
    }
    # ② 每张任务的最后一次运行(含试跑与定时,与任务列表页同一把尺),一次批量聚合
    last_runs = {
        tid: ts
        for tid, ts in db.execute(
            select(QueryJob.template_id, func.max(QueryJob.created_at))
            .where(*jc).group_by(QueryJob.template_id)
        ).all()
    }
    now = datetime.now()  # 整批共用同一把尺,理由见 template_service.idle_days
    rows = list(db.scalars(select(SqlTemplate).where(*tc, SqlTemplate.status == STATUS_PUBLISHED)))
    published_ids = {t.id for t in rows}
    idle_rows = []
    never_run = 0
    for t in rows:
        last = last_runs.get(t.id)
        if last is None:
            never_run += 1
        days = template_service.idle_days(t, last, now)
        if template_service.is_idle(days):
            idle_rows.append({
                "template_id": t.id, "name": t.name, "team_name": t.team_name,
                "author_name": t.author_name, "idle_days": days, "last_run_at": last,
            })
    idle_rows.sort(key=lambda r: -r["idle_days"])

    # ③ 窗口内的运行排行:次数 / 去重使用人数 / 成功率,一次 GROUP BY 拿齐
    run_stats = db.execute(
        select(
            QueryJob.template_id,
            func.count(QueryJob.id),
            func.count(distinct(QueryJob.user_id)),
            func.sum(case((QueryJob.status == JOB_SUCCESS, 1), else_=0)),
            func.sum(case((QueryJob.status == JOB_FAILED, 1), else_=0)),
        )
        .where(*jc, *win, QueryJob.source == SOURCE_RUN)
        .group_by(QueryJob.template_id)
    ).all()
    total_runs = sum(r[1] for r in run_stats)
    ranked = sorted(run_stats, key=lambda r: -r[1])[:TOP_N]
    # 名字批量补:先 GROUP BY 拿 id,再一次 IN 查询 —— 绝不在循环里 db.get
    name_map = {
        t.id: t
        for t in db.scalars(
            select(SqlTemplate).where(SqlTemplate.id.in_([r[0] for r in ranked]))
        )
    } if ranked else {}
    top_templates = []
    for tid, n, users, ok, bad in ranked:
        t = name_map.get(tid)
        top_templates.append({
            "template_id": tid,
            "name": t.name if t else None,
            "team_name": t.team_name if t else None,
            "author_name": t.author_name if t else None,
            "run_count": n,
            # 只有作者自己在跑的任务 = 没被业务用起来。这比「跑了多少次」更能说明问题
            "distinct_users": users,
            "success_rate": ratio(int(ok or 0), int(ok or 0) + int(bad or 0)),
        })

    # 窗口内跑过 ≤1 次的已上线任务 = 长尾
    ran_counts = {r[0]: r[1] for r in run_stats}
    tail = sum(1 for tid in published_ids if ran_counts.get(tid, 0) <= 1)

    # ④ 窗口内的变动
    new_templates = db.scalar(
        select(func.count(SqlTemplate.id)).where(
            *tc, *in_window(SqlTemplate.created_at, window)
        )
    ) or 0
    ver_scope = by_template(TemplateVersion.template_id, tc)
    new_versions = db.scalar(
        select(func.count(TemplateVersion.id)).where(
            *ver_scope, *in_window(TemplateVersion.created_at, window)
        )
    ) or 0
    # 上线**次数**只能从审计里数:published_version_id 只反映当前态,答不了「这个窗口上线了几次」。
    # 团队视角不读审计(见 governance 的说明),所以这一项仅平台视角提供
    publishes = None
    if scope.is_platform:
        publishes = db.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.action == ACTION_TASK_PUBLISH,
                *in_window(AuditLog.created_at, window),
            )
        ) or 0

    # ⑤ 订阅健康
    sub_scope = by_template(TaskSubscription.template_id, tc)
    subscribers = db.scalar(
        select(func.count(TaskSubscription.id)).where(*sub_scope)
    ) or 0
    at_risk = db.scalar(
        select(func.count(TaskSubscription.id)).where(
            *sub_scope,
            TaskSubscription.miss_streak >= max(1, settings.SUBSCRIPTION_MISS_LIMIT // 2),
        )
    ) or 0
    # 计划开着**且任务已上线**才算真的在跑 —— 下线即暂停(TaskSchedule 刻意没有 paused 列),
    # 只看 enabled 会虚报
    schedules_on = db.scalar(
        select(func.count(TaskSchedule.id))
        .select_from(TaskSchedule)
        .join(SqlTemplate, SqlTemplate.id == TaskSchedule.template_id)
        .where(*tc, TaskSchedule.enabled.is_(True), SqlTemplate.status == STATUS_PUBLISHED)
    ) or 0

    body = {
        "as_of": {
            "total": metric(sum(by_status.values()), windowed=False),
            "published": metric(by_status.get(STATUS_PUBLISHED, 0), windowed=False),
            "draft": metric(by_status.get(STATUS_DRAFT, 0), windowed=False),
            "archived": metric(by_status.get(STATUS_ARCHIVED, 0), windowed=False),
            "idle": metric(len(idle_rows), windowed=False,
                           has_data=bool(published_ids)),
            # 阈值一并下发:前端的「闲置」卡与下钻链接在阈值为 0(功能关闭)时必须一起隐藏,
            # 否则会跳到一张空列表
            "idle_threshold_days": settings.TASK_IDLE_DAYS,
            "never_run": metric(never_run, windowed=False, has_data=bool(published_ids)),
            "schedules_enabled": metric(schedules_on, windowed=False),
            "subscribers": metric(subscribers, windowed=False),
            "at_risk_subscriptions": metric(at_risk, windowed=False,
                                            has_data=bool(subscribers)),
        },
        "window_changes": {
            "new_templates": metric(new_templates, windowed=True),
            "new_versions": metric(new_versions, windowed=True),
        },
        "top_templates": top_templates,
        "top10_share": metric(
            ratio(sum(r["run_count"] for r in top_templates), total_runs),
            windowed=True, has_data=bool(total_runs),
        ),
        "tail_count": metric(tail, windowed=True, has_data=bool(published_ids)),
        "idle_list": idle_rows[:TOP_N],
    }
    if publishes is not None:
        body["window_changes"]["publishes"] = metric(publishes, windowed=True)
    return envelope(scope, window, body)


# ---------------------------------------------------------------- 板块④ 权限与配置治理


def governance(db: Session, scope: AnalyticsScope, window) -> dict:
    """「有没有治理漏洞」—— 权限膨胀、配置缺口、以及谁在大量下载。

    **团队视角不提供任何 audit_logs 派生指标。** 审计接口本身是 admin-only,运营板不该开一条
    绕过它的侧门;而且 audit_logs.resource_id 是 VARCHAR、难安全收窄,detail 里还可能带别队
    信息(如 task_team_transfer 的 from/to team),逐条脱敏的成本远大于收益。团队管理员改用
    Permission 与 TaskSubscription 这类**挂得住 template_id 的存量表**(天然可收窄),
    够用且零泄漏风险 —— 这是个干净的边界,不是临时妥协。
    """

    tc = template_conditions(scope)
    jc = job_conditions(scope)
    # 任务 → 所属团队。③ 的僵尸编辑权要用它,范围内的任务 id 集合也是它的 keys ——
    # 同一批行查两遍没有意义
    tmpl_team = dict(db.execute(select(SqlTemplate.id, SqlTemplate.team_id).where(*tc)).all())
    tids = None if scope.is_platform else set(tmpl_team)  # None = 全平台

    # ① 授权存量。**不做 SQL join**:Permission 的 subject_id / resource_id 是 VARCHAR(64),
    #    与 templates 做 cast join 在 MySQL/SQLite 上都不安全(permission_service
    #    .manageable_template_ids 的注释已经定论过)。一次列级查询 + 内存配对。
    #    只取 run 与 edit:view 是「让他在列表里看得见」,没跑不算浪费;download 依附于 run
    grant_rows = db.execute(
        select(
            Permission.action, Permission.subject_id, Permission.resource_id,
            Permission.granted_by, Permission.created_at,
        ).where(
            Permission.subject_type == SUBJECT_USER,
            Permission.resource_type == RESOURCE_TEMPLATE,
            Permission.action.in_((ACTION_RUN, ACTION_EDIT)),
        )
    ).all()

    # **「哪些授权行算数」只判一次**,run 与 edit 在同一趟里分开装:
    # 解析规则复用 permission_service,收窄判断(tids)也只写在这一个循环里
    run_grants: dict[tuple[int, int], tuple] = {}
    edit_pairs: set[tuple[int, int]] = set()
    for act, sid, rid, by, at in grant_rows:
        uid = permission_service.parse_subject_id(sid)
        tid = permission_service.parse_subject_id(rid)
        if uid is None or tid is None:
            continue
        if tids is not None and tid not in tids:
            continue
        if act == ACTION_RUN:
            run_grants[(uid, tid)] = (by, at)
        else:
            edit_pairs.add((uid, tid))

    run_pairs = set(run_grants)

    # ② 真正用过的 (人, 任务) 对。**全期,不吃时间窗** —— 授权是存量事实,
    #    套时间窗会把三个月前用过的人误报成僵尸
    used_pairs = set(
        db.execute(
            select(distinct(QueryJob.user_id), QueryJob.template_id)
            .where(*jc, QueryJob.source == SOURCE_RUN)
        ).all()
    )
    dormant = run_pairs - used_pairs

    # 明细要能直接行动:谁、哪张任务、谁授的、什么时候授的
    dormant_detail = []
    if dormant:
        uids = {u for u, _ in dormant}
        dids = {t for _, t in dormant}
        users = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_(uids)))}
        tmpls = {
            t.id: t for t in db.scalars(select(SqlTemplate).where(SqlTemplate.id.in_(dids)))
        }
        granters = {
            u.id: u.name
            for u in db.scalars(
                select(User).where(User.id.in_({b for b, _ in run_grants.values() if b}))
            )
        }
        for uid, tid in sorted(dormant)[:TOP_N]:
            by, at = run_grants.get((uid, tid), (None, None))
            t = tmpls.get(tid)
            dormant_detail.append({
                "user_id": uid, "user_name": users.get(uid),
                "template_id": tid, "template_name": t.name if t else None,
                "team_name": t.team_name if t else None,
                "granted_by_name": granters.get(by),
                "granted_at": at,
            })

    # ③ 僵尸编辑权:授过 edit,但这个人已经不在该任务所属团队里了 —— can_edit 因此静默失效,
    #    行却还在。有明确的下一步(撤掉),所以值得单列
    members = {(tid, uid) for tid, uid in db.execute(
        select(TeamMember.team_id, TeamMember.user_id)
    ).all()}
    stale_edit = sum(
        1
        for uid, tid in edit_pairs
        if tmpl_team.get(tid) is not None and (tmpl_team[tid], uid) not in members
    )

    # ④ 授权面最大的任务 —— 授权面失控是取数平台唯一的合规风险点
    wide: dict[int, int] = {}
    for _uid, tid in run_pairs:
        wide[tid] = wide.get(tid, 0) + 1
    top_ids = sorted(wide, key=lambda t: -wide[t])[:TOP_N]
    wide_names = {
        t.id: t for t in db.scalars(select(SqlTemplate).where(SqlTemplate.id.in_(top_ids)))
    } if top_ids else {}
    wide_access = [
        {
            "template_id": tid,
            "name": wide_names[tid].name if tid in wide_names else None,
            "team_name": wide_names[tid].team_name if tid in wide_names else None,
            "granted_users": wide[tid],
        }
        for tid in top_ids
    ]

    # ⑤ 取数账号覆盖:**直接转调 credential_service,不重算**。「此刻跑不动的已上线任务」
    #    这条口径由它的 not_ready_templates 独家持有,两个视角都走同一个函数 ——
    #    在这里照着抄一份团队版,就等于让口径分了家,而且改了其中一处不会有任何报错。
    #    看板不需要库账号名,一律不取(reveal_username=False)—— 少一个泄密面
    if scope.is_platform:
        ov = credential_service.overview(db)
        configured = sum(
            1 for t in ov["teams"] for c in t["credentials"] if c.get("configured")
        )
        required = len(ov["teams"]) * max(1, len(ov["datasources"])) if ov["teams"] else 0
        not_ready_count = len(ov["not_ready_templates"])
    else:
        cells = credential_service.list_for_team(db, scope.team_id, reveal_username=False)
        configured = sum(1 for c in cells if c["configured"])
        required = len(cells)
        not_ready_count = len(credential_service.not_ready_templates(db, scope.team_id))
    credential_block = {
        "configured": configured,
        "required": required,
        # 两张卡都在这里就做成信封:覆盖率的分母为 0 时它是 None(「没得算」),
        # 让前端各自写 `required ? configured / required : null` 迟早有一处写成 0%
        "not_ready": metric(not_ready_count, windowed=False, has_data=True),
        "coverage": metric(
            ratio(configured, required), windowed=False, has_data=bool(required)),
    }

    # ⑥ 下载:集中度不是指控,是发现「一个人在给全组取数」这种反模式
    dl_scope = by_job(DownloadEvent.job_id, jc)
    dl_rows = db.execute(
        select(DownloadEvent.user_id, func.count(DownloadEvent.id),
               func.max(DownloadEvent.row_count))
        .where(*dl_scope, *in_window(DownloadEvent.created_at, window))
        .group_by(DownloadEvent.user_id)
    ).all()
    dl_total = sum(r[1] for r in dl_rows)
    dl_top = sorted(dl_rows, key=lambda r: -r[1])[:TOP_N]
    dl_names = {
        u.id: u.name for u in db.scalars(select(User).where(User.id.in_([r[0] for r in dl_top])))
    } if dl_top else {}

    body = {
        "as_of": {
            "grants_total": metric(len(run_pairs), windowed=False),
            "dormant_grants": metric(len(dormant), windowed=False,
                                     has_data=bool(run_pairs)),
            "dormant_ratio": metric(ratio(len(dormant), len(run_pairs)), windowed=False,
                                    has_data=bool(run_pairs)),
            "stale_edit_grants": metric(stale_edit, windowed=False),
            "credentials": credential_block,
        },
        "dormant_detail": dormant_detail,
        "wide_access_tasks": wide_access,
        "downloads": {
            "total": metric(dl_total, windowed=True),
            "top_users": [
                {"user_id": uid, "user_name": dl_names.get(uid), "downloads": n,
                 "max_rows": mx}
                for uid, n, mx in dl_top
            ],
            "concentration": metric(
                ratio(dl_top[0][1], dl_total) if dl_top else None,
                windowed=True, has_data=bool(dl_total),
            ),
        },
    }

    if scope.is_platform:
        # ---- 平台视角限定。团队管理员这里**一个键都没有**,前端整块不渲染
        role_rows = dict(db.execute(
            select(User.role, func.count(User.id))
            .where(User.last_login_at.isnot(None), User.is_active.is_(True))
            .group_by(User.role)
        ).all())
        teams = list(db.scalars(select(Team)))
        admin_team_ids = set(db.scalars(
            select(TeamMember.team_id).where(TeamMember.is_team_admin.is_(True))
        ))
        body["platform"] = {
            "role_distribution": {
                ROLE_ADMIN: role_rows.get(ROLE_ADMIN, 0),
                ROLE_DEVELOPER: role_rows.get(ROLE_DEVELOPER, 0),
                ROLE_USER: role_rows.get(ROLE_USER, 0),
            },
            "teams_total": len(teams),
            # 没有团队管理员的团队 = 治理黑洞:没人能配账号、加成员、授编辑权
            "teams_without_admin": metric(
                sum(1 for t in teams if t.id not in admin_team_ids), windowed=False,
                has_data=bool(teams),
            ),
            "audit_actions": [
                {"action": act, "label": ACTION_META.get(act, (act, ""))[0], "count": n}
                for act, n in db.execute(
                    select(AuditLog.action, func.count(AuditLog.id))
                    .where(*in_window(AuditLog.created_at, window))
                    .group_by(AuditLog.action)
                    .order_by(func.count(AuditLog.id).desc())
                ).all()
            ],
        }

    return envelope(scope, window, body)


# ---------------------------------------------------------------- 板块⑤ 开放 API

# 「活跃 token」的固定回看窗口。刻意**不吃页面上的时间范围**:它回答的是「这些长期凭证
# 最近还活没活着」(安全卫生问题),而不是「这段时间 API 用得怎样」(那是 api_runs)
API_TOKEN_ACTIVE_DAYS = 7


def api_usage(db: Session, scope: AnalyticsScope, window) -> dict:
    """「开放 API 有没有被用起来」—— token 发放与活跃、API 来源的运行与下载、Top 任务。

    token 属于**人**而不是任务,团队视角按「该团队的成员」收窄(成员身份天然可收窄,
    同 scope_template_ids 的取舍);运行与下载照旧走 job_conditions / by_job,
    不另立收窄口径。
    """
    jc = job_conditions(scope)
    win = in_window(QueryJob.created_at, window)

    # ① token:发放数(此刻快照)与近 7 天活跃数。三列同生同灭(见 api_token_service),
    #    以 hash 非空为「有 token」的唯一判据
    token_conds = [User.api_token_hash.isnot(None)]
    if not scope.is_platform:
        token_conds.append(
            User.id.in_(select(TeamMember.user_id).where(TeamMember.team_id == scope.team_id))
        )
    active_since = datetime.now() - timedelta(days=API_TOKEN_ACTIVE_DAYS)
    # 发放数与近 7 天活跃数是同一批行上的两个聚合,一次查完 ——
    # 各查一次是对同一批行的重复扫描(本模块 ①② 板块的既有约定)
    issued, active_7d = db.execute(
        select(
            func.count(User.id),
            func.sum(case((User.api_token_last_used_at >= active_since, 1), else_=0)),
        ).where(*token_conds)
    ).one()
    active_7d = int(active_7d or 0)

    # ② API 来源的运行:窗口内次数与占比(分母 = 窗口内全部运行)。has_data 看全期,
    #    与其它窗口指标同一约定(「这段时间没人用」与「从来没人用过」指向不同的下一步)
    # 窗口内 API 运行数与其分母(全部运行)谓词完全一致(*jc, *win),
    # 是同一批行上的两个聚合,一次查完;各查一次是对同一批行的重复扫描
    total_runs, api_runs = db.execute(
        select(
            func.count(QueryJob.id),
            func.sum(case((QueryJob.source == SOURCE_API, 1), else_=0)),
        ).where(*jc, *win)
    ).one()
    api_runs = int(api_runs or 0)
    api_runs_ever = db.scalar(
        select(func.count(QueryJob.id)).where(*jc, QueryJob.source == SOURCE_API)
    ) or 0

    # ③ API 下载(DownloadEvent.via;web 下载不进这个数)
    dl_scope = by_job(DownloadEvent.job_id, jc)
    api_downloads = db.scalar(
        select(func.count(DownloadEvent.id)).where(
            *dl_scope, *in_window(DownloadEvent.created_at, window),
            DownloadEvent.via == VIA_API,
        )
    ) or 0

    # ④ API 调用 Top 任务:先 GROUP BY 拿 id,再一次 IN 补名字 —— 不在循环里 db.get
    top_rows = db.execute(
        select(QueryJob.template_id, func.count(QueryJob.id))
        .where(*jc, *win, QueryJob.source == SOURCE_API)
        .group_by(QueryJob.template_id)
        .order_by(func.count(QueryJob.id).desc())
        .limit(TOP_N)
    ).all()
    name_map = {
        t.id: t
        for t in db.scalars(
            select(SqlTemplate).where(SqlTemplate.id.in_([tid for tid, _ in top_rows]))
        )
    } if top_rows else {}
    top_tasks = []
    for tid, n in top_rows:
        t = name_map.get(tid)
        top_tasks.append({
            "template_id": tid,
            "name": t.name if t else None,
            "team_name": t.team_name if t else None,
            "run_count": n,
        })

    return envelope(scope, window, {
        "tokens_issued": metric(issued, windowed=False),
        "tokens_active_7d": metric(active_7d, windowed=False, has_data=bool(issued)),
        "api_runs": metric(api_runs, windowed=True, has_data=bool(api_runs_ever)),
        # 分母为 0 时是 None(「没得算」),不是 0 —— 同 ratio 的既有口径
        "api_run_share": metric(ratio(api_runs, total_runs), windowed=True,
                                has_data=bool(total_runs)),
        "api_downloads": metric(api_downloads, windowed=True, has_data=bool(api_runs_ever)),
        "top_tasks": top_tasks,
    })

"""运营分析:可见范围(scope)、收窄谓词,以及各板块的聚合口径。

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
from app.models.audit import DownloadEvent
from app.models.datasource import DataSource
from app.models.permission import (
    ACTION_EDIT, ACTION_RUN, RESOURCE_TEMPLATE, SUBJECT_USER, Permission,
)
from app.models.query_job import (
    JOB_FAILED, JOB_QUEUED, JOB_RUNNING, JOB_SOURCES, JOB_SUCCESS,
    SOURCE_API, SOURCE_RUN, SOURCE_SUBSCRIBE, SOURCE_TEST, QueryJob,
)
from app.models.subscription import TaskSchedule
from app.models.team import Team, TeamMember
from app.models.template import (
    STATUS_ARCHIVED, STATUS_DRAFT, STATUS_PUBLISHED, SqlTemplate,
)
from app.models.user import ROLE_USER, SYSTEM_SCHEDULER_OPEN_ID, User, is_platform_admin
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


def execution_conditions(scope: AnalyticsScope) -> list:
    """「一次执行」的条件 = 作用域收窄 + **排除补推记录**。运行次数 / 成功率 / 耗时这类
    按执行统计的板块都用它,而不是直接用 job_conditions。

    补推(pushed_from_job_id 非空)是把一次已有的运行结果再交付给订阅者,不是一次执行 ——
    算进来的话每补推一次就多一条「定时运行成功」,失败的那一期会被报成 50% 成功率。
    下载事件照旧挂 job_conditions:补推记录上的下载是真下载。
    """
    return [*job_conditions(scope), QueryJob.pushed_from_job_id.is_(None)]


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

    相邻两个窗口因此首尾相接、不重不漏,环比才算得对(见 core/timewindow)。
    """
    return [column >= window.start, column < window.end]


# ---------------------------------------------------------------- 板块① 采纳与活跃

# 「活跃 token」的固定回看窗口。刻意**不吃页面上的时间范围**:它回答的是「这些长期凭证
# 最近还活没活着」(安全卫生问题),而不是「这段时间 API 用得怎样」(那是 api_runs)
API_TOKEN_ACTIVE_DAYS = 7


def adoption(db: Session, scope: AnalyticsScope, window) -> dict:
    """「平台有没有人用、用得深不深」。

    全篇最要紧的一条口径:**活跃取数人只算 source='run' 的自然人,不含试跑**。
    试跑是「做任务的人在验证自己的产品」,把它计入采纳率等于允许开发者自己刷高这块板的
    核心数字 —— 而这块板存在的意义恰恰是回答「业务方真的在自助用吗」。

    开放 API 是采纳的另一条腿(界面之外还有没有人在取数),只是运行来源中的一种,
    所以不单立板块:API 调用与 token 活跃度并在这里。
    """

    jc = execution_conditions(scope)
    sys_ids = system_user_ids(db)
    # 排除定时运行的挂名人。不排的话「活跃取数人」会凭空多一个、而且永远活跃
    not_system = [QueryJob.user_id.notin_(sys_ids)] if sys_ids else []

    # ① 窗口内按 source 的次数与成功数,**一次查完**
    counts = {
        src: (n, int(ok or 0))
        for src, n, ok in db.execute(
            select(
                QueryJob.source,
                func.count(QueryJob.id),
                func.sum(case((QueryJob.status == JOB_SUCCESS, 1), else_=0)),
            )
            .where(*jc, *in_window(QueryJob.created_at, window))
            .group_by(QueryJob.source)
        ).all()
    }

    # ② 全期:同样按 source,用来回答 has_data 与「最近一次在什么时候」
    ever = {
        src: (n, last)
        for src, n, last in db.execute(
            select(QueryJob.source, func.count(QueryJob.id), func.max(QueryJob.created_at))
            .where(*jc)
            .group_by(QueryJob.source)
        ).all()
    }

    def _job_metric(src: str):
        """某个 source 的窗口内次数。

        has_data 一律看**全期**有没有过 —— 窗口内为 0 要能说清是「这段时间没人跑」
        还是「从来没人跑过」,这两句话指向完全不同的下一步。
        """
        ever_n, ever_last = ever.get(src, (0, None))
        return metric(counts.get(src, (0, 0))[0], windowed=True, has_data=bool(ever_n),
                      last_event_at=ever_last)

    # ③ 本窗口与上一个等长窗口的活跃人数(环比用)。两个窗口首尾相接,一趟扫描按时间分两桶去重
    prev_window = timewindow.previous(window)
    in_cur = QueryJob.created_at >= window.start
    cur_users, prev_users = db.execute(
        select(
            func.count(distinct(case((in_cur, QueryJob.user_id)))),
            func.count(distinct(case((~in_cur, QueryJob.user_id)))),
        ).where(
            *jc, *not_system,
            QueryJob.source == SOURCE_RUN,
            QueryJob.created_at >= prev_window.start, QueryJob.created_at < window.end,
        )
    ).one()

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

    # ⑤ 下载转化的分子;分母(成功运行数)从 ① 里取。
    #    按 job 收窄但**不排除补推**:补推记录上的下载是真下载
    dl_n = db.scalar(
        select(func.count(DownloadEvent.id))
        .where(*by_job(DownloadEvent.job_id, job_conditions(scope)),
               *in_window(DownloadEvent.created_at, window))
    ) or 0
    success_jobs = sum(ok for _n, ok in counts.values())

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

    # ⑦ token:发放数(此刻快照)与近 7 天活跃数,同一批行上的两个聚合一次查完。
    #    token 属于**人**而不是任务,团队视角按「该团队的成员」收窄。
    #    三列同生同灭(见 api_token_service),以 hash 非空为「有 token」的唯一判据
    token_conds = [User.api_token_hash.isnot(None)]
    if not scope.is_platform:
        token_conds.append(
            User.id.in_(select(TeamMember.user_id).where(TeamMember.team_id == scope.team_id))
        )
    active_since = datetime.now() - timedelta(days=API_TOKEN_ACTIVE_DAYS)
    issued, active_7d = db.execute(
        select(
            func.count(User.id),
            func.sum(case((User.api_token_last_used_at >= active_since, 1), else_=0)),
        ).where(*token_conds)
    ).one()

    run_n = counts.get(SOURCE_RUN, (0, 0))[0]

    return envelope(scope, window, {
        "run_jobs": _job_metric(SOURCE_RUN),
        "scheduled_jobs": _job_metric(SOURCE_SUBSCRIBE),
        "api_runs": _job_metric(SOURCE_API),
        "active_users": metric(
            cur_users, windowed=True,
            has_data=bool(ever.get(SOURCE_RUN, (0, None))[0]),
            prev_value=prev_users,
        ),
        # 跑了不下载 = 结果没用上。最容易被忽略的一个信号
        "download_per_success": metric(ratio(dl_n, success_jobs), windowed=True,
                                       has_data=bool(success_jobs)),
        # 刻意不做「节省 X 人天」:那需要一个拍脑袋的工时假设,
        # 而一旦写进看板就会变成 KPI,反过来污染数据
        "self_service_ratio": metric(ratio(biz_runs, run_n), windowed=True,
                                     has_data=bool(run_n)),
        "tokens_issued": metric(issued, windowed=False),
        "tokens_active_7d": metric(int(active_7d or 0), windowed=False, has_data=bool(issued)),
        "daily_series": [by_day[k] for k in sorted(by_day)],
    })


# ---------------------------------------------------------------- 板块② 运行健康

# 失败归因最多分多少条(取最近的)。窗口内失败上千已是事故级,再多拉只是更慢,分布不会变
FAILURE_SAMPLE_CAP = 5_000
# other 桶回传多少条样例(界面只摆这么多)。这是失败归因规则表能演进的唯一机制 —— 没有它,
# other 会永远是最大的桶,且没人知道该往里加什么规则。
UNBUCKETED_SAMPLE_LIMIT = 3
UNBUCKETED_SAMPLE_CHARS = 200


def health(db: Session, scope: AnalyticsScope, window) -> dict:
    """「跑得顺不顺」。

    成功率**必须按 source 拆三份**:试跑失败是正常的研发过程(写 SQL 就是试错),
    定时失败才是事故。合并成一个数会让平台看起来一团糟,然后所有人学会忽略这个数字。
    """

    jc = execution_conditions(scope)
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
    # 只给 P90:一个数就能回答「慢不慢」,P50/P95 并排摆着没人会去对比
    duration = {
        engine: {"samples": len(vals), "p90_ms": am.percentiles(vals, ps=(90,))[90]}
        for engine, vals in by_engine.items()
    }

    # ⑤ 排队:started_at - created_at,只数「等过一分钟以上」的次数 —— 直指并发不足,
    #    比分位数更好向人解释。**排除试跑**(它同步执行不入队)。
    #    只拉**盖过章**的行:历史行 started_at 为空,不拿 0 充数 —— 那会把全部历史
    #    算成「零排队」,而且看起来很健康。差值在 Python 里算:时间相减 MySQL 与 SQLite 写法不同
    queue_stamped_ever = db.scalar(
        select(QueryJob.id).where(*jc, QueryJob.started_at.isnot(None)).limit(1)
    ) is not None
    waits = [
        (started - created).total_seconds()
        for created, started in db.execute(
            select(QueryJob.created_at, QueryJob.started_at)
            .where(
                *jc, *win, QueryJob.source != SOURCE_TEST,
                QueryJob.status.in_(TERMINAL), QueryJob.started_at.isnot(None),
            )
            .limit(am.PERCENTILE_SAMPLE_CAP)
        ).all()
        if created is not None and started >= created
    ]

    # ⑥ 失败归因:拉 error 文本在内存里分桶(规则要频繁迭代,且 LIKE 的大小写敏感性
    #    在 MySQL 与 SQLite 上不同 —— 放 SQL 里两边会算出不同的分类)。
    #    读 job.error 而不是审计 detail:前者已过 credential_service.redact 脱敏,
    #    后者是原文,团队视角下会漏库账号名
    err_rows = db.scalars(
        select(QueryJob.error)
        .where(*jc, *win, QueryJob.status == JOB_FAILED)
        .order_by(QueryJob.id.desc())
        .limit(FAILURE_SAMPLE_CAP)
    ).all()

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

    # ⑧ 此刻的队列 —— **不吃时间窗**。切时间范围它不该变
    in_flight = dict(db.execute(
        select(QueryJob.status, func.count(QueryJob.id))
        .where(*jc, QueryJob.status.in_((JOB_QUEUED, JOB_RUNNING)))
        .group_by(QueryJob.status)
    ).all())

    # 刻意**不返回**一个合并的总成功率:它把「试跑失败」和「定时失败」加在一起,
    # 正是本板块开头那条口径要避免的读法
    run = by_source[SOURCE_RUN]
    return envelope(scope, window, {
        "by_source": by_source,
        # 顶部那几张卡各自是一个完整的指标信封。**不要让前端拿 by_source 里的裸数字
        # 自己拼 {value, has_data, windowed}** —— has_data 的口径是「**全期**有没有过」,
        # 前端手拼时只看得见本区间,于是「从来没跑过」会被渲染成一个绿色的 0
        "run_success_rate": metric(
            run["success_rate"], windowed=True, has_data=bool(ever_terminal)),
        "run_failed": metric(run["failed"], windowed=True, has_data=bool(ever_terminal)),
        "queue_over_60s": metric(
            sum(1 for w in waits if w >= 60) if waits else None,
            # 「有没有过排队记录」看全期盖过章的行,不看终态数 —— 只有历史行的平台
            # 该显示「还没有数据」,而不是一个有数据却为空的卡
            windowed=True, has_data=queue_stamped_ever,
        ),
        "queued_now": metric(in_flight.get(JOB_QUEUED, 0), windowed=False, has_data=True),
        "daily_series": [daily[k] for k in sorted(daily)],
        "duration_by_engine": duration,
        "zero_row_jobs": metric(zero_rows, windowed=True, has_data=bool(ever_terminal)),
        "failure_buckets": failure_buckets,
        "unbucketed_samples": unbucketed,
        "in_flight": {
            "queued": in_flight.get(JOB_QUEUED, 0),
            "running": in_flight.get(JOB_RUNNING, 0),
        },
        "by_datasource": by_datasource,
    })


# ---------------------------------------------------------------- meta

# 时间范围预设(天)。前端的快捷档与这里同源,改这里前端跟着变。
PRESET_DAYS = [7, 30, 90]

# 每条指标的口径说明。**单一真源** —— 前端不自己维护一份中文解释,否则口径改了文案不改,
# 页面上那句「什么算活跃」会变成一句错话,而且没人会发现。
# 各板块的 note 都必须从这里取(前端 notes[key]?.note);在 JSX 里直接写中文,
# 等于把这条约定破掉一半 —— 而破掉的那一半不会报错,只会慢慢说假话。
METRIC_NOTES: list[dict] = [
    {"key": "run_jobs", "label": "正式取数", "windowed": True,
     "note": "业务方填参跑的次数。不含作者试跑,也不含定时运行。"},
    {"key": "scheduled_jobs", "label": "定时运行", "windowed": True,
     "note": "平台按订阅计划自动跑的次数，挂在系统账号名下。"},
    {"key": "active_users", "label": "活跃取数人", "windowed": True,
     "note": "发起过正式取数的不同的人。**不含试跑** —— 否则开发者调试很勤会被读成业务很活跃。"},
    {"key": "download_per_success", "label": "下载转化", "windowed": True,
     "note": "下载次数 ÷ 成功运行次数。偏低说明跑出来的结果没被真正用上。"},
    {"key": "self_service_ratio", "label": "业务自助率", "windowed": True,
     "note": "普通用户发起的正式取数占比。上升即意味着取数真的从数据同学手里转移出去了。"},
    {"key": "success_rate", "label": "成功率", "windowed": True,
     "note": "成功 ÷（成功 + 失败）。排队中与运行中不进分母 —— 它们还没有结论。"},
    {"key": "queue", "label": "等待超过 1 分钟", "windowed": True,
     "note": "入队后等了一分钟以上才开始执行的次数，偏多说明并发不够。试跑不入队，不计入；"
             "早于该功能上线的历史运行没有记录，也不当成零等待。"},
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
    {"key": "dormant_grants", "label": "授权后从没跑过", "windowed": False,
     "note": "授了权却一次都没用过的（人 × 任务）。这是**全期**口径，不随上方时间范围变化"
             "——授权是存量事实，套时间窗会把三个月前用过的人误报成僵尸。"},
    {"key": "stale_edit_grants", "label": "失效的编辑权", "windowed": False,
     "note": "授过编辑权、人却已不在该任务所属团队里。权限实际已经失效，但这行还留着，该撤掉。"},
    {"key": "credential_not_ready", "label": "缺账号的已上线任务", "windowed": False,
     "note": "这些任务现在就跑不动——业务同学点运行会直接失败。"},
    {"key": "teams_without_admin", "label": "没有团队管理员", "windowed": False,
     "note": "没人能给它配取数账号、加成员、授编辑权——治理黑洞。"},
    {"key": "tokens_active_7d", "label": "近 7 天用过的 Token", "windowed": False,
     "note": "最近 7 天调用过开放 API 的 Token 数 / 此刻已发放的 Token 数（每人至多一枚，吊销即不计入）。"
             "**固定看 7 天**，不随上方时间范围变化——它问的是「这些长期凭证还活着吗」。"
             "团队视角按**团队成员**收窄——token 属于人，不属于任务。"},
    {"key": "api_runs", "label": "API 调用", "windowed": True,
     "note": "来源是开放 API 的运行次数。同一张任务在界面上被跑的次数不计入。"},
]


def meta(db: Session, user: User) -> dict:
    """页面初始化需要的一切:能选的范围、预设、口径说明、以及「平台有没有开张」。"""

    options = scope_options(db, user)
    scope = resolve_scope(db, user)
    jc = execution_conditions(scope)
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
    jc = execution_conditions(scope)
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

    # ③ 窗口内的运行排行:次数 / 去重使用人数 / 成功率,一次 GROUP BY 拿齐、在库里截到前 N
    ranked = db.execute(
        select(
            QueryJob.template_id,
            func.count(QueryJob.id),
            func.count(distinct(QueryJob.user_id)),
            func.sum(case((QueryJob.status == JOB_SUCCESS, 1), else_=0)),
            func.sum(case((QueryJob.status == JOB_FAILED, 1), else_=0)),
        )
        .where(*jc, *win, QueryJob.source == SOURCE_RUN)
        .group_by(QueryJob.template_id)
        .order_by(func.count(QueryJob.id).desc())
        .limit(TOP_N)
    ).all()
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

    # ④ 计划开着**且任务已上线**才算真的在跑 —— 下线即暂停(TaskSchedule 刻意没有 paused 列),
    # 只看 enabled 会虚报
    schedules_on = db.scalar(
        select(func.count(TaskSchedule.id))
        .select_from(TaskSchedule)
        .join(SqlTemplate, SqlTemplate.id == TaskSchedule.template_id)
        .where(*tc, TaskSchedule.enabled.is_(True), SqlTemplate.status == STATUS_PUBLISHED)
    ) or 0

    return envelope(scope, window, {
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
        },
        "top_templates": top_templates,
        "idle_list": idle_rows[:TOP_N],
    })


# ---------------------------------------------------------------- 板块④ 权限与配置治理


def governance(db: Session, scope: AnalyticsScope, window) -> dict:
    """「有没有治理漏洞」—— 权限膨胀与配置缺口。

    **团队视角不提供任何 audit_logs 派生指标。** 审计接口本身是 admin-only,运营板不该开一条
    绕过它的侧门;而且 audit_logs.resource_id 是 VARCHAR、难安全收窄,detail 里还可能带别队
    信息(如 task_team_transfer 的 from/to team),逐条脱敏的成本远大于收益。团队管理员改用
    Permission 与 TaskSubscription 这类**挂得住 template_id 的存量表**(天然可收窄),
    够用且零泄漏风险 —— 这是个干净的边界,不是临时妥协。
    """

    tc = template_conditions(scope)
    jc = execution_conditions(scope)
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

    body = {
        "as_of": {
            "grants_total": metric(len(run_pairs), windowed=False),
            "dormant_grants": metric(len(dormant), windowed=False,
                                     has_data=bool(run_pairs)),
            "stale_edit_grants": metric(stale_edit, windowed=False),
            "credentials": credential_block,
        },
        "dormant_detail": dormant_detail,
        "wide_access_tasks": wide_access,
    }

    if scope.is_platform:
        # ---- 平台视角限定。团队管理员这里**一个键都没有**,前端整块不渲染
        # 没有团队管理员的团队 = 治理黑洞:没人能配账号、加成员、授编辑权。
        # 两个计数同一趟拿齐:has_data 看的是「有没有团队」,不是「有没有黑洞」
        teams_total, without_admin = db.execute(
            select(
                func.count(Team.id),
                func.sum(case((Team.id.notin_(
                    select(TeamMember.team_id).where(TeamMember.is_team_admin.is_(True))
                ), 1), else_=0)),
            )
        ).one()
        body["platform"] = {
            "teams_without_admin": metric(
                int(without_admin or 0), windowed=False, has_data=bool(teams_total),
            ),
        }

    return envelope(scope, window, body)

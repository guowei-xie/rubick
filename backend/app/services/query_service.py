"""业务用户取数。

**异步**执行(不依赖 Redis/Celery):
  submit_run() —— 请求内:鉴权/校验参数/安全网关(_admit)→ 优先复用同参结果 / 接上在途运行,
                  否则建 queued 任务 → 立即返回(enqueue() 是不带复用与上限的「必定新建」原语)
  execute_job() —— 由独立的 DB 轮询 worker(app.worker)拾取 queued 任务后执行:
                   连接器 → 落地本地文件 → 审计 → 通知(自管独立 DB 会话)

RUN_INLINE=true 时 execute_job 在请求内同步执行(无需 worker,便于本地开发)。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import case, func, literal, select, tuple_, update
from sqlalchemy.orm import Session

from app.connectors import get_connector
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.exceptions import (
    ConflictError,
    CredentialRequiredError,
    NotFoundError,
    PermissionDeniedError,
    ResultExpiredError,
    RubicError,
)
from app.core.sql_gateway import validate_readonly
from app.core.timewindow import start_of_today
from app.models.audit import (
    ACTION_API_RUN_DENIED,
    ACTION_QUERY_CANCEL,
    ACTION_RUN_QUERY,
    ACTION_RUN_QUERY_FAILED,
    ACTION_SUBMIT_QUERY,
    VIA_API,
    VIA_WEB,
)
from app.models.datasource import DataSource
from app.models.permission import RESOURCE_TEMPLATE
from app.models.query_job import (
    JOB_CANCELLED,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
    JOB_SUCCESS,
    SOURCE_API,
    SOURCE_RUN,
    SOURCE_SUBSCRIBE,
    SOURCE_TEST,
    QueryJob,
)
from app.models.template import STATUS_PUBLISHED, SqlTemplate, TemplateVersion
from app.models.user import User
from app.schemas.common import ParamDef
from app.models.datasource import ENGINE_HIVE, ENGINE_MYSQL
from app.services import (
    audit_service,
    credential_service,
    notify_service,
    params_service,
    permission_service,
    result_service,
)


def _run_as_detail(job: QueryJob) -> dict:
    """审计 detail 里的取数身份 = 这次用了哪个团队的库账号。

    为空只发生在「身份还没解析出来就失败了」的行上(如入队即被拒),此时无字段可记。
    """
    if not job.run_as_username:
        return {}
    return {"run_as": {"team_id": job.run_as_team_id, "db_username": job.run_as_username}}


def effective_timeout(tmpl: SqlTemplate | None, ds: DataSource) -> int:
    """该任务生效的查询超时(秒):任务显式配置优先,否则按引擎默认
    (Hive 用 HIVE_QUERY_TIMEOUT_SECONDS,其余用 QUERY_TIMEOUT_SECONDS)。

    tmpl 可空:编辑器里试跑一个**还没保存**的任务时没有任务行,那就只剩引擎默认这一档。
    收在这里而不是让调用方各写一遍 —— 「这条 SQL 该给多少秒」只表述一次。
    """
    if tmpl is not None and tmpl.timeout_seconds:
        return tmpl.timeout_seconds
    if ds and ds.engine == ENGINE_HIVE:
        return settings.HIVE_QUERY_TIMEOUT_SECONDS
    return settings.QUERY_TIMEOUT_SECONDS


def _claim_tier(source):
    """认领档位:0 = 网页取数与订阅定时运行,1 = API。source 给列就得到 SQL 表达式,给值就得到数。"""
    if isinstance(source, str):
        return 1 if source == SOURCE_API else 0
    return case((source == SOURCE_API, 1), else_=0)


def claim_order():
    """worker 认领 queued 任务的顺序:(档位, id) —— **非 API 来源优先**,同档内先到先得。

    为什么 API 往后排:API 调用方是脚本与 Agent,本来就在轮询、能等;而网页上的人盯着抽屉。
    一批 API 运行排在前面时,FIFO 会让人工取数跟着等完整批 —— 队列「挤」的体感正是这个。
    代价是网页流量持续不断时 API 会一直往后让;按平台的实际用量(人工取数远少于 Agent 调用),
    这个饥饿只在理论上成立,真出现了再加老化(等太久的 API 提档)。

    queue_ahead 用同一个 (档位, id) 键数位次,顺序只在 _claim_tier 定义一次。
    """
    return (_claim_tier(QueryJob.source), QueryJob.id)


def queue_ahead(db: Session, job: QueryJob) -> int:
    """这条 queued 任务前面还排着几个(不含正在跑的)。

    = 认领键(claim_order)比它小的 queued 行数。口径住在执行侧同一个模块、与认领共用一个键,
    不散在路由里;路由只决定「什么时候展示」。
    """
    ahead = tuple_(*claim_order()) < tuple_(literal(_claim_tier(job.source)), literal(job.id))
    return db.scalar(
        select(func.count())
        .select_from(QueryJob)
        .where(QueryJob.status == JOB_QUEUED, ahead)
    ) or 0


def _visible_task(db: Session, user: User, template_id: int):
    """取任务并验可见:返回 (任务, 视角)。视角一次算出,调用方后续的权限问题共用。

    **不可见一律 404**,与单条运行记录同口径(routes.query.load_job):不暴露
    「存在但无权」。否则拿一枚 token 逐个试编号就能问出平台上有哪些任务。
    """
    tmpl = db.get(SqlTemplate, template_id)
    if tmpl is None:
        raise NotFoundError("任务不存在")
    scope = permission_service.team_scope(db, user)
    if not permission_service.can_view(scope, tmpl):
        raise NotFoundError("任务不存在")
    return tmpl, scope


@dataclass(frozen=True)
class _ParamKey:
    """「两次运行是不是同参」的比对依据:这个上线版本的参数定义 + 本次入参归一后的形状。"""

    defs: list[ParamDef]
    want: dict

    @classmethod
    def of(cls, version: TemplateVersion, values: dict) -> "_ParamKey":
        """参数定义只解析一次、入参只绑定一次;不合法照常抛(与入队同文案)。"""
        defs = [ParamDef.model_validate(p) for p in version.params]
        return cls(defs, params_service.canonical(params_service.validate_and_bind(defs, values)))

    def matches(self, params: dict) -> bool:
        # 同一上线版本的行入队时已按这份定义校验过,重绑只为归一(数值串 → 数值等),不会抛
        return params_service.canonical(params_service.validate_and_bind(self.defs, params)) == self.want


# submit_run 的第二个返回值:这次提交是怎么被满足的。None = 新建了一条运行去执行
REUSE_RESULT = "result"      # 复用了时效内的同参成功结果(另建一条指向来源的 success 记录)
REUSE_INFLIGHT = "inflight"  # 接上了一条同参、还在排队/执行的运行(不建新行)


def _admit(
    db: Session, user: User, template_id: int, values: dict, ip: str | None, source: str
) -> tuple[SqlTemplate, TemplateVersion, _ParamKey]:
    """入队前的全部关卡:可见、已上线、可运行、运行闸、参数、只读网关、取数身份。

    复用与接上在途**都在这之后**:没有运行权的人不能借「已经有人跑过」拿到一条运行。

    **运行闸**:API 触发要求任务的「允许 API 调用」(allow_api)是开着的 —— 新任务默认开,
    但被编辑者关掉的任务不该因为某枚 token 就对外可跑;拒绝会记审计
    (排查 Agent 接入时第一个要看的地方)。
    """
    tmpl, scope = _visible_task(db, user, template_id)
    if tmpl.status != STATUS_PUBLISHED or tmpl.published_version_id is None:
        raise RubicError("任务未上线,不可运行")
    if not permission_service.can_run(scope, tmpl):
        raise PermissionDeniedError("无权运行该任务")
    if source == SOURCE_API and not tmpl.allow_api:
        # 此刻还没有任何写入(建行在后面),audit_service.log 的 commit 不会误提交半成品
        audit_service.log(
            db, user=user, action=ACTION_API_RUN_DENIED,
            resource_type=RESOURCE_TEMPLATE, resource_id=tmpl.id, resource_name=tmpl.name,
            detail={"reason": "allow_api_off"}, ip=ip,
        )
        raise PermissionDeniedError(
            "该任务未开放 API 调用:请让任务编辑者在编辑器中开启「允许 API 调用」"
        )

    version = db.get(TemplateVersion, tmpl.published_version_id)
    # 请求内先做参数校验与安全网关,把可预见的错误即时反馈给用户。
    # 校验的产物(归一后的参数)就是复用比对的依据,一并交回,不再绑第二遍
    key = _ParamKey.of(version, values)
    validate_readonly(version.sql_text, tmpl.dialect)
    # 取数身份(= 任务所属团队的账号)也在请求内先探一次:缺了就当场告诉业务用户找谁去配,
    # 而不是让他等 worker 跑完、再从运行记录里读一条失败原因。
    # 这条**才是**「账号没配/失效」的常见落点(worker 侧那条只在排队期间被改掉时才触发),
    # 所以通知能修的人必须挂在这里 —— 否则业务用户干等,而团队管理员毫不知情。
    try:
        credential_service.for_template(db, tmpl)
    except CredentialRequiredError:
        notify_service.notify_credential_blocked(db, tmpl, requester_id=user.id)
        raise
    return tmpl, version, key


def _log_submit(
    db: Session, user: User, tmpl: SqlTemplate, job: QueryJob, values: dict,
    source: str, ip: str | None, **extra,
) -> None:
    audit_service.log(
        db, user=user, action=ACTION_SUBMIT_QUERY, resource_type=RESOURCE_TEMPLATE,
        resource_id=tmpl.id, ip=ip,
        detail={
            "job_id": job.id, "params": values,
            "via": VIA_API if source == SOURCE_API else VIA_WEB,
            **extra,
        },
    )


def _insert_queued(
    db: Session, user: User, tmpl: SqlTemplate, version: TemplateVersion,
    values: dict, ip: str | None, source: str,
) -> QueryJob:
    job = QueryJob(
        user_id=user.id,
        template_id=tmpl.id,
        template_version_id=version.id,
        datasource_id=tmpl.datasource_id,
        params=values,
        status=JOB_QUEUED,
        # API 触发的运行与界面触发的分开记账:分析维度与运行记录来源标记全靠它
        source=source,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    _log_submit(db, user, tmpl, job, values, source, ip)

    # 交给 worker 后台执行;RUN_INLINE 模式则在请求内同步执行(便于本地开发,无需 worker)
    if settings.RUN_INLINE:
        execute_job(job.id, ip)
        db.refresh(job)  # 此时已是 success/failed
    return job


def enqueue(
    db: Session, user: User, template_id: int, values: dict,
    ip: str | None = None, *, source: str = SOURCE_RUN,
) -> QueryJob:
    """前置校验后**必定新建**一条 queued 运行。返回它(RUN_INLINE 模式下返回时可能已完成)。

    这是不带复用、不带在途上限的原语;面向用户的两个入口(界面 POST /run、开放 API
    POST /tasks/{id}/runs)走 submit_run —— 那里在它前面叠了「优先复用 / 接上在途 / 上限」。

    source = 这次运行从哪来,直接用 QueryJob 的既有词表(run=界面 / api=开放 API)——
    「触发通道」与「运行来源」是同一个事实,不为入队路径再建一套 via 词汇。
    审计 detail 里仍写 via(那是审计自己的词表,DownloadEvent 也用它)。
    """
    tmpl, version, _key = _admit(db, user, template_id, values, ip, source)
    return _insert_queued(db, user, tmpl, version, values, ip, source)


def submit_run(
    db: Session, user: User, template_id: int, values: dict,
    ip: str | None = None, *, source: str = SOURCE_RUN, fresh: bool = False,
) -> tuple[QueryJob, str | None]:
    """用户点「运行」/ 调用方 POST 运行时的入口:**复用优先,重跑始终可用**。

    关卡(_admit)全部过完之后,依次:
      1. fresh=False 且时效内有同参成功结果 → 复用(REUSE_RESULT);
      2. fresh=False 且有同参在途运行 → 接上它(REUSE_INFLIGHT);
      3. API 来源受每用户在途上限约束(API_MAX_INFLIGHT_PER_USER),超出 429;
      4. 新建一条运行入队。
    fresh=True 跳过 1、2,一定真跑一次 —— 「复用只是优先」,重新运行的能力不因它丢失。
    上限对 fresh 也生效:它限的是一个人同时占几个队列位,不是能不能重跑。

    **为什么跨用户复用是安全的**:同任务、同上线版本、同参数的结果与谁跑无关(取数身份
    跟着任务走,见 credential_service.for_template;SQL 只由版本与参数渲染)。调用者已过
    can_run,而 can_run ⊂ can_view,can_view 本来就能打开这个任务下的任意一条运行
    (permission_service.can_access_job);下载仍按调用者本人的授权判(can_download_job
    不给发起人开短路),所以复用不放宽任何权限。

    并发:两个完全同时到达的同参请求可能都查不到对方、各建一行。概率低、代价只是多跑一次,
    不值得为它加锁。
    """
    tmpl, version, key = _admit(db, user, template_id, values, ip, source)
    if not fresh:
        src = _reusable_result(db, tmpl, version, key)
        if src is not None:
            return _copy_reused(db, user, tmpl, src, values, source, ip), REUSE_RESULT
        # 界面只接本人的在途运行:完成通知只发给发起人,接上别人的那条,他就等不到「跑完了」。
        # API 不发通知(调用方自己在轮询),看得见就能接
        inflight = _inflight_job(
            db, tmpl, version, key, user_id=None if source == SOURCE_API else user.id
        )
        if inflight is not None:
            _log_submit(
                db, user, tmpl, inflight, values, source, ip, reuse_kind=REUSE_INFLIGHT
            )
            return inflight, REUSE_INFLIGHT
    if source == SOURCE_API:
        _check_inflight_cap(db, user, tmpl, ip)
    return _insert_queued(db, user, tmpl, version, values, ip, source), None


def _reuse_since(db: Session, tmpl: SqlTemplate) -> datetime | None:
    """复用时效的起点;None = 这个任务此刻不复用。

    Hive 等 T+1 数仓当天内结果不变,以当天零点为界;MySQL 是实时库,只认最近
    RESULT_REUSE_MYSQL_MINUTES 分钟(且不跨天)。任务级开关与全局开关任一关着都不复用。
    """
    if not settings.RESULT_REUSE_ENABLED or not tmpl.allow_result_reuse:
        return None
    today = start_of_today()
    ds = db.get(DataSource, tmpl.datasource_id)
    if ds is not None and ds.engine == ENGINE_MYSQL:
        minutes = settings.RESULT_REUSE_MYSQL_MINUTES
        if minutes <= 0:
            return None
        # 「最近 N 分钟」对着库时钟算:比较的对象 started_at / created_at 都是库时钟写的
        # (见 QueryJob.started_at),混用应用时钟会在两者不在同一时区/机器时整段错开
        return max(today, db.scalar(select(func.now())) - timedelta(minutes=minutes))
    return today


def _same_param_job_ids(
    db: Session, tmpl: SqlTemplate, version: TemplateVersion, key: _ParamKey, *conds,
):
    """同任务、同上线版本、非试跑、满足 conds 且**归一后参数相同**的运行 id,新的在前。

    参数比对在 Python 里做:canonical 把值列表变成集合(不看顺序与重复),SQL 里表达不了。
    只取比对要用的两列 —— 整行会连带 executed_sql(大列表参数下几十 KB)与两个 joined 关系。
    """
    stmt = (
        select(QueryJob.id, QueryJob.params)
        .where(
            QueryJob.template_id == tmpl.id,
            QueryJob.template_version_id == version.id,
            QueryJob.datasource_id == tmpl.datasource_id,
            QueryJob.source != SOURCE_TEST,
            *conds,
        )
        .order_by(QueryJob.id.desc())
    )
    for job_id, params in db.execute(stmt):
        if key.matches(params):
            yield job_id


def _reusable_result(
    db: Session, tmpl: SqlTemplate, version: TemplateVersion, key: _ParamKey
) -> QueryJob | None:
    """时效内同参、成功、结果还在的**原始**运行(取最新);没有返回 None。

    只认真正执行过的那条:复用出来的记录与补推记录都不当来源 —— 否则「数据是什么时候取的」
    要沿着链一路往回找,时效也会按复制时刻而不是取数时刻算。
    时效按**开始执行**的时刻判(数据新鲜度取决于它,不取决于排队多久);早于该列上线的
    历史行退到入队时刻。
    """
    since = _reuse_since(db, tmpl)
    if since is None:
        return None
    for job_id in _same_param_job_ids(
        db, tmpl, version, key,
        QueryJob.status == JOB_SUCCESS,
        func.coalesce(QueryJob.started_at, QueryJob.created_at) >= since,
        QueryJob.reused_from_job_id.is_(None),
        QueryJob.pushed_from_job_id.is_(None),
    ):
        job = db.get(QueryJob, job_id)
        if not result_service.is_gone(job):
            return job
    return None


def _inflight_job(
    db: Session, tmpl: SqlTemplate, version: TemplateVersion, key: _ParamKey,
    *, user_id: int | None,
) -> QueryJob | None:
    """同参、还在排队或执行的运行(取最新);user_id 给了就只找这个人发起的。"""
    conds = [QueryJob.status.in_((JOB_QUEUED, JOB_RUNNING))]
    if user_id is not None:
        conds.append(QueryJob.user_id == user_id)
    job_id = next(_same_param_job_ids(db, tmpl, version, key, *conds), None)
    return db.get(QueryJob, job_id) if job_id is not None else None


def _copy_reused(
    db: Session, user: User, tmpl: SqlTemplate, src: QueryJob, values: dict,
    source: str, ip: str | None,
) -> QueryJob:
    """复用 = 另建一条与来源共用结果文件的 success 记录(QueryJob.sharing_result_of,补推也用它)。

    发起人填调用者、来源填调用者自己的通道:于是它出现在调用者的运行记录里,
    下载授权按调用者本人判,运营分析也能按人按通道看到这次使用。
    执行类统计据 reused_from_job_id 排除它。
    不发完成通知:界面上用户当场就拿到了结果,API 本来就不发。
    """
    job = QueryJob.sharing_result_of(
        src,
        user_id=user.id,
        params=values,
        source=source,
        reused_from_job_id=src.id,
        reused_from_at=src.started_at or src.created_at,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    _log_submit(
        db, user, tmpl, job, values, source, ip,
        reuse_kind=REUSE_RESULT, reused_from_job_id=src.id,
    )
    return job


def _check_inflight_cap(db: Session, user: User, tmpl: SqlTemplate, ip: str | None) -> None:
    """每用户同时在途的 API 运行不超过 API_MAX_INFLIGHT_PER_USER;超出 429 并留审计。

    文案与每分钟限流的 429 分开说:那个退避重试就好,这个重试多少次都一样 —— 得等自己的
    运行跑完,或取消不要的那条。
    """
    cap = settings.API_MAX_INFLIGHT_PER_USER
    if cap <= 0:
        return
    inflight = db.scalar(
        select(func.count())
        .select_from(QueryJob)
        .where(
            QueryJob.user_id == user.id,
            QueryJob.source == SOURCE_API,
            QueryJob.status.in_((JOB_QUEUED, JOB_RUNNING)),
        )
    ) or 0
    if inflight < cap:
        return
    audit_service.log(
        db, user=user, action=ACTION_API_RUN_DENIED,
        resource_type=RESOURCE_TEMPLATE, resource_id=tmpl.id, resource_name=tmpl.name,
        detail={"reason": "inflight_cap", "inflight": inflight, "cap": cap}, ip=ip,
    )
    raise RubicError(
        f"你已有 {inflight} 条 API 运行在排队或执行(每人同时最多 {cap} 条):"
        "请等它们结束,或用 DELETE /api/v1/runs/{job_id} 取消不再需要的那条,再提交",
        status_code=429,
    )


def cancel_job(db: Session, user: User, job_id: int, ip: str | None = None) -> QueryJob:
    """取消一条**本人**发起、还在排队的运行。

    只认本人:别人的一律 404(与 routes.query.load_job 同一「不暴露存在但无权」口径)——
    看得见别人的运行不等于能替他取消。只认 queued:用条件 UPDATE 抢,与 worker 的认领
    互斥;已经被认领(running)或已结束的返回 409。执行中的查询不支持取消。
    """
    job = db.get(QueryJob, job_id)
    if job is None or job.user_id != user.id:
        raise NotFoundError("运行记录不存在")
    result = db.execute(
        update(QueryJob)
        .where(QueryJob.id == job_id, QueryJob.status == JOB_QUEUED)
        .values(status=JOB_CANCELLED)
    )
    db.commit()
    db.refresh(job)
    if result.rowcount != 1:
        raise ConflictError(
            "这次运行已经开始执行或已经结束,无法取消"
            if job.status != JOB_CANCELLED else "这次运行已经取消过了"
        )
    audit_service.log(
        db, user=user, action=ACTION_QUERY_CANCEL, resource_type=RESOURCE_TEMPLATE,
        resource_id=job.template_id, ip=ip, detail={"job_id": job.id},
    )
    return job


def find_reusable_job(
    db: Session, user: User, template_id: int, values: dict
) -> QueryJob | None:
    """时效内已经用同样参数跑成、结果还在的那条运行(取最新);没有返回 None。

    给开放 API 的「先找现成结果再决定跑不跑」用 —— 如今 POST /runs 自己就会优先复用,
    这个端点留作兼容。判据与 submit_run 的复用**同一份**(_reusable_result):同任务同一
    上线版本、成功且非试跑、时效内、结果没过期也没丢,跨用户。**比对在服务端做**:对外的
    job 投影刻意不带入参(V1JobOut 注释),调用方自己在 /runs 里对不上参数,只能瞎猜。

    只取结果不跑,所以不要求 can_run / allow_api,只要任务可见 —— 可见就能打开该任务下的
    任意一条运行(can_access_job);下载权照旧由 assert_downloadable 在取文件时把关。
    参数不合法与 submit_run 同样报错 —— 否则调用方会把「没找到」读成「去跑一次」,
    而那一次注定失败。
    """
    tmpl, _scope = _visible_task(db, user, template_id)
    if tmpl.status != STATUS_PUBLISHED or tmpl.published_version_id is None:
        return None
    version = db.get(TemplateVersion, tmpl.published_version_id)
    return _reusable_result(db, tmpl, version, _ParamKey.of(version, values))


def enqueue_scheduled(db: Session, tmpl: SqlTemplate) -> QueryJob:
    """订阅计划到期的入队 —— enqueue 的无发起人变体,只由 subscription_service.tick 调用。

    跳过 permission_service.can(RUN):调度不是人,「谁能订」已在订阅时按 can_view 把过关,
    真正的取数身份照旧跟着任务走(团队账号)。其余骨架与 enqueue 一致;job 的 user_id
    填系统用户(见 models/user.SYSTEM_SCHEDULER_OPEN_ID 的说明,不能填作者)。

    params 非空的任务不该有可用的订阅计划(卡点在 template_service.add_version),
    这里 fail-closed 再兜一道 —— 与其组一个缺参数的运行让 worker 去失败,不如当场拒绝。
    """
    from app.services import subscription_service

    if tmpl.status != STATUS_PUBLISHED or tmpl.published_version_id is None:
        raise RubicError("任务未上线,订阅计划不应触发")
    version = db.get(TemplateVersion, tmpl.published_version_id)
    if version.params:
        raise RubicError("任务包含变量,不能定时自动运行,请先关闭订阅")
    validate_readonly(version.sql_text, tmpl.dialect)
    # 取数身份探活;缺账号原样上抛,由 tick 走「通知能修的人 + 订阅者简讯」的失败流
    credential_service.for_template(db, tmpl)

    job = QueryJob(
        user_id=subscription_service.scheduler_user(db).id,
        template_id=tmpl.id,
        template_version_id=version.id,
        datasource_id=tmpl.datasource_id,
        params={},
        status=JOB_QUEUED,
        source=SOURCE_SUBSCRIBE,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    # 不走 RUN_INLINE:tick 只在 worker 进程里跑,建出的 queued job 由本进程自己的
    # 认领循环消化;执行与通知照旧走 execute_job → notify_job_done。
    return job


def execute_job(job_id: int, ip: str | None = None) -> None:
    """worker 侧执行。自管独立 DB 会话,幂等地推进任务状态并发通知。"""
    db = SessionLocal()
    failure: Exception | None = None
    try:
        job = db.get(QueryJob, job_id)
        if job is None or job.status not in (JOB_QUEUED, JOB_RUNNING):
            return
        user = db.get(User, job.user_id)
        tmpl = db.get(SqlTemplate, job.template_id)
        version = db.get(TemplateVersion, job.template_version_id)
        ds = db.get(DataSource, job.datasource_id)

        job.status = JOB_RUNNING
        # 排队到此为止。用 DB 时钟对齐 created_at(见 QueryJob.started_at 的注释);
        # `is None` 的守卫让这次盖章幂等 —— execute_job 对 worker 已认领过的 job 会重入,
        # 无条件赋值会把开始时刻一路推后。
        if job.started_at is None:
            job.started_at = func.now()
        db.commit()

        # 必须在 try 之前:失败分支要用它给 job.error 脱敏,而失败可能发生在身份解析之前
        # (参数校验 / SQL 网关),那时它就是 None —— redact(None) 原样返回,不必分叉。
        credential = None
        try:
            bound = params_service.validate_and_bind(version.params, job.params)
            validate_readonly(version.sql_text, tmpl.dialect)
            # 取数身份 = 任务所属团队(不是发起人、也不是作者)。入队时已探过一次,
            # 这里是 worker 侧的纵深防御:排队期间凭证可能被团队管理员改掉或被回收
            credential = credential_service.for_template(db, tmpl)
            if credential.is_team_account:
                job.run_as_team_id = credential.owner_team_id
                job.run_as_username = credential.username
            sql_text, bound = params_service.expand_list_params(version.sql_text, bound)  # 展开正选 IN
            # 存下最终 SQL 供查阅。超长会被自动截断 —— 规则收在模型层
            # (QueryJob 的 validates,见 clip_executed_sql),赋值即生效。
            job.executed_sql = params_service.render_sql(sql_text, bound)
            db.commit()
            connector = get_connector(ds, credential)
            filename = f"{tmpl.name}_{job.id}.csv"
            object_key = f"jobs/{job.id}/{filename}"
            # 边取边写盘:结果行数不再有平台上限(见 settings.MAX_RESULT_ROWS 的说明),
            # 所以这条链路上不能有「先把所有行收进内存」的一步。duration 由这里计时 ——
            # 它现在盖住的是「提交查询到结果落盘」整段,连接器不再自己报时长。
            start = time.perf_counter()
            with connector.stream(
                sql_text, bound, timeout_seconds=effective_timeout(tmpl, ds)
            ) as (columns, rows):
                row_count, truncated = result_service.write_csv(
                    object_key, columns, rows, max_rows=settings.result_row_cap
                )
            duration_ms = int((time.perf_counter() - start) * 1000)

            job.status = JOB_SUCCESS
            job.row_count = row_count
            job.duration_ms = duration_ms
            job.result_object_key = object_key
            job.result_filename = filename
            db.commit()

            audit_service.log(
                db, user=user, action=ACTION_RUN_QUERY, resource_type=RESOURCE_TEMPLATE,
                resource_id=tmpl.id,
                detail={"template_version_id": version.id, "datasource": ds.name,
                        "params": job.params, "row_count": row_count,
                        # 只有配了 MAX_RESULT_ROWS 才可能为 true(那一项的说明里讲了
                        # 这条记录为什么重要),不限行数时恒 false。
                        "truncated": truncated, "executed_sql": (job.executed_sql or "")[:20000],
                        # 用哪个团队的库账号取的数 —— 按团队隔离数据权限后,这是审计的关键一列
                        **_run_as_detail(job)},
                ip=ip,
            )
        except Exception as e:
            failure = e  # 交给 notify_service 判断这次失败还该通知谁
            job.status = JOB_FAILED
            # job.error 是**面向用户**的:它经 JobOut 展示给运行记录查看者,还会被
            # notify_service 推进飞书通知。而引擎的鉴权报错会带上库账号名
            # (`Access denied for user 'team_acct'@...`),业务用户根本不属于这个团队,
            # 却能就此拿到团队库账号 —— Hive auth=NONE 下那就是完整凭证。故这里必须脱敏。
            job.error = credential_service.redact(str(e), credential)[:2000]
            db.commit()
            audit_service.log(
                db, user=user, action=ACTION_RUN_QUERY_FAILED, resource_type=RESOURCE_TEMPLATE,
                resource_id=tmpl.id,
                # 审计 detail 保留**原文**:这是 admin-only 的取证面,抹掉就查不出哪个账号被拒
                detail={"error": str(e)[:500], "params": job.params,
                        "executed_sql": (job.executed_sql or "")[:20000],
                        **_run_as_detail(job)},
                ip=ip,
            )

        notify_service.notify_job_done(db, job, failure)
    finally:
        db.close()


# 一条 running 记录要「老」到什么程度才判定为孤儿。取库里所有可能的超时上限之上再加一段缓冲
# —— 判早了会把还在正常跑的长任务打死,那比留一条孤儿糟得多。
STALE_RECLAIM_GRACE_SECONDS = 600

# 收回来的 job 写给用户看的原因。**面向发起人**,所以要说清「没结果」和「该怎么办」,
# 而不是留一句技术描述让他自己猜。
_RECLAIM_ERROR = (
    "取数进程在本次运行期间被中断(服务重启或异常退出),这次运行没有产出结果,请重新运行。"
)


def stale_after_seconds(db: Session) -> int:
    """running 超过这个秒数即视为孤儿。

    盖住三个上限里最大的那个:全局默认、Hive 默认、以及**库里某个任务自己配的**超时
    —— 只按全局默认算的话,一个配了 6 小时的任务会在正常运行途中被判死。
    """
    longest = db.scalar(select(func.max(SqlTemplate.timeout_seconds))) or 0
    ceiling = max(
        settings.QUERY_TIMEOUT_SECONDS, settings.HIVE_QUERY_TIMEOUT_SECONDS, int(longest)
    )
    return ceiling + STALE_RECLAIM_GRACE_SECONDS


def reclaim_stale_jobs() -> int:
    """把卡死在 running 的运行记录收回成 failed 并通知发起人。返回收回条数。

    **为什么必须有**:worker 的停止处理只置一个标志、不打断进行中的查询,而 systemd 默认
    90 秒后 SIGKILL。于是每次 `deploy.sh update`,只要有一个跑了 90 秒以上的取数,那条记录
    就永久停在「运行中」——业务用户在抽屉里等到轮询窗口耗尽,然后那条记录再也不会变,
    没有报警、没有重试、没人知道。这是由部署动作本身保证会发生的事,不是理论。

    自管独立会话(同 execute_job):它由 worker 在请求之外调用。
    时间比较全部取**库时钟**,不掺应用本地时钟 —— 跨时钟比较是这套代码里反复踩过的坑。
    """
    db = SessionLocal()
    try:
        cutoff = db.scalar(select(func.now())) - timedelta(seconds=stale_after_seconds(db))
        stale = list(
            db.scalars(
                select(QueryJob).where(
                    QueryJob.status == JOB_RUNNING, QueryJob.updated_at < cutoff
                )
            )
        )
        for job in stale:
            job.status = JOB_FAILED
            job.error = _RECLAIM_ERROR
            db.commit()
            # 必须通知:不通知等于把发起人继续晾着 —— 那正是这个函数要消灭的状态
            notify_service.notify_job_done(db, job, RuntimeError(_RECLAIM_ERROR))
        return len(stale)
    finally:
        db.close()


def assert_downloadable(db: Session, user: User, job: QueryJob) -> None:
    """「这次运行的结果现在拿得到吗」—— 可见、下载权、状态、保留期四道校验。

    界面(签名 URL 两步)与开放 API(Bearer 直出)共用:改保留期文案或判据只改这一处。
    两条权限规则都住在 permission_service:能不能看见这条记录(can_access_job)、
    能不能取走文件(require_can_download_job)。**预览不叠第二道** —— 跑完能看前 50 行、
    取走完整结果另需「下载」授权,是产品口径(见 routes/query.preview_payload)。

    顺序即语义:权限(恒定答案)在状态与保留期(此刻答案)之前。反过来的话,同一个没有
    下载权的人对同一条记录会先后收到两句不同的话 —— 跑完之前「无可下载结果」、跑完之后
    「无权下载」,而 Agent 会把前者当瞬时故障一路轮询下去。
    """
    # 两问共用同一份视角与同一行任务:TeamScope 固定 2 次查询,各问各算就是白付一遍
    scope = permission_service.team_scope(db, user)
    tmpl = db.get(SqlTemplate, job.template_id)
    if not permission_service.can_access_job(db, user, job, scope=scope, tmpl=tmpl):
        raise PermissionDeniedError("无权下载该次运行结果")
    permission_service.require_can_download_job(db, user, job, scope=scope, tmpl=tmpl)
    if job.status != JOB_SUCCESS or not job.result_object_key:
        raise RubicError("该次运行无可下载结果")
    if result_service.is_gone(job):
        raise ResultExpiredError()


def get_download_url(db: Session, user: User, job: QueryJob, ip: str | None = None) -> str:
    assert_downloadable(db, user, job)
    # 返回带签名 token 的根相对 URL,浏览器新标签页可直接下载(不需 Authorization 头)。
    # 单端口部署下 /api 与 SPA 同源;本地 split dev 模式由 vite 代理到后端。
    from app.core.security import create_download_token

    token = create_download_token(job.id)
    url = f"/api/jobs/{job.id}/file?t={token}"
    audit_service.log_download(
        db, user=user, job_id=job.id, filename=job.result_filename, row_count=job.row_count, ip=ip
    )
    return url

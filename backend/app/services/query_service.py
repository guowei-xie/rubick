"""业务用户取数。

**异步**执行(不依赖 Redis/Celery):
  enqueue()  —— 请求内:鉴权/校验参数/安全网关 → 建 queued 任务 → 立即返回
  execute_job() —— 由独立的 DB 轮询 worker(app.worker)拾取 queued 任务后执行:
                   连接器 → 落地本地文件 → 审计 → 通知(自管独立 DB 会话)

RUN_INLINE=true 时 execute_job 在请求内同步执行(无需 worker,便于本地开发)。
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.connectors import get_connector
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.exceptions import (
    CredentialRequiredError,
    NotFoundError,
    PermissionDeniedError,
    RubicError,
)
from app.core.sql_gateway import validate_readonly
from app.models.audit import ACTION_RUN_QUERY, ACTION_RUN_QUERY_FAILED, ACTION_SUBMIT_QUERY
from app.models.datasource import DataSource
from app.models.permission import ACTION_RUN, RESOURCE_TEMPLATE
from app.models.query_job import (
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
    JOB_SUCCESS,
    SOURCE_SUBSCRIBE,
    QueryJob,
)
from app.models.template import STATUS_PUBLISHED, SqlTemplate, TemplateVersion
from app.models.user import User
from app.models.datasource import ENGINE_HIVE
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


def queue_ahead(db: Session, job: QueryJob) -> int:
    """这条 queued 任务前面还排着几个(不含正在跑的)。

    谓词「status=queued 且 id 更小」是对 worker 认领顺序的复述 —— _claim_next_job_id
    按 id 升序认领。将来队列加优先级,这两处必须一起变,所以口径住在执行侧同一个模块,
    不散在路由里;路由只决定「什么时候展示」。
    """
    return db.scalar(
        select(func.count())
        .select_from(QueryJob)
        .where(QueryJob.status == JOB_QUEUED, QueryJob.id < job.id)
    ) or 0


def enqueue(db: Session, user: User, template_id: int, values: dict, ip: str | None = None) -> QueryJob:
    """前置校验后入队。返回 queued 任务(RUN_INLINE 模式下返回时可能已完成)。"""
    tmpl = db.get(SqlTemplate, template_id)
    if tmpl is None:
        raise NotFoundError("任务不存在")
    if tmpl.status != STATUS_PUBLISHED or tmpl.published_version_id is None:
        raise RubicError("任务未上线,不可运行")
    if not permission_service.can(db, user, ACTION_RUN, RESOURCE_TEMPLATE, template_id):
        raise PermissionDeniedError("无权运行该任务")

    version = db.get(TemplateVersion, tmpl.published_version_id)
    # 请求内先做参数校验与安全网关,把可预见的错误即时反馈给用户
    params_service.validate_and_bind(version.params, values)
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

    job = QueryJob(
        user_id=user.id,
        template_id=tmpl.id,
        template_version_id=version.id,
        datasource_id=tmpl.datasource_id,
        params=values,
        status=JOB_QUEUED,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    audit_service.log(
        db, user=user, action=ACTION_SUBMIT_QUERY, resource_type=RESOURCE_TEMPLATE,
        resource_id=tmpl.id, detail={"job_id": job.id, "params": values}, ip=ip,
    )

    # 交给 worker 后台执行;RUN_INLINE 模式则在请求内同步执行(便于本地开发,无需 worker)
    if settings.RUN_INLINE:
        execute_job(job.id, ip)
        db.refresh(job)  # 此时已是 success/failed
    return job


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
            res = connector.execute(
                sql_text, bound,
                timeout_seconds=effective_timeout(tmpl, ds),
                max_rows=settings.MAX_RESULT_ROWS,
            )
            filename = f"{tmpl.name}_{job.id}.csv"
            object_key = f"jobs/{job.id}/{filename}"
            result_service.upload_csv(object_key, result_service.to_csv_bytes(res))

            job.status = JOB_SUCCESS
            job.row_count = res.row_count
            job.duration_ms = res.meta.get("duration_ms")
            job.result_object_key = object_key
            job.result_filename = filename
            db.commit()

            audit_service.log(
                db, user=user, action=ACTION_RUN_QUERY, resource_type=RESOURCE_TEMPLATE,
                resource_id=tmpl.id,
                detail={"template_version_id": version.id, "datasource": ds.name,
                        "params": job.params, "row_count": res.row_count,
                        "truncated": res.truncated, "executed_sql": (job.executed_sql or "")[:20000],
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


def get_download_url(db: Session, user: User, job: QueryJob, ip: str | None = None) -> str:
    # 「谁能看这次运行的结果」只有一条规则,住在 permission_service.can_access_job。
    # 这里曾有一个 can_view_job_result 重复表述同一件事,已删除。
    if not permission_service.can_access_job(db, user, job):
        raise PermissionDeniedError("无权下载该次运行结果")
    if job.status != JOB_SUCCESS or not job.result_object_key:
        raise RubicError("该次运行无可下载结果")
    if job.result_expired or not result_service.exists(job.result_object_key):
        raise RubicError(
            f"结果已超过保留期({settings.RESULT_RETENTION_DAYS} 天)并被自动清理,请重新运行取数"
        )
    # 返回带签名 token 的根相对 URL,浏览器新标签页可直接下载(不需 Authorization 头)。
    # 单端口部署下 /api 与 SPA 同源;本地 split dev 模式由 vite 代理到后端。
    from app.core.security import create_download_token

    token = create_download_token(job.id)
    url = f"/api/jobs/{job.id}/file?t={token}"
    audit_service.log_download(
        db, user=user, job_id=job.id, filename=job.result_filename, row_count=job.row_count, ip=ip
    )
    return url

"""业务用户取数。

**异步**执行(不依赖 Redis/Celery):
  enqueue()  —— 请求内:鉴权/校验参数/安全网关 → 建 queued 任务 → 立即返回
  execute_job() —— 由独立的 DB 轮询 worker(app.worker)拾取 queued 任务后执行:
                   连接器 → 落地本地文件 → 审计 → 通知(自管独立 DB 会话)

RUN_INLINE=true 时 execute_job 在请求内同步执行(无需 worker,便于本地开发)。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.connectors import get_connector
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.exceptions import NotFoundError, PermissionDeniedError, RubicError
from app.core.sql_gateway import validate_readonly
from app.models.audit import ACTION_RUN_QUERY, ACTION_RUN_QUERY_FAILED, ACTION_SUBMIT_QUERY
from app.models.datasource import DataSource
from app.models.permission import ACTION_RUN, RESOURCE_TEMPLATE
from app.models.query_job import JOB_FAILED, JOB_QUEUED, JOB_RUNNING, JOB_SUCCESS, QueryJob
from app.models.template import STATUS_PUBLISHED, SqlTemplate, TemplateVersion
from app.models.user import User
from app.models.datasource import ENGINE_HIVE
from app.services import (
    audit_service,
    notify_service,
    params_service,
    permission_service,
    result_service,
)


def effective_timeout(tmpl: SqlTemplate, ds: DataSource) -> int:
    """该任务生效的查询超时(秒):任务显式配置优先,否则按引擎默认
    (Hive 用 HIVE_QUERY_TIMEOUT_SECONDS,其余用 QUERY_TIMEOUT_SECONDS)。"""
    if tmpl.timeout_seconds:
        return tmpl.timeout_seconds
    if ds and ds.engine == ENGINE_HIVE:
        return settings.HIVE_QUERY_TIMEOUT_SECONDS
    return settings.QUERY_TIMEOUT_SECONDS


def enqueue(db: Session, user: User, template_id: int, values: dict, ip: str | None = None) -> QueryJob:
    """前置校验后入队。返回 queued 任务(RUN_INLINE 模式下返回时可能已完成)。"""
    tmpl = db.get(SqlTemplate, template_id)
    if tmpl is None:
        raise NotFoundError("模板不存在")
    if tmpl.status != STATUS_PUBLISHED or tmpl.published_version_id is None:
        raise RubicError("模板未发布,不可运行")
    if not permission_service.can(db, user, ACTION_RUN, RESOURCE_TEMPLATE, template_id):
        raise PermissionDeniedError("无权运行该模板")

    version = db.get(TemplateVersion, tmpl.published_version_id)
    # 请求内先做参数校验与安全网关,把可预见的错误即时反馈给用户
    params_service.validate_and_bind(version.params, values)
    validate_readonly(version.sql_text, tmpl.dialect)

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


def execute_job(job_id: int, ip: str | None = None) -> None:
    """worker 侧执行。自管独立 DB 会话,幂等地推进任务状态并发通知。"""
    db = SessionLocal()
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

        try:
            bound = params_service.validate_and_bind(version.params, job.params)
            validate_readonly(version.sql_text, tmpl.dialect)
            sql_text, bound = params_service.expand_list_params(version.sql_text, bound)  # 展开正选 IN
            job.executed_sql = params_service.render_sql(sql_text, bound)  # 存下最终 SQL 供查阅
            db.commit()
            connector = get_connector(ds)
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
                        "truncated": res.truncated, "executed_sql": (job.executed_sql or "")[:20000]},
                ip=ip,
            )
        except Exception as e:
            job.status = JOB_FAILED
            job.error = str(e)[:2000]
            db.commit()
            audit_service.log(
                db, user=user, action=ACTION_RUN_QUERY_FAILED, resource_type=RESOURCE_TEMPLATE,
                resource_id=tmpl.id,
                detail={"error": str(e)[:500], "params": job.params,
                        "executed_sql": (job.executed_sql or "")[:20000]},
                ip=ip,
            )

        notify_service.notify_job_done(db, job)
    finally:
        db.close()


def can_view_job_result(db: Session, user: User, job: QueryJob) -> bool:
    """能查看/下载某次运行结果:发起人本人、管理员,或该项目的作者(可看本项目全部运行)。"""
    if permission_service.can_access_job(user, job):
        return True
    return permission_service.is_template_owner(user, db.get(SqlTemplate, job.template_id))


def get_download_url(db: Session, user: User, job: QueryJob, ip: str | None = None) -> str:
    if not can_view_job_result(db, user, job):
        raise PermissionDeniedError("无权下载该任务结果")
    if job.status != JOB_SUCCESS or not job.result_object_key:
        raise RubicError("任务无可下载结果")
    if job.result_expired or not result_service.exists(job.result_object_key):
        raise RubicError(
            f"结果已超过保留期({settings.RESULT_RETENTION_DAYS} 天)并被自动清理,请重新运行取数"
        )
    # 返回带签名 token 的根相对 URL,浏览器新标签页可直接下载(不需 Authorization 头);
    # 前端 /api 由 dev 代理或生产 nginx 反代到后端。
    from app.core.security import create_download_token

    token = create_download_token(job.id)
    url = f"/api/jobs/{job.id}/file?t={token}"
    audit_service.log_download(
        db, user=user, job_id=job.id, filename=job.result_filename, row_count=job.row_count, ip=ip
    )
    return url

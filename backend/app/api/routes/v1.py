"""开放 API v1:面向脚本与 AI Agent 的**稳定对外契约**。

- 只接受 API token(deps.get_api_user),不接受登录 JWT —— 鉴权通道分离的理由见该函数注释;
- 全部端点叠加每用户滑动窗口限流(core/rate_limit),超限 429;
- 薄封装:权限、入队、结果、过期判断全部复用现有 service,这里不做第二份口径;
- 长任务纯轮询:POST 入队即返回 job_id → GET /runs/{job_id} 轮询 → /result 下载。

结果文件保留 RESULT_RETENTION_DAYS 天,过期一律 400(与界面下载同一句报错)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_api_user
from app.api.routes.query import job_out
from app.core import rate_limit
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import NotFoundError, RubicError
from app.models.audit import VIA_API
from app.models.query_job import JOB_SUCCESS, QueryJob
from app.models.template import SqlTemplate, TemplateVersion
from app.models.user import User
from app.schemas.query import JobOut
from app.schemas.v1 import V1RunIn, V1TaskOut
from app.services import (
    audit_service,
    permission_service,
    query_service,
    result_service,
    subscription_service,
)

router = APIRouter(prefix="/v1", tags=["v1"])


def api_user(user: User = Depends(get_api_user)) -> User:
    """v1 公共入口守卫:token 鉴权(get_api_user)+ 每用户限流,所有端点共用。"""
    if not rate_limit.check(user.id):
        raise RubicError(
            f"超出 API 调用频率限制(每用户 {settings.API_RATE_LIMIT_PER_MINUTE} 次/分钟),"
            "请稍后重试",
            status_code=429,
        )
    return user


def _load_job(db: Session, user: User, job_id: int) -> QueryJob:
    """取一条我有权看的运行记录。不可见一律 404(与界面口径一致:不暴露「存在但无权」)。"""
    job = db.get(QueryJob, job_id)
    if job is None or not permission_service.can_access_job(db, user, job):
        raise NotFoundError("运行记录不存在")
    return job


@router.get("/tasks", response_model=list[V1TaskOut])
def list_tasks(db: Session = Depends(get_db), user: User = Depends(api_user)):
    """我可见的任务列表。可见口径与界面任务列表同源(permission_service.visible_condition)。"""
    scope = permission_service.team_scope(db, user)
    stmt = select(SqlTemplate).order_by(SqlTemplate.id.desc())
    cond = permission_service.visible_condition(scope)
    rows = list(db.scalars(stmt if cond is None else stmt.where(cond)))

    # 参数定义取自各任务的已上线版本;一次批量取回,避免逐任务查库(N+1)
    pv_ids = [t.published_version_id for t in rows if t.published_version_id]
    versions = (
        {v.id: v for v in db.scalars(select(TemplateVersion).where(TemplateVersion.id.in_(pv_ids)))}
        if pv_ids else {}
    )
    return [
        V1TaskOut(
            id=t.id, name=t.name, description=t.description, status=t.status,
            team_id=t.team_id, team_name=t.team_name,
            allow_api=bool(t.allow_api),
            can_run=permission_service.can_run(scope, t),
            can_download=permission_service.can_download(scope, t),
            params=(versions[t.published_version_id].params or [])
            if t.published_version_id in versions else [],
        )
        for t in rows
    ]


@router.post("/tasks/{template_id}/runs", response_model=JobOut)
def run_task(
    template_id: int,
    data: V1RunIn,
    db: Session = Depends(get_db),
    user: User = Depends(api_user),
    ip: str | None = Depends(client_ip),
):
    """触发一次运行:入队即返回运行记录(含 job_id 与 queue_ahead),用 /runs/{id} 轮询。

    除 can_run 外还要求任务开启了「允许 API 调用」(运行闸在 query_service.enqueue,
    fail-closed)。
    """
    job = query_service.enqueue(db, user, template_id, data.values, ip=ip, via=VIA_API)
    return job_out(db, job)


@router.get("/runs", response_model=list[JobOut])
def list_runs(db: Session = Depends(get_db), user: User = Depends(api_user)):
    """运行记录列表:本人发起的 + 所属团队任务下的全部 + 被授权任务的定时运行。
    口径与界面 /api/jobs 同源(job_visibility_condition),不另立一份。"""
    stmt = select(QueryJob).order_by(QueryJob.id.desc()).limit(100)
    cond = permission_service.job_visibility_condition(permission_service.team_scope(db, user))
    if cond is not None:
        stmt = stmt.where(cond)
    return list(db.scalars(stmt))


@router.get("/runs/{job_id}", response_model=JobOut)
def get_run(job_id: int, db: Session = Depends(get_db), user: User = Depends(api_user)):
    """轮询一次运行的状态:status / row_count / duration_ms / error / queue_ahead。"""
    return job_out(db, _load_job(db, user, job_id))


@router.get("/runs/{job_id}/preview")
def preview_run(job_id: int, db: Session = Depends(get_db), user: User = Depends(api_user)):
    """表头 + 前 50 行(JSON)。口径与界面预览一致(含结果过期判断)。"""
    job = _load_job(db, user, job_id)
    if job.status != JOB_SUCCESS or not job.result_object_key:
        raise RubicError("该次运行无可预览结果")
    if job.result_expired:
        raise RubicError(
            f"结果已超过保留期({settings.RESULT_RETENTION_DAYS} 天)并被自动清理,无法预览"
        )
    columns, rows = result_service.read_csv_preview(job.result_object_key, 50)
    # 「下载或预览都算消费」的打点与界面同层同口径(非订阅 job / 非订阅者零成本)
    subscription_service.mark_consumed(db, user.id, job)
    return {"columns": columns, "rows": rows, "row_count": job.row_count}


@router.get("/runs/{job_id}/result")
def download_result(
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(api_user),
    ip: str | None = Depends(client_ip),
):
    """直接下载结果 CSV(Bearer 鉴权直出,不走界面那套「签名 URL 两步」——
    调用方是脚本/Agent,没有浏览器新标签页的场景)。"""
    job = _load_job(db, user, job_id)
    if job.status != JOB_SUCCESS or not job.result_object_key:
        raise RubicError("该次运行无可下载结果")
    if job.result_expired or not result_service.exists(job.result_object_key):
        raise RubicError(
            f"结果已超过保留期({settings.RESULT_RETENTION_DAYS} 天)并被自动清理,请重新运行取数"
        )
    # 与界面下载同一份留痕,只多一个 via=api 标记(分析据此拆 API 用量)
    audit_service.log_download(
        db, user=user, job_id=job.id, filename=job.result_filename,
        row_count=job.row_count, ip=ip, via=VIA_API,
    )
    subscription_service.mark_consumed(db, user.id, job)
    return FileResponse(
        result_service.local_path(job.result_object_key),
        media_type="text/csv; charset=utf-8",
        filename=job.result_filename or f"result_{job.id}.csv",
    )

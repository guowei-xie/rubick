"""开放 API v1:面向脚本与 AI Agent 的**稳定对外契约**。

- 只接受 API token(deps.get_api_user),不接受登录 JWT —— 鉴权通道分离的理由见该函数注释;
- 全部端点叠加每用户滑动窗口限流(core/rate_limit),超限 429;
- 薄封装:权限、入队、结果、过期判断全部复用现有 service,这里不做第二份口径;
- 长任务纯轮询:POST 入队即返回 job_id → GET /runs/{job_id} 轮询 → /result 下载。

结果文件保留 RESULT_RETENTION_DAYS 天,过期一律 400(与界面下载同一句报错)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_api_user
from app.api.routes import query as query_routes
from app.api.routes.query import job_out
from app.core import rate_limit
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import RubicError
from app.models.audit import VIA_API
from app.models.query_job import SOURCE_API
from app.models.template import TemplateVersion
from app.models.user import User
from app.schemas.query import JobOut
from app.schemas.v1 import V1RunIn, V1TaskOut
from app.services import (
    audit_service,
    permission_service,
    query_service,
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


@router.get("/tasks", response_model=list[V1TaskOut])
def list_tasks(db: Session = Depends(get_db), user: User = Depends(api_user)):
    """我可见的任务列表。可见口径与界面任务列表同源(permission_service.visible_condition)。"""
    scope = permission_service.team_scope(db, user)
    rows = permission_service.visible_templates(db, scope)

    # 参数定义取自各任务的已上线版本;一次批量取回,避免逐任务查库(N+1)。
    # **只取 id 与 params 两列**:TemplateVersion.sql_text 是 Text 列(取数 SQL 动辄几 KB),
    # 而这个端点是 Agent 每次开场都要打的第一枪,整行拉回来等于每次白搬一遍全部 SQL 原文
    pv_ids = [t.published_version_id for t in rows if t.published_version_id]
    params_by_version = dict(
        db.execute(
            select(TemplateVersion.id, TemplateVersion.params)
            .where(TemplateVersion.id.in_(pv_ids))
        ).all()
    )
    out: list[V1TaskOut] = []
    for t in rows:
        params = params_by_version.get(t.published_version_id)
        out.append(V1TaskOut(
            id=t.id, name=t.name, description=t.description, status=t.status,
            team_id=t.team_id, team_name=t.team_name,
            allow_api=bool(t.allow_api),
            can_run=permission_service.can_run(scope, t),
            can_download=permission_service.can_download(scope, t),
            params=params or [],
        ))
    return out


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
    job = query_service.enqueue(db, user, template_id, data.values, ip=ip, source=SOURCE_API)
    return job_out(db, job)


@router.get("/runs", response_model=list[JobOut])
def list_runs(db: Session = Depends(get_db), user: User = Depends(api_user)):
    """运行记录列表:本人发起的 + 所属团队任务下的全部 + 被授权任务的定时运行。
    口径与界面 /api/jobs 同源(query.visible_jobs),不另立一份。"""
    return query_routes.visible_jobs(db, user)


@router.get("/runs/{job_id}", response_model=JobOut)
def get_run(job_id: int, db: Session = Depends(get_db), user: User = Depends(api_user)):
    """轮询一次运行的状态:status / row_count / duration_ms / error / queue_ahead。"""
    return job_out(db, query_routes.load_job(db, user, job_id))


@router.get("/runs/{job_id}/preview")
def preview_run(job_id: int, db: Session = Depends(get_db), user: User = Depends(api_user)):
    """表头 + 前 50 行(JSON)。口径与界面预览同源(query.preview_payload,含结果过期判断)。"""
    return query_routes.preview_payload(db, user, query_routes.load_job(db, user, job_id))


@router.get("/runs/{job_id}/result")
def download_result(
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(api_user),
    ip: str | None = Depends(client_ip),
):
    """直接下载结果 CSV(Bearer 鉴权直出,不走界面那套「签名 URL 两步」——
    调用方是脚本/Agent,没有浏览器新标签页的场景)。

    「拿不拿得到」的三道校验与界面同源(query_service.assert_downloadable);
    本端点独有的只有 via=VIA_API 这一个审计标记。
    """
    job = query_routes.load_job(db, user, job_id)
    query_service.assert_downloadable(db, user, job)
    # 与界面下载同一份留痕,只多一个 via=api 标记(分析据此拆 API 用量)
    audit_service.log_download(
        db, user=user, job_id=job.id, filename=job.result_filename,
        row_count=job.row_count, ip=ip, via=VIA_API,
    )
    subscription_service.mark_consumed(db, user.id, job)
    return query_routes.csv_file_response(job)

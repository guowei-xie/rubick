from __future__ import annotations
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user
from app.core.database import get_db
from app.core.exceptions import NotFoundError
from app.models.query_job import QueryJob
from app.models.user import ROLE_ADMIN, User
from app.schemas.query import JobOut, RunIn
from app.services import permission_service, query_service

router = APIRouter(tags=["query"])


@router.post("/run", response_model=JobOut)
def run_query(data: RunIn, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """业务用户填参运行:入队异步执行,立即返回任务(前端轮询 /jobs/{id} 或看通知)。"""
    job = query_service.enqueue(db, user, data.template_id, data.values, ip=client_ip(request))
    return JobOut.model_validate(job)


@router.get("/jobs", response_model=list[JobOut])
def my_jobs(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    stmt = select(QueryJob).order_by(QueryJob.id.desc()).limit(100)
    if user.role != ROLE_ADMIN:
        stmt = stmt.where(QueryJob.user_id == user.id)
    return list(db.scalars(stmt))


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    job = db.get(QueryJob, job_id)
    if job is None or not permission_service.can_access_job(user, job):
        raise NotFoundError("任务不存在")
    return JobOut.model_validate(job)


@router.get("/jobs/{job_id}/download")
def download(job_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    job = db.get(QueryJob, job_id)
    if job is None:
        raise NotFoundError("任务不存在")
    url = query_service.get_download_url(db, user, job, ip=client_ip(request))
    return {"url": url, "filename": job.result_filename}


@router.get("/jobs/{job_id}/preview")
def preview(job_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """运行结果预览(表头 + 前 50 行)。发起人/管理员/项目作者可看。"""
    from app.core.exceptions import RubicError
    from app.services import result_service

    job = db.get(QueryJob, job_id)
    if job is None or not query_service.can_view_job_result(db, user, job):
        raise NotFoundError("运行记录不存在")
    if job.status != "success" or not job.result_object_key:
        raise RubicError("该次运行无可预览结果")
    if job.result_expired:
        raise RubicError("结果已过期(保留 7 天),无法预览")
    columns, rows = result_service.read_csv_preview(job.result_object_key, 50)
    return {"columns": columns, "rows": rows, "row_count": job.row_count}

from __future__ import annotations
from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user
from app.core.database import get_db
from app.core.exceptions import NotFoundError, ResultExpiredError, RubicError
from app.core.security import verify_download_token
from app.models.query_job import JOB_QUEUED, JOB_SUCCESS, QueryJob
from app.models.user import User
from app.schemas.query import JobOut, RunIn
from app.services import permission_service, query_service, result_service, subscription_service

router = APIRouter(tags=["query"])


def job_out(db: Session, job: QueryJob) -> JobOut:
    """运行记录的单条响应。排队中的额外算一次「前面还有几个」。

    只在 queued 时算:已经在跑的还报位次会让人以为还在排队。一次按 status 索引的 COUNT,
    而轮询是每人每几秒一次,代价可忽略 —— 换来的是用户分得清「系统在跑我的活」和
    「在等别人的活跑完」。位次怎么算住在 query_service.queue_ahead(与 worker 的认领顺序
    同一模块),这里只留「什么时候展示」这个决定。
    公开供 routes/v1 复用:开放 API 的轮询响应与界面轮询是同一份形状。
    """
    out = JobOut.model_validate(job)
    if job.status == JOB_QUEUED:
        out.queue_ahead = query_service.queue_ahead(db, job)
    return out


def load_job(db: Session, user: User, job_id: int) -> QueryJob:
    """取一条我有权看的运行记录。不可见一律 404(不暴露「存在但无权」)。

    公开供 routes/v1 复用:界面与开放 API 对「谁能看这条记录」只有一份口径。
    """
    job = db.get(QueryJob, job_id)
    if job is None or not permission_service.can_access_job(db, user, job):
        raise NotFoundError("运行记录不存在")
    return job


def csv_file_response(job: QueryJob) -> FileResponse:
    """结果 CSV 的文件响应。两条下载路径(签名 URL 与开放 API 的 Bearer 直出)共用 ——
    media_type 与兜底文件名只表述一次。"""
    return FileResponse(
        result_service.local_path(job.result_object_key),
        media_type="text/csv; charset=utf-8",
        filename=job.result_filename or f"result_{job.id}.csv",
    )


@router.post("/run", response_model=JobOut)
def run_query(data: RunIn, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """业务用户填参运行:入队异步执行,立即返回运行记录(前端轮询 /jobs/{id} 或看通知)。"""
    job = query_service.enqueue(db, user, data.template_id, data.values, ip=client_ip(request))
    return job_out(db, job)


# 运行记录列表的条数上限。界面与开放 API 同一个数
JOBS_LIMIT = 100


def visible_jobs(db: Session, user: User) -> list[QueryJob]:
    """我可见的运行记录(最近 JOBS_LIMIT 条)。

    公开供 routes/v1 复用:可见口径(job_visibility_condition)与条数上限只表述一次。
    """
    stmt = select(QueryJob).order_by(QueryJob.id.desc()).limit(JOBS_LIMIT)
    cond = permission_service.job_visibility_condition(
        permission_service.team_scope(db, user)
    )
    if cond is not None:
        stmt = stmt.where(cond)
    return list(db.scalars(stmt))


@router.get("/jobs", response_model=list[JobOut])
def my_jobs(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """本人发起的 + 我所属团队任务下的全部运行;平台管理员看全部。
    口径与 /tasks/{id}/jobs 同源(见 permission_service.job_visibility_condition)。"""
    return visible_jobs(db, user)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return job_out(db, load_job(db, user, job_id))


@router.get("/jobs/{job_id}/download")
def download(job_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    job = db.get(QueryJob, job_id)
    if job is None:
        raise NotFoundError("运行记录不存在")
    url = query_service.get_download_url(db, user, job, ip=client_ip(request))
    # 订阅消费打点:「下载或预览都算消费」这条口径的两个打点并排在路由层
    # (与 preview 同层;/file 那一段只有签名 token、没有操作人,打不了)。
    # 非订阅 job / 非订阅者零成本(见 subscription_service.mark_consumed)。
    subscription_service.mark_consumed(db, user.id, job)
    return {"url": url, "filename": job.result_filename}


@router.get("/jobs/{job_id}/file")
def download_file(job_id: int, t: str, db: Session = Depends(get_db)):
    """凭下载令牌流式返回结果文件。令牌由 /download 签发,放在 URL 里供浏览器直接下载。

    **本端点刻意没有 user**:浏览器新标签页发不了 Authorization 头,所以授权在**签发那一步**
    就花掉了(/download → assert_downloadable,含下载闸),令牌本身即凭证。因此改下载闸不必
    动这里 —— 但要知道代价:令牌不绑人、不可吊销,**撤掉某人的 download 授权不会作废他手上
    已签发的链接**(有效期 DOWNLOAD_URL_EXPIRE_SECONDS),期间它还可以转给别人。
    """
    tok_job_id = verify_download_token(t)
    if tok_job_id != job_id:
        raise NotFoundError("下载链接无效或已过期")
    job = db.get(QueryJob, job_id)
    if job is None or job.status != JOB_SUCCESS or not job.result_object_key:
        raise NotFoundError("该次运行结果不存在")
    if result_service.is_gone(job):
        raise ResultExpiredError()
    return csv_file_response(job)


# 预览返回多少行。界面与开放 API 同一个数,改一处即可
PREVIEW_ROWS = 50


def preview_payload(db: Session, user: User, job: QueryJob) -> dict:
    """结果预览的返回体(校验 + 读前 PREVIEW_ROWS 行 + 订阅消费打点)。

    公开供 routes/v1 复用:预览行数、过期文案、「预览也算消费」这三条口径只表述一次。
    """
    if job.status != JOB_SUCCESS or not job.result_object_key:
        raise RubicError("该次运行无可预览结果")
    if result_service.is_gone(job):
        raise ResultExpiredError()
    columns, rows = result_service.read_csv_preview(job.result_object_key, PREVIEW_ROWS)
    # 订阅消费打点:预览与下载同算「消费」(需求口径),非订阅 job / 非订阅者零成本
    subscription_service.mark_consumed(db, user.id, job)
    return {"columns": columns, "rows": rows, "row_count": job.row_count}


@router.get("/jobs/{job_id}/preview")
def preview(job_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """运行结果预览(表头 + 前 50 行)。发起人本人,或对该任务可见的人可看。"""
    return preview_payload(db, user, load_job(db, user, job_id))

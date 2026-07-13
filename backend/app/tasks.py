"""Celery 任务定义。worker 启动:celery -A app.tasks worker。"""
from __future__ import annotations

from app.core.celery_app import celery_app
from app.services import query_service


@celery_app.task(name="execute_query_job", bind=True, max_retries=0)
def execute_query_job(self, job_id: int, ip: str | None = None) -> None:
    """异步执行一个取数任务。失败已在 execute_job 内落库,不重试(取数结果无副作用重跑意义不大)。"""
    query_service.execute_job(job_id, ip)

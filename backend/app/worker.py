"""独立取数 worker(替代 Celery+Redis)。

轮询 query_jobs 表,原子地认领 queued 任务并执行;定期清理过期结果文件。
启动:python -m app.worker

单进程即可(部署脚本以 nohup 拉起一个)。若崩溃时有任务停留在 running,
不会自动重跑(取数无副作用,重跑意义不大),与原 Celery max_retries=0 行为一致。

注意:当 RUN_INLINE=true(取数在请求内同步执行)时,不会有 queued 任务可认领,
worker 仅承担「定期清理过期结果文件」这一职责;生产建议 RUN_INLINE=false 走异步。
"""
from __future__ import annotations

import signal
import time

from sqlalchemy import update

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging_setup import get_logger
from app.models.query_job import JOB_QUEUED, JOB_RUNNING, QueryJob
from app.services import query_service, result_service

log = get_logger("rubick.worker")
_running = True
# 每隔约 1 小时清理一次过期结果文件
_CLEANUP_EVERY_SECONDS = 3600


def _claim_next_job_id() -> int | None:
    """原子地把最早的一个 queued 任务标记为 running 并返回其 id;没有则 None。

    用 UPDATE ... WHERE id=(子查询) 保证多 worker/重复轮询下不会重复认领。
    """
    db = SessionLocal()
    try:
        job = (
            db.query(QueryJob)
            .filter(QueryJob.status == JOB_QUEUED)
            .order_by(QueryJob.id.asc())
            .first()
        )
        if job is None:
            return None
        result = db.execute(
            update(QueryJob)
            .where(QueryJob.id == job.id, QueryJob.status == JOB_QUEUED)
            .values(status=JOB_RUNNING)
        )
        db.commit()
        return job.id if result.rowcount == 1 else None
    finally:
        db.close()


def _handle_stop(*_a) -> None:
    global _running
    _running = False


def main() -> None:
    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)
    log.info("started; poll=%ss, result_dir=%s", settings.WORKER_POLL_INTERVAL, settings.result_dir_path)
    last_cleanup = 0.0
    while _running:
        job_id = _claim_next_job_id()
        if job_id is not None:
            log.info("executing job %s", job_id)
            try:
                query_service.execute_job(job_id)
            except Exception as e:  # 兜底:execute_job 内部已落库失败,这里只记日志
                log.exception("job %s crashed: %s", job_id, e)
            continue  # 立刻取下一个,不睡

        now = time.time()
        if now - last_cleanup > _CLEANUP_EVERY_SECONDS:
            removed = result_service.cleanup_expired()
            if removed:
                log.info("cleaned %d expired result file(s)", removed)
            last_cleanup = now

        time.sleep(settings.WORKER_POLL_INTERVAL)
    log.info("stopped")


if __name__ == "__main__":
    main()

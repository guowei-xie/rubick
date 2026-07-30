"""独立取数 worker(替代 Celery+Redis)。

轮询 query_jobs 表,原子地认领 queued 任务并执行;定期清理过期结果文件。
启动:python -m app.worker

单进程即可(部署脚本以 nohup 拉起一个)。若崩溃时有任务停留在 running,
不会自动重跑(取数无副作用,重跑意义不大),与原 Celery max_retries=0 行为一致。
"""
from __future__ import annotations

import signal
import time

from sqlalchemy import update

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.query_job import JOB_QUEUED, JOB_RUNNING, QueryJob
from app.services import query_service, result_service

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
    print(f"[worker] started; poll={settings.WORKER_POLL_INTERVAL}s, result_dir={settings.result_dir_path}")
    last_cleanup = 0.0
    while _running:
        job_id = _claim_next_job_id()
        if job_id is not None:
            print(f"[worker] executing job {job_id}")
            try:
                query_service.execute_job(job_id)
            except Exception as e:  # 兜底:execute_job 内部已落库失败,这里只记日志
                print(f"[worker] job {job_id} crashed: {e}")
            continue  # 立刻取下一个,不睡

        now = time.time()
        if now - last_cleanup > _CLEANUP_EVERY_SECONDS:
            removed = result_service.cleanup_expired()
            if removed:
                print(f"[worker] cleaned {removed} expired result file(s)")
            last_cleanup = now

        time.sleep(settings.WORKER_POLL_INTERVAL)
    print("[worker] stopped")


if __name__ == "__main__":
    main()

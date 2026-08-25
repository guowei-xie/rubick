"""独立取数 worker(替代 Celery+Redis)。

轮询 query_jobs 表,原子地认领 queued 任务并**并发**执行;每小时清一次过期结果文件与孤儿记录
(启动时先做一次)。
启动:python -m app.worker

**并发**:`WORKER_CONCURRENCY` 决定同时能跑几个取数(默认 2)。取数几乎全程阻塞在等目标库
回包上,所以线程是对的工具;认领本身早就是原子的(UPDATE ... WHERE status=queued +
rowcount==1),并发只差一个有名额限制的执行池。串行的代价很具体:Hive 默认超时 3600 秒,
一个长任务运行期间全平台的取数都排在它后面。

**孤儿回收**:停止处理只置一个标志、不打断进行中的查询,而 systemd 默认 90 秒后 SIGKILL。
于是每次滚动更新,只要有一个跑了 90 秒以上的取数,那条记录就会永久停在 running。
故启动时、以及每轮清理时都收一次(见 query_service.reclaim_stale_jobs)。

注意:当 RUN_INLINE=true(取数在请求内同步执行)时,不会有 queued 任务可认领,但 worker
**仍必须常驻** —— 它还要清过期结果文件、并回收被中断的 running 记录,而后者在 RUN_INLINE=true
下同样会发生(取数跑在 API 进程里,API 一重启就断)。生产建议 RUN_INLINE=false 走异步。
"""
from __future__ import annotations

import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import update

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging_setup import get_logger
from app.models.query_job import JOB_QUEUED, JOB_RUNNING, QueryJob
from app.services import query_service, result_service, subscription_service

log = get_logger("rubick.worker")
_running = True
# 每隔约 1 小时清理一次过期结果文件,并顺手收一次孤儿记录
_CLEANUP_EVERY_SECONDS = 3600


class JobPool:
    """有名额限制的取数执行池。

    名额用信号量单独管,而不是直接往 ThreadPoolExecutor 里灌 —— 池内排队会让任务早早离开
    queued 状态却其实还没开始跑,那样运行记录里的状态、以及给用户看的「前面还有几个」
    就都成了假话。**只有真的拿到名额才认领**。

    所以真正那道闸是信号量:executor 的 max_workers 只是同一个数的复述(留着是因为
    shutdown(wait=True) 要靠它等在跑的取数收尾,那是停机时不丢结果的唯一依赖)。
    """

    def __init__(self, size: int | None = None):
        # 不传就取配置 —— 「默认并发是多少」只在这一处表述,不再需要第三个入口名
        self.size = max(1, int(settings.WORKER_CONCURRENCY if size is None else size))
        self.slots = threading.Semaphore(self.size)
        self._pool = ThreadPoolExecutor(
            max_workers=self.size, thread_name_prefix="rubick-job"
        )

    def try_acquire(self) -> bool:
        """有空位就占一个,没有就立刻返回 False —— 绝不在这里阻塞:
        轮询循环还要按时去做清理与孤儿回收。"""
        return self.slots.acquire(blocking=False)

    def release(self) -> None:
        self.slots.release()

    def submit(self, job_id: int) -> None:
        self._pool.submit(self._run, job_id)

    def _run(self, job_id: int) -> None:
        log.info("executing job %s", job_id)
        try:
            query_service.execute_job(job_id)
        except Exception as e:  # 兜底:execute_job 内部已落库失败,这里只记日志
            log.exception("job %s crashed: %s", job_id, e)
        finally:
            # 必须在 finally 里还:漏一次名额,worker 的可用并发就永久少一格
            self.release()

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)


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


def claim_and_submit(pool: JobPool) -> bool:
    """有空位就认领一个 queued 任务并提交执行;认领到返回 True。

    抽成一个函数是为了它能被单测驱动 —— 「两个任务真的同时在跑」这件事,
    只有拿得到这一步才验得了。
    """
    if not pool.try_acquire():
        return False  # 满负荷,这一轮不认领
    job_id = _claim_next_job_id()
    if job_id is None:
        pool.release()
        return False
    pool.submit(job_id)
    return True


def _handle_stop(*_a) -> None:
    global _running
    _running = False


def main() -> None:
    # 护栏第二层(第一层在 Settings 构造期,见 config._guard_remote_db):挡运行期被改掉的
    # 开关。worker 是危害最大的那个入口 —— 它替线上**认领**任务,而认领是原子的:
    # 抢到就是抢到,结果文件写在本机,线上那条运行记录永远只有「成功 N 行」却下载不到。
    if not settings.DATABASE_ALLOWED:
        raise SystemExit(settings.remote_db_refusal())
    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)
    pool = JobPool()
    log.info(
        "started; poll=%ss, concurrency=%s, result_dir=%s",
        settings.WORKER_POLL_INTERVAL, pool.size, settings.result_dir_path,
    )
    # 启动即维护一次:上一轮被 SIGKILL 留下的 running 记录现在就收(别让发起人接着等),
    # 过期结果文件也照旧在启动时清一遍(原行为,靠 last_cleanup=0 实现,别改成 now ——
    # 那会让重启频繁的机器永远等不到那一小时)
    _maintenance()
    last_cleanup = time.time()
    # 订阅计划扫描:首轮就扫(0.0),之后每 SCHEDULE_SCAN_INTERVAL_SECONDS 一次。
    # 调度只存在于本进程 —— API 进程不扫,单进程扫描本身就消除了重复触发
    # (水位原子推进再兜一道,见 subscription_service.tick)。
    last_schedule_scan = 0.0
    try:
        while _running:
            if claim_and_submit(pool):
                continue  # 立刻再试下一个,不睡

            now = time.time()
            if now - last_schedule_scan > settings.SCHEDULE_SCAN_INTERVAL_SECONDS:
                _scan_schedules()
                last_schedule_scan = now
            if now - last_cleanup > _CLEANUP_EVERY_SECONDS:
                _maintenance()
                last_cleanup = now

            time.sleep(settings.WORKER_POLL_INTERVAL)
    finally:
        # 等在跑的取数收尾;systemd 的 TimeoutStopSec 到点仍会 SIGKILL,
        # 那种情况由下次启动的 _maintenance() 兜住
        pool.shutdown(wait=True)
    log.info("stopped")


def _scan_schedules() -> None:
    """扫描到期的订阅计划并入队(见 subscription_service.tick)。异常只记日志:
    调度失败不该让取数 worker 停摆,下一轮扫描天然重试(水位没推进,不会丢期)。"""
    try:
        fired = subscription_service.tick()
    except Exception as e:  # noqa: BLE001
        log.exception("订阅计划扫描失败:%s", e)
        return
    if fired:
        log.info("订阅计划触发 %d 个定时取数", fired)


def _maintenance() -> None:
    """周期性维护:清过期结果文件 + 收孤儿运行记录。启动时先做一次,之后每小时一次。"""
    # 尚未被下一期取代的订阅结果不许删(保留到下期,可能远长于常规保留天数)。
    # 保护名单读不出来时**跳过整轮清理**而不是裸删 —— fail 的方向必须是「多留」,
    # 误删订阅结果等于把订阅者本期的数据变没了。
    try:
        protected = subscription_service.protected_result_keys()
    except Exception as e:  # noqa: BLE001
        log.exception("读取受保护的订阅结果清单失败,本轮跳过文件清理:%s", e)
        protected = None
    if protected is not None:
        removed = result_service.cleanup_expired(protected)
        if removed:
            log.info("cleaned %d expired result file(s)", removed)
    try:
        n = query_service.reclaim_stale_jobs()
    except Exception as e:  # noqa: BLE001 -- 回收失败不该让 worker 起不来/停不下
        log.exception("回收卡死的运行记录失败:%s", e)
        return
    if n:
        log.warning("回收了 %d 条卡在 running 的运行记录(上一轮进程被中断)", n)


if __name__ == "__main__":
    main()

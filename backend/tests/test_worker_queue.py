"""护栏:取数队列的两件事 —— 断了要有人收尸,以及别只有一条线。

**F1a 孤儿回收** —— worker 崩溃/被 SIGKILL 时,正在跑的 job 永远停在 running。原先没有任何
地方把它收回来(全库搜 JOB_RUNNING 只有写入、没有回收),而这不是理论:deploy.sh stop 只发
SIGTERM,worker 的处理函数只置一个标志、不打断当前查询,systemd 默认 90 秒后 SIGKILL ——
**每次部署,只要有一个跑了 90 秒以上的取数,那条记录就永久停在「运行中」**。业务用户在抽屉里
等到轮询窗口耗尽,拿到一句「稍后到运行记录看」,然后那条记录再也不会变。

**F1b 并发** —— worker 原先是「认领一个 → 同步跑完 → 再认领下一个」,而 Hive 默认超时是
3600 秒。一个长任务运行期间全平台的取数都排在它后面,前端还只显示同一句「已提交,执行中…」。
_claim_next_job_id 本来就是原子的(条件 UPDATE + rowcount==1),并发只差一个执行池。
"""
import threading
import time
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from app import worker
from app.models.datasource import DataSource
from app.models.query_job import JOB_FAILED, JOB_QUEUED, JOB_RUNNING, JOB_SUCCESS, QueryJob
from app.models.notification import Notification
from app.models.user import ROLE_ADMIN
from app.services import query_service
from tests.conftest import db_now

# ID 段 9150
OWNER = 9150


@pytest.fixture
def owner(user_factory):
    return user_factory(OWNER, ROLE_ADMIN, "发起人", prefix="wq")


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("wq-mysql")


def _job(db, owner, ds, *, status, age_seconds=0, template_id=1):
    job = QueryJob(
        user_id=owner.id, template_id=template_id, datasource_id=ds.id,
        params={}, status=status,
    )
    db.add(job)
    db.commit()
    if age_seconds:
        # 显式写 updated_at 绕开 onupdate,把它推回过去
        db.execute(
            update(QueryJob).where(QueryJob.id == job.id)
            .values(updated_at=db_now(db) - timedelta(seconds=age_seconds))
        )
        db.commit()
    db.refresh(job)
    return job


# ---------------------------------------------------------------- F1a 孤儿回收


def test_reclaims_a_job_stuck_in_running(db, owner, ds):
    """跑了远超任何可能超时的 running 记录 = 上一轮进程被杀了,收回来并说清原因。"""
    stale = _job(db, owner, ds, status=JOB_RUNNING, age_seconds=3600 * 24)

    n = query_service.reclaim_stale_jobs()

    assert n >= 1
    db.expire_all()
    got = db.get(QueryJob, stale.id)
    assert got.status == JOB_FAILED
    assert "重启" in got.error or "中断" in got.error, f"要说清是怎么回事:{got.error}"


def test_reclaim_notifies_the_requester(db, owner, ds):
    """发起人必须收到通知 —— 不通知的话他只会一直等着,而那条记录已经不会再动了。"""
    stale = _job(db, owner, ds, status=JOB_RUNNING, age_seconds=3600 * 24)
    before = db.scalar(select(func.count()).select_from(Notification)) or 0

    query_service.reclaim_stale_jobs()

    db.expire_all()
    after = db.scalar(select(func.count()).select_from(Notification)) or 0
    assert after > before, "收回一条卡死的取数却不告诉发起人,等于把他继续晾着"
    note = db.scalars(
        select(Notification).where(Notification.job_id == stale.id).order_by(Notification.id.desc())
    ).first()
    assert note is not None and note.level == "error"


def test_does_not_touch_a_job_that_may_still_be_running(db, owner, ds):
    """刚起步的 running 不许动:回收的判据必须盖得住最长的合法超时。"""
    fresh = _job(db, owner, ds, status=JOB_RUNNING, age_seconds=5)
    query_service.reclaim_stale_jobs()
    db.expire_all()
    assert db.get(QueryJob, fresh.id).status == JOB_RUNNING


def test_reclaim_window_covers_the_longest_configured_task_timeout(db, owner, ds):
    """某个任务把超时配到 6 小时时,回收窗口要跟着放宽,不能拿全局默认一刀切。"""
    from app.models.template import SqlTemplate

    long = db.scalar(select(func.max(SqlTemplate.timeout_seconds)))
    assert query_service.stale_after_seconds(db) >= (long or 0), (
        "回收窗口必须盖得住库里最长的任务超时,否则会把还在正常跑的任务判死"
    )


def test_queued_jobs_are_left_alone(db, owner, ds):
    """排队中的不是孤儿,新 worker 起来照样能认领它。"""
    q = _job(db, owner, ds, status=JOB_QUEUED, age_seconds=3600 * 24)
    query_service.reclaim_stale_jobs()
    db.expire_all()
    assert db.get(QueryJob, q.id).status == JOB_QUEUED


# ---------------------------------------------------------------- F1b 并发执行


class _Gate:
    """让 execute_job 卡在一个闸门上,好观察「同时有几个在跑」。"""

    def __init__(self):
        self.entered = threading.Semaphore(0)
        self.release = threading.Event()
        self.concurrent = 0
        self.peak = 0
        self._lock = threading.Lock()

    def execute_job(self, job_id, ip=None):
        with self._lock:
            self.concurrent += 1
            self.peak = max(self.peak, self.concurrent)
        self.entered.release()
        self.release.wait(timeout=5)
        with self._lock:
            self.concurrent -= 1


def _drive(db, owner, ds, monkeypatch, *, concurrency, jobs):
    gate = _Gate()
    # worker 是 `from app.services import query_service`,两个名字指向同一个模块对象,打一处即可
    monkeypatch.setattr(worker.query_service, "execute_job", gate.execute_job)
    for _ in range(jobs):
        _job(db, owner, ds, status=JOB_QUEUED)

    pool = worker.JobPool(concurrency)
    try:
        for _ in range(jobs):
            worker.claim_and_submit(pool)
        deadline = time.time() + 2
        while gate.peak < min(concurrency, jobs) and time.time() < deadline:
            time.sleep(0.01)
        peak = gate.peak
    finally:
        gate.release.set()
        pool.shutdown(wait=True)
    return peak


def test_two_jobs_run_at_the_same_time(db, owner, ds, monkeypatch):
    """并发度 2 时,两个任务确实同时在跑 —— 而不是一个等另一个。"""
    assert _drive(db, owner, ds, monkeypatch, concurrency=2, jobs=2) == 2


def test_concurrency_one_still_serialises(db, owner, ds, monkeypatch):
    """并发度 1 = 旧行为,不许因为引入执行池就偷偷放开。"""
    assert _drive(db, owner, ds, monkeypatch, concurrency=1, jobs=2) == 1


def test_a_crashing_job_frees_its_slot(db, owner, ds, monkeypatch):
    """一个 job 抛异常不许把执行位漏掉,否则跑崩几次 worker 就彻底不干活了。"""
    def boom(job_id, ip=None):
        raise RuntimeError("炸了")

    monkeypatch.setattr(worker.query_service, "execute_job", boom)
    _job(db, owner, ds, status=JOB_QUEUED)
    pool = worker.JobPool(1)
    assert worker.claim_and_submit(pool) is True
    pool.shutdown(wait=True)
    assert pool.try_acquire(), "执行位没还回来"  # 公开接口:能再占到位 = 名额还回来了


# ---------------------------------------------------------------- 队列可见性


def test_queued_job_reports_how_many_are_ahead(db, owner, ds):
    """排队中的运行记录要能答出「前面还有几个」。

    没有这个数,前端就只能对排队和执行中说同一句「已提交,执行中…」——用户分不清系统在跑
    他的活,还是在等别人的活跑完,于是长等待读起来像卡死。
    """
    from app.api.routes.query import get_job

    first = _job(db, owner, ds, status=JOB_QUEUED)
    second = _job(db, owner, ds, status=JOB_QUEUED)

    # 断相对关系而不是绝对值:库不按用例清理,前面本来就可能躺着别的用例留下的排队记录
    a = get_job(first.id, db, owner).queue_ahead
    b = get_job(second.id, db, owner).queue_ahead
    assert a is not None and b == a + 1, f"后进队的该多排一个:{a} → {b}"


def test_running_job_reports_no_queue_position(db, owner, ds):
    """已经在跑的不该再报位次 —— 报了会让人以为还在排队。"""
    from app.api.routes.query import get_job

    r = _job(db, owner, ds, status=JOB_RUNNING)
    assert get_job(r.id, db, owner).queue_ahead is None

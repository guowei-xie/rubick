"""实时负载板(/analytics/live)的口径。

钉住的是几种「看起来正常、其实在说假话」的错法:
  · 试跑混进槽位占用 —— 它跑在 API 进程里,不占 worker 槽位,算进去会凭空显示满载;
  · worker 崩了之后遗留的 running 行被当成占槽 —— 最该报「停摆」的时候报了个「忙」;
  · 补推 / 复用记录算进提交量 —— 它们从没进过队列;
  · 团队管理员拿到全平台的在跑明细 —— 那是一条越权侧门。
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import update

from app.api.routes.analytics import analytics_live
from app.core.config import settings
from app.core.exceptions import PermissionDeniedError
from app.models.query_job import (
    JOB_QUEUED, JOB_RUNNING, JOB_SUCCESS, SOURCE_API, SOURCE_RUN, SOURCE_TEST, QueryJob,
)
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER
from app.services import analytics_service

from tests.conftest import db_now

pytestmark = pytest.mark.usefixtures("clean_jobs")

# ID 段 9380–9383
PLAT, A_ADMIN, DEV, BIZ = 9380, 9381, 9382, 9383


@pytest.fixture(autouse=True)
def _worker_mode(monkeypatch):
    """按「独立 worker、2 个槽位」来测 —— 本机 config 可能开着 RUN_INLINE。"""
    monkeypatch.setattr(settings, "RUN_INLINE", False)
    monkeypatch.setattr(settings, "WORKER_CONCURRENCY", 2)
    monkeypatch.setattr(settings, "WORKER_POLL_INTERVAL", 2.0)


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("alv-mysql")


@pytest.fixture
def plat(user_factory):
    return user_factory(PLAT, ROLE_ADMIN, "平台管理员", prefix="alv")


@pytest.fixture
def a_admin(user_factory):
    return user_factory(A_ADMIN, ROLE_DEVELOPER, "甲队团队管理员", prefix="alv")


@pytest.fixture
def dev(user_factory):
    return user_factory(DEV, ROLE_DEVELOPER, "开发者", prefix="alv")


@pytest.fixture
def biz(user_factory):
    return user_factory(BIZ, ROLE_USER, "业务使用者", prefix="alv")


@pytest.fixture
def team_a(db, a_admin, dev, team_factory):
    return team_factory("alv-team-A", [(a_admin, True), (dev, False)])


@pytest.fixture
def ta(db, dev, ds, team_a, template_factory):
    return template_factory(dev, ds, team_a, "alv-甲队任务")


def _live(db, user):
    return analytics_service.live(db, analytics_service.resolve_scope(db, user))


def _job(job_factory, ta, ds, biz, *, status, source=SOURCE_RUN, created_ago=0,
         started_ago=None, duration_ms=None, db=None):
    now = db_now(db)
    return job_factory(
        user=biz, template=ta, datasource=ds, status=status, source=source,
        created_at=now - timedelta(seconds=created_ago),
        started_at=None if started_ago is None else now - timedelta(seconds=started_ago),
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------- 权限


def test_team_admin_is_refused(db, a_admin, team_a):
    """团队管理员看不到实时负载:槽位是全平台共用的,列全就是越权,只列本队又自相矛盾。"""
    with pytest.raises(PermissionDeniedError):
        analytics_live(db=db, user=a_admin)


def test_team_scope_is_refused_even_for_platform_admin(db, plat, team_a):
    """平台管理员下钻到某个队(团队视角)也不给 —— 服务层自己守,不靠路由不收 team_id。"""
    scope = analytics_service.resolve_scope(db, plat, team_a.id)
    with pytest.raises(PermissionDeniedError):
        analytics_service.live(db, scope)


# ---------------------------------------------------------------- 此刻


def test_idle_platform(db, plat):
    got = analytics_live(db=db, user=plat)
    assert got["worker_state"] == analytics_service.WORKER_IDLE
    assert (got["running"], got["queued"], got["capacity"]) == (0, 0, 2)
    assert got["oldest_wait_s"] is None
    assert got["running_list"] == [] and got["queued_list"] == []


def test_test_runs_do_not_occupy_worker_slots(db, plat, biz, ds, ta, job_factory):
    """试跑在 API 进程里同步执行 —— 单独计数,不进槽位,也不进在跑明细。"""
    _job(job_factory, ta, ds, biz, status=JOB_RUNNING, source=SOURCE_TEST,
         created_ago=5, started_ago=5, db=db)
    got = _live(db, plat)
    assert got["test_running"] == 1
    assert got["running"] == 0
    assert got["running_list"] == []


def test_busy_when_slots_full_and_queue_waiting(db, plat, biz, ds, ta, job_factory):
    for _ in range(2):
        _job(job_factory, ta, ds, biz, status=JOB_RUNNING, created_ago=30, started_ago=20, db=db)
    _job(job_factory, ta, ds, biz, status=JOB_QUEUED, created_ago=300, db=db)

    got = _live(db, plat)
    assert got["worker_state"] == analytics_service.WORKER_BUSY
    assert got["running"] == 2 and got["queued"] == 1
    assert 290 <= got["oldest_wait_s"] <= 400
    row = got["running_list"][0]
    assert row["template_name"] == "alv-甲队任务"
    assert row["user_name"] == "业务使用者"
    assert row["timeout_s"] == settings.QUERY_TIMEOUT_SECONDS
    assert 15 <= row["elapsed_s"] <= 120 and row["overdue"] is False


def test_ok_when_queue_is_fresh(db, plat, biz, ds, ta, job_factory):
    """刚入队几秒没人领是常态(worker 按轮询间隔认领),不能报停摆。"""
    _job(job_factory, ta, ds, biz, status=JOB_QUEUED, created_ago=3, db=db)
    assert _live(db, plat)["worker_state"] == analytics_service.WORKER_OK


def test_stalled_when_slots_free_but_queue_old(db, plat, biz, ds, ta, job_factory):
    _job(job_factory, ta, ds, biz, status=JOB_QUEUED, created_ago=600, db=db)
    assert _live(db, plat)["worker_state"] == analytics_service.WORKER_STALLED


def test_overdue_running_rows_do_not_count_as_busy(db, plat, biz, ds, ta, job_factory):
    """worker 崩掉后遗留的 running 行要等孤儿回收才转 failed。它们超时仍挂着,
    按行数算槽位会显示「满载」—— 而真相是 worker 死了、该报停摆。"""
    dead = settings.QUERY_TIMEOUT_SECONDS + analytics_service.OVERDUE_GRACE_SECONDS + 300
    for _ in range(2):
        _job(job_factory, ta, ds, biz, status=JOB_RUNNING,
             created_ago=dead + 10, started_ago=dead, db=db)
    _job(job_factory, ta, ds, biz, status=JOB_QUEUED, created_ago=600, db=db)

    got = _live(db, plat)
    assert got["overdue"] == 2
    assert all(r["overdue"] for r in got["running_list"])
    assert got["worker_state"] == analytics_service.WORKER_STALLED


def test_inline_mode_has_no_worker_to_judge(db, plat, biz, ds, ta, job_factory, monkeypatch):
    monkeypatch.setattr(settings, "RUN_INLINE", True)
    _job(job_factory, ta, ds, biz, status=JOB_QUEUED, created_ago=600, db=db)
    assert _live(db, plat)["worker_state"] == analytics_service.WORKER_INLINE


def test_queued_list_follows_claim_order(db, plat, biz, ds, ta, job_factory):
    """排队明细按 worker 真实的认领顺序(非 API 优先),位次才对得上「接下来轮到谁」。"""
    api_first = _job(job_factory, ta, ds, biz, status=JOB_QUEUED, source=SOURCE_API,
                     created_ago=60, db=db)
    web_later = _job(job_factory, ta, ds, biz, status=JOB_QUEUED, created_ago=10, db=db)

    got = _live(db, plat)
    assert [r["job_id"] for r in got["queued_list"]] == [web_later.id, api_first.id]
    assert [r["position"] for r in got["queued_list"]] == [1, 2]


# ---------------------------------------------------------------- 今天逐小时


def test_today_counts_only_jobs_that_went_through_the_queue(db, plat, biz, ds, ta, job_factory):
    """提交量排除试跑、补推、复用 —— 这三种都没进过 worker 队列。"""
    ok = _job(job_factory, ta, ds, biz, status=JOB_SUCCESS, created_ago=1,
              started_ago=1, duration_ms=500, db=db)
    _job(job_factory, ta, ds, biz, status=JOB_SUCCESS, source=SOURCE_TEST, created_ago=1, db=db)
    pushed = _job(job_factory, ta, ds, biz, status=JOB_SUCCESS, created_ago=1, db=db)
    reused = _job(job_factory, ta, ds, biz, status=JOB_SUCCESS, created_ago=1, db=db)
    db.execute(update(QueryJob).where(QueryJob.id == pushed.id).values(pushed_from_job_id=ok.id))
    db.execute(update(QueryJob).where(QueryJob.id == reused.id).values(reused_from_job_id=ok.id))
    db.commit()

    got = _live(db, plat)
    assert sum(h["submitted"] for h in got["today"]) == 1
    assert len(got["today"]) == db_now(db).hour + 1


def test_running_job_counts_toward_current_hour_peak(db, plat, biz, ds, ta, job_factory):
    _job(job_factory, ta, ds, biz, status=JOB_RUNNING, created_ago=1, started_ago=1, db=db)
    got = _live(db, plat)
    assert got["today"][-1]["peak_concurrency"] == 1


# ---------------------------------------------------------------- 未来 24 小时


def test_schedule_forecast_counts_only_runs_that_will_enqueue(
    db, plat, dev, biz, ds, team_a, subscribed_task_factory, team_credential
):
    """只有「开着、已上线、有订阅者」的计划才会真的入队 —— 与调度器 _tick 同一筛选。"""
    team_credential(team_a, ds)
    at = (datetime.now() + timedelta(hours=2)).strftime("%H:%M")
    subscribed_task_factory(dev, ds, team_a, "alv-有人订", at_time=at, subscribers=[biz])
    subscribed_task_factory(dev, ds, team_a, "alv-没人订", at_time=at)
    subscribed_task_factory(dev, ds, team_a, "alv-没上线", at_time=at, publish=False,
                            subscribers=[biz])

    got = _live(db, plat)
    assert len(got["schedule_24h"]) == 25
    names = [t["name"] for b in got["schedule_24h"] for t in b["tasks"]]
    assert names == ["alv-有人订"]
    assert sum(b["count"] for b in got["schedule_24h"]) == 1
    assert not any(b["crowded"] for b in got["schedule_24h"])

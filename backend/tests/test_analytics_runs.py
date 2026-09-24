"""运行明细板(/analytics/runs)的口径。

钉住的是运维拿它排障时最容易被误导的几处:
  · 团队管理员看得到别队的运行 —— 越权侧门;
  · 失败的那次没有耗时 → 按耗时排序时超时失败全沉底,最该先看的反而看不到;
  · 状态芯片的计数带着状态筛选本身 → 选了「失败」后其余芯片全变 0;
  · 「只看真正执行的」没排掉补推 / 复用 → 与运行健康板的数对不上;
  · 详情接口对范围外的 id 给出与「不存在」不同的回应 → 能挨个试出别队的运行量。
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import update

from app.api.routes.analytics import analytics_run_detail, analytics_runs
from app.core.exceptions import NotFoundError, PermissionDeniedError, RubicError
from app.core.timewindow import Window
from app.models.query_job import (
    JOB_FAILED, JOB_QUEUED, JOB_RUNNING, JOB_SUCCESS,
    SOURCE_API, SOURCE_SUBSCRIBE, QueryJob,
)
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER
from app.services import analytics_service
from app.services.analytics_service import RunFilters

pytestmark = pytest.mark.usefixtures("clean_jobs")

# ID 段 9395–9399
PLAT, A_ADMIN, B_ADMIN, DEV, BIZ = 9395, 9396, 9397, 9398, 9399

NOW = datetime(2026, 6, 15, 12, 0)
WINDOW = Window(NOW - timedelta(days=30), NOW)
T0 = NOW - timedelta(days=2)


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("aru-mysql")


@pytest.fixture
def plat(user_factory):
    return user_factory(PLAT, ROLE_ADMIN, "平台管理员", prefix="aru")


@pytest.fixture
def a_admin(user_factory):
    return user_factory(A_ADMIN, ROLE_DEVELOPER, "甲队团队管理员", prefix="aru")


@pytest.fixture
def b_admin(user_factory):
    return user_factory(B_ADMIN, ROLE_DEVELOPER, "乙队团队管理员", prefix="aru")


@pytest.fixture
def dev(user_factory):
    return user_factory(DEV, ROLE_DEVELOPER, "开发者", prefix="aru")


@pytest.fixture
def biz(user_factory):
    return user_factory(BIZ, ROLE_USER, "业务使用者", prefix="aru")


@pytest.fixture
def team_a(db, a_admin, dev, team_factory):
    return team_factory("aru-team-A", [(a_admin, True), (dev, False)])


@pytest.fixture
def team_b(db, b_admin, team_factory):
    return team_factory("aru-team-B", [(b_admin, True)])


@pytest.fixture
def ta(dev, ds, team_a, template_factory):
    return template_factory(dev, ds, team_a, "aru-甲队任务")


@pytest.fixture
def tb(dev, ds, team_b, template_factory):
    return template_factory(dev, ds, team_b, "aru-乙队任务")


def _set(db, job, **values):
    """job_factory 不收的列(updated_at / 补推 / 复用)直接 UPDATE —— updated_at 有 onupdate。"""
    db.execute(update(QueryJob).where(QueryJob.id == job.id).values(**values))
    db.commit()
    db.refresh(job)
    return job


@pytest.fixture
def jobs(db, job_factory, ta, tb, ds, biz):
    """甲队:一次成功(执行 5 秒、排队 90 秒)、一次超时失败(跑了 120 秒才失败)、
    一次复用、一次补推;乙队:一次缺账号失败、一次 API 排队中。"""
    mk = lambda t, **kw: job_factory(user=biz, template=t, datasource=ds, **kw)  # noqa: E731
    ok = mk(ta, status=JOB_SUCCESS, created_at=T0, started_at=T0 + timedelta(seconds=90),
            duration_ms=5_000, row_count=10)
    slow = mk(ta, status=JOB_FAILED, created_at=T0 + timedelta(minutes=1),
              started_at=T0 + timedelta(minutes=1, seconds=2),
              error="Query execution was interrupted, maximum statement execution time exceeded")
    _set(db, slow, updated_at=T0 + timedelta(minutes=3, seconds=2))
    reused = mk(ta, status=JOB_SUCCESS, created_at=T0 + timedelta(minutes=2), row_count=10)
    _set(db, reused, reused_from_job_id=ok.id, reused_from_at=T0)
    pushed = mk(ta, status=JOB_SUCCESS, source=SOURCE_SUBSCRIBE,
                created_at=T0 + timedelta(minutes=3), row_count=10)
    _set(db, pushed, pushed_from_job_id=ok.id)
    cred = mk(tb, status=JOB_FAILED, created_at=T0 + timedelta(minutes=4),
              error="团队尚未配置取数账号")
    queued = mk(tb, status=JOB_QUEUED, source=SOURCE_API, created_at=T0 + timedelta(minutes=5))
    return dict(ok=ok, slow=slow, reused=reused, pushed=pushed, cred=cred, queued=queued)


def _runs(db, user, filters=RunFilters(), team_id=None, **kw):
    scope = analytics_service.resolve_scope(db, user, team_id)
    return analytics_service.runs(db, scope, WINDOW, filters, **kw)


def _ids(res):
    return [r["job_id"] for r in res["items"]]


# ---------------------------------------------------------------- 权限与收窄


def test_plain_user_is_refused(db, biz):
    with pytest.raises(PermissionDeniedError):
        analytics_runs(db=db, user=biz)


def test_team_admin_sees_only_own_team(db, a_admin, team_b, jobs):
    mine = _ids(_runs(db, a_admin))
    assert set(mine) == {jobs[k].id for k in ("ok", "slow", "reused", "pushed")}
    with pytest.raises(PermissionDeniedError):
        _runs(db, a_admin, team_id=team_b.id)


def test_platform_sees_all_and_can_drill_into_team(db, plat, jobs, team_b):
    assert len(_runs(db, plat)["items"]) == 6
    assert set(_ids(_runs(db, plat, team_id=team_b.id))) == {jobs["cred"].id, jobs["queued"].id}


def test_newest_first_by_default(db, plat, jobs):
    assert _ids(_runs(db, plat))[0] == jobs["queued"].id


# ---------------------------------------------------------------- 筛选


def test_status_counts_ignore_the_status_filter_itself(db, plat, jobs):
    """选了「失败」后,「成功」芯片上仍要显示有几条 —— 否则没法切过去比。"""
    res = _runs(db, plat, RunFilters(status=(JOB_FAILED,)))
    assert set(_ids(res)) == {jobs["slow"].id, jobs["cred"].id}
    assert res["total"] == 2
    assert res["status_counts"][JOB_SUCCESS] == 3
    assert res["status_counts"][JOB_QUEUED] == 1


def test_source_filter(db, plat, jobs):
    assert _ids(_runs(db, plat, RunFilters(source=(SOURCE_API,)))) == [jobs["queued"].id]


def test_executed_only_drops_reuse_and_push(db, plat, jobs):
    ids = set(_ids(_runs(db, plat, RunFilters(executed_only=True))))
    assert jobs["reused"].id not in ids and jobs["pushed"].id not in ids
    assert jobs["ok"].id in ids


def test_default_keeps_reuse_and_push_with_marks(db, plat, jobs):
    by_id = {r["job_id"]: r for r in _runs(db, plat)["items"]}
    assert by_id[jobs["reused"].id]["reused_from_job_id"] == jobs["ok"].id
    assert by_id[jobs["pushed"].id]["pushed_from_job_id"] == jobs["ok"].id


def test_failed_run_gets_approximate_duration_and_sorts_first(db, plat, jobs):
    """超时失败跑了 120 秒,比成功那次的 5 秒长 —— 按耗时排序它必须排第一。"""
    res = _runs(db, plat, sort="duration")
    top = res["items"][0]
    assert top["job_id"] == jobs["slow"].id
    assert top["duration_approx"] is True
    assert top["duration_ms"] == pytest.approx(120_000, abs=1_000)
    ok = next(r for r in res["items"] if r["job_id"] == jobs["ok"].id)
    assert ok["duration_ms"] == 5_000 and ok["duration_approx"] is False


def test_min_duration_filter_uses_approximation(db, plat, jobs):
    assert _ids(_runs(db, plat, RunFilters(min_duration_s=60))) == [jobs["slow"].id]


def test_queue_sort_and_filter(db, plat, jobs):
    assert _runs(db, plat, sort="queue")["items"][0]["job_id"] == jobs["ok"].id
    assert _ids(_runs(db, plat, RunFilters(min_queue_s=60))) == [jobs["ok"].id]


def test_error_keyword_is_case_insensitive(db, plat, jobs):
    assert _ids(_runs(db, plat, RunFilters(error_kw="QUERY EXECUTION"))) == [jobs["slow"].id]


def test_bucket_filter_matches_health_board(db, plat, jobs):
    """按归因筛与运行健康板用同一套规则:MySQL 的超时那句不含 timeout 字样,也要归进超时。"""
    res = _runs(db, plat, RunFilters(bucket="timeout"))
    assert _ids(res) == [jobs["slow"].id]
    assert res["items"][0]["error_label"] == "查询超时"
    assert _ids(_runs(db, plat, RunFilters(bucket="credential"))) == [jobs["cred"].id]


def test_template_and_user_keyword(db, plat, jobs):
    assert set(_ids(_runs(db, plat, RunFilters(template_kw="乙队")))) == {
        jobs["cred"].id, jobs["queued"].id}
    assert len(_ids(_runs(db, plat, RunFilters(user_kw="业务")))) == 6
    assert _ids(_runs(db, plat, RunFilters(user_kw="没这个人"))) == []


def test_unknown_values_are_rejected(db, plat, jobs):
    for f in (RunFilters(status=("done",)), RunFilters(source=("cron",)), RunFilters(bucket="x")):
        with pytest.raises(RubicError):
            _runs(db, plat, f)
    with pytest.raises(RubicError):
        _runs(db, plat, sort="rows")


def test_pagination(db, plat, jobs):
    p1 = _runs(db, plat, page=1, page_size=4)
    p2 = _runs(db, plat, page=2, page_size=4)
    assert p1["total"] == p2["total"] == 6
    assert len(p1["items"]) == 4 and len(p2["items"]) == 2
    assert not set(_ids(p1)) & set(_ids(p2))
    assert _runs(db, plat, page_size=10_000)["page_size"] == analytics_service.RUNS_PAGE_MAX


def test_running_row_reports_elapsed(db, plat, job_factory, ta, ds, biz):
    from tests.conftest import db_now

    now = db_now(db)
    job_factory(user=biz, template=ta, datasource=ds, status=JOB_RUNNING,
                created_at=now - timedelta(seconds=50), started_at=now - timedelta(seconds=30))
    scope = analytics_service.resolve_scope(db, plat)
    win = Window(now - timedelta(days=1), now + timedelta(days=1))
    row = analytics_service.runs(db, scope, win)["items"][0]
    assert 25 <= row["elapsed_s"] <= 60


def test_list_carries_only_error_excerpt(db, plat, job_factory, ta, ds, biz):
    job_factory(user=biz, template=ta, datasource=ds, status=JOB_FAILED, created_at=T0,
                error="x" * 5_000)
    row = _runs(db, plat)["items"][0]
    assert len(row["error_excerpt"]) == analytics_service.RUNS_ERROR_CHARS
    assert "executed_sql" not in row


# ---------------------------------------------------------------- 详情


def test_detail_returns_full_error_and_sql(db, plat, jobs):
    d = analytics_run_detail(job_id=jobs["slow"].id, db=db, user=plat)
    assert d["error"].startswith("Query execution was interrupted")
    assert d["error_label"] == "查询超时"
    assert d["duration_approx"] is True
    assert d["duration_ms"] == pytest.approx(120_000, abs=1_000)
    assert "params" in d and "executed_sql" in d


def test_detail_out_of_scope_is_404(db, a_admin, jobs):
    with pytest.raises(NotFoundError):
        analytics_run_detail(job_id=jobs["cred"].id, db=db, user=a_admin)
    with pytest.raises(NotFoundError):
        analytics_run_detail(job_id=10_000_000, db=db, user=a_admin)
    assert analytics_run_detail(job_id=jobs["ok"].id, db=db, user=a_admin)["job_id"] == jobs["ok"].id


def test_route_wires_filters(db, plat, jobs):
    from app.api.routes.analytics import run_filters

    f = run_filters(status=["failed", "queued"], source=["run"], datasource_id=None,
                    template_id=None, template_kw="乙队", user_kw=None, min_duration_s=None,
                    min_queue_s=None, error_kw=None, bucket=None, executed_only=True)
    assert f.status == ("failed", "queued") and f.executed_only and f.template_kw == "乙队"
    res = analytics_runs(db=db, user=plat, start=WINDOW.start, end=WINDOW.end, days=None,
                         team_id=None, filters=RunFilters(status=(JOB_FAILED,)),
                         sort="created", page=1, page_size=20)
    assert set(_ids(res)) == {jobs["slow"].id, jobs["cred"].id}

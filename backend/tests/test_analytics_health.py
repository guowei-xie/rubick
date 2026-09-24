"""板块②「运行健康」的口径。

这里有全案最要紧的一条裁决(见 analytics_service.job_conditions):团队收窄挂
**sql_templates.team_id**,不挂 query_jobs.run_as_team_id。test_failure_before_identity_
resolution_still_counts 与 test_team_transfer_follows_the_task 两条就是它的实测 ——
挂错了,失败率会被系统性高估,而偏差永远朝着「看起来很健康」的方向,没人会去查。

其余几条钉的是同类的「悄悄变好看」的错法:把 queued 算进成功率分母、把试跑混进总成功率、
把没有 started_at 的历史行当成零排队。
"""
from datetime import datetime, timedelta

import pytest

from app.api.routes.analytics import analytics_health
from app.core.timewindow import Window
from app.models.query_job import (
    JOB_FAILED, JOB_QUEUED, JOB_RUNNING, JOB_SUCCESS,
    SOURCE_RUN, SOURCE_TEST,
)
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER
from app.services import analytics_service

pytestmark = pytest.mark.usefixtures("clean_jobs")

# ID 段 9330–9334
PLAT, A_ADMIN, B_ADMIN, DEV, BIZ = 9330, 9331, 9332, 9333, 9334

NOW = datetime(2026, 6, 15, 12, 0)
WINDOW = Window(NOW - timedelta(days=30), NOW)


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("ahe-mysql")


@pytest.fixture
def hive_ds(datasource_factory):
    return datasource_factory("ahe-hive", engine="hive", port=10000)


@pytest.fixture
def plat(user_factory):
    return user_factory(PLAT, ROLE_ADMIN, "平台管理员", prefix="ahe")


@pytest.fixture
def a_admin(user_factory):
    return user_factory(A_ADMIN, ROLE_DEVELOPER, "甲队团队管理员", prefix="ahe")


@pytest.fixture
def b_admin(user_factory):
    return user_factory(B_ADMIN, ROLE_DEVELOPER, "乙队团队管理员", prefix="ahe")


@pytest.fixture
def dev(user_factory):
    return user_factory(DEV, ROLE_DEVELOPER, "开发者", prefix="ahe")


@pytest.fixture
def biz(user_factory):
    return user_factory(BIZ, ROLE_USER, "业务使用者", prefix="ahe")


@pytest.fixture
def team_a(db, a_admin, dev, team_factory):
    return team_factory("ahe-team-A", [(a_admin, True), (dev, False)])


@pytest.fixture
def team_b(db, b_admin, team_factory):
    return team_factory("ahe-team-B", [(b_admin, True)])




@pytest.fixture
def ta(db, dev, ds, team_a, template_factory):
    return template_factory(dev, ds, team_a, "ahe-甲队任务")


def _platform(db, plat):
    return analytics_service.resolve_scope(db, plat)


def _team(db, admin):
    return analytics_service.resolve_scope(db, admin)


# ---------------------------------------------------------------- 团队收窄的那条裁决


def test_failure_before_identity_resolution_still_counts(
    db, a_admin, biz, ds, ta, job_factory
):
    """参数校验/SQL 网关拦下的失败,run_as_team_id 恒为 NULL —— 它必须仍然算进本团队的失败率。

    这就是收窄挂 sql_templates.team_id 而不挂 run_as_team_id 的全部理由。挂错了,
    这一类失败会整个从分母里消失,成功率被系统性高估 —— 而它偏偏是最该被看见的一类
    (业务方填错参数、SQL 写了写操作),也是最容易复现的一类。
    """
    job_factory(user=biz, template=ta, datasource=ds, source=SOURCE_RUN,
                status=JOB_SUCCESS, run_as_team_id=ta.team_id,
                created_at=NOW - timedelta(days=1))
    # 身份还没解析就失败了:run_as_team_id 留空
    job_factory(user=biz, template=ta, datasource=ds, source=SOURCE_RUN,
                status=JOB_FAILED, error="缺少必填参数:统计日期", run_as_team_id=None,
                created_at=NOW - timedelta(days=1))

    got = analytics_service.health(db, _team(db, a_admin), WINDOW)

    assert got["by_source"][SOURCE_RUN]["total"] == 2, "身份解析前失败的那次被漏掉了"
    assert got["by_source"][SOURCE_RUN]["failed"] == 1
    assert got["by_source"][SOURCE_RUN]["success_rate"] == pytest.approx(0.5)


def test_team_transfer_follows_the_task_not_the_old_credential(
    db, a_admin, b_admin, biz, ds, team_a, team_b, dev, job_factory
, template_factory):
    """任务转移团队后,历史运行归**新**团队 —— 「本团队的资产现在表现如何」。

    历史行的 run_as_team_id 还留着旧团队,按它算的话这些运行会永远挂在旧队名下,
    新队接手了资产却看不到它的历史。哪个团队该为这张任务负责,看的是它现在归谁。
    """
    t = template_factory(dev, ds, team_a, "ahe-被转移的任务")
    job_factory(user=biz, template=t, datasource=ds, source=SOURCE_RUN,
                status=JOB_FAILED, error="timeout", run_as_team_id=team_a.id,
                created_at=NOW - timedelta(days=2))

    # 转给乙队
    t.team_id = team_b.id
    db.commit()

    assert analytics_service.health(db, _team(db, b_admin), WINDOW)["by_source"][SOURCE_RUN]["total"] == 1
    assert analytics_service.health(db, _team(db, a_admin), WINDOW)["by_source"][SOURCE_RUN]["total"] == 0


# ---------------------------------------------------------------- 成功率的分母


def test_queued_and_running_are_not_in_the_denominator(db, plat, biz, ds, ta, job_factory):
    """还没有结论的运行不进分母 —— 否则刚入队的一批会把成功率生生拉低。"""
    job_factory(user=biz, template=ta, datasource=ds, status=JOB_SUCCESS,
                created_at=NOW - timedelta(days=1))
    job_factory(user=biz, template=ta, datasource=ds, status=JOB_QUEUED,
                created_at=NOW - timedelta(days=1))
    job_factory(user=biz, template=ta, datasource=ds, status=JOB_RUNNING,
                created_at=NOW - timedelta(days=1))

    got = analytics_service.health(db, _platform(db, plat), WINDOW)
    assert got["by_source"][SOURCE_RUN]["total"] == 1
    assert got["by_source"][SOURCE_RUN]["success_rate"] == 1.0
    # 但它们要出现在「此刻队列」里 —— 那是个不吃时间窗的快照
    assert got["in_flight"] == {"queued": 1, "running": 1}


def test_success_rate_is_split_by_source(db, plat, dev, biz, ds, ta, job_factory):
    """试跑失败是正常的研发过程,定时失败才是事故 —— 两者混算会让平台看起来一团糟,
    然后所有人学会忽略这个数字。"""
    job_factory(user=biz, template=ta, datasource=ds, source=SOURCE_RUN,
                status=JOB_SUCCESS, created_at=NOW - timedelta(days=1))
    for _ in range(3):
        job_factory(user=dev, template=ta, datasource=ds, source=SOURCE_TEST,
                    status=JOB_FAILED, error="syntax error", created_at=NOW - timedelta(days=1))

    got = analytics_service.health(db, _platform(db, plat), WINDOW)
    assert got["by_source"][SOURCE_RUN]["success_rate"] == 1.0
    assert got["by_source"][SOURCE_TEST]["success_rate"] == 0.0
    # **不提供**合并的总成功率:一个 25% 会让人以为平台一团糟,而真相是业务取数全成功、
    # 开发者在编辑器里试错了三次。给了它就一定有人只看它
    assert "overall" not in got
    # 顶部卡片拿的是正式取数这一路,且是包好的信封
    assert got["run_success_rate"] == {
        "value": 1.0, "has_data": True, "windowed": True,
        "last_event_at": None, "prev_value": None,
    }


def test_success_rate_is_none_not_zero_without_runs(db, a_admin, team_a):
    """0 次取数的成功率不是 0%,是没得算。"""
    got = analytics_service.health(db, _team(db, a_admin), WINDOW)
    assert got["by_source"][SOURCE_RUN]["success_rate"] is None
    # 卡片同样是 None 而不是 0,且 has_data=False —— 前端据此渲染灰色的「—」而不是绿色的 0
    assert got["run_success_rate"]["value"] is None
    assert got["run_success_rate"]["has_data"] is False


# ---------------------------------------------------------------- 排队


def test_queue_ignores_rows_without_a_stamp(db, plat, biz, ds, ta, job_factory):
    """没有 started_at 的历史行不参与,也不被当成「零等待」。"""
    base = NOW - timedelta(days=1)
    # 有记录:排了 2 分钟
    job_factory(user=biz, template=ta, datasource=ds, status=JOB_SUCCESS,
                created_at=base, started_at=base + timedelta(seconds=120))
    # 有记录:排了 10 秒,不到一分钟
    job_factory(user=biz, template=ta, datasource=ds, status=JOB_SUCCESS,
                created_at=base, started_at=base + timedelta(seconds=10))
    # 历史行:没有记录
    job_factory(user=biz, template=ta, datasource=ds, status=JOB_SUCCESS, created_at=base)

    got = analytics_service.health(db, _platform(db, plat), WINDOW)
    assert got["queue_over_60s"]["value"] == 1


def test_queue_excludes_test_runs(db, plat, dev, biz, ds, ta, job_factory):
    """试跑同步执行、不入队 —— 它的「排队」不算数。"""
    base = NOW - timedelta(days=1)
    job_factory(user=dev, template=ta, datasource=ds, source=SOURCE_TEST,
                status=JOB_SUCCESS, created_at=base, started_at=base + timedelta(seconds=300))

    got = analytics_service.health(db, _platform(db, plat), WINDOW)
    assert got["queue_over_60s"]["value"] is None, "试跑被当成排队样本了"


def test_queue_is_none_when_no_samples(db, a_admin, team_a):
    """一条带开始时刻的样本都没有时是「没得算」,不是「零次超时」。"""
    got = analytics_service.health(db, _team(db, a_admin), WINDOW)
    assert got["queue_over_60s"]["value"] is None


# ---------------------------------------------------------------- 失败归因与耗时


def test_failure_buckets_are_labelled_and_sorted(db, plat, biz, ds, ta, job_factory):
    base = NOW - timedelta(days=1)
    for err in ("Hive 查询超时(>3600s)已被终止", "query timed out", "缺少必填参数:日期"):
        job_factory(user=biz, template=ta, datasource=ds, status=JOB_FAILED,
                    error=err, created_at=base)

    buckets = analytics_service.health(db, _platform(db, plat), WINDOW)["failure_buckets"]
    assert buckets[0]["code"] == "timeout" and buckets[0]["count"] == 2
    assert all(b["label"] for b in buckets), "前端只消费中文标签,漏一个就会露出英文 code"


def test_unbucketed_samples_are_returned_so_the_rules_can_evolve(
    db, plat, biz, ds, ta, job_factory
):
    """other 桶要带样例回来 —— 否则它会永远是最大的桶,且没人知道该往里加什么规则。"""
    job_factory(user=biz, template=ta, datasource=ds, status=JOB_FAILED,
                error="谁也没见过的新错误", created_at=NOW - timedelta(days=1))

    got = analytics_service.health(db, _platform(db, plat), WINDOW)
    assert got["unbucketed_samples"] == ["谁也没见过的新错误"]


def test_duration_percentiles_are_split_by_engine(
    db, plat, biz, ds, hive_ds, ta, dev, team_a, job_factory
, template_factory):
    """Hive 超时上限 3600s、MySQL 120s —— 混在一起的分位数没有可比性。只给 P90。"""
    hive_tmpl = template_factory(dev, hive_ds, team_a, "ahe-hive 任务")
    hive_tmpl.datasource_id = hive_ds.id
    db.commit()
    base = NOW - timedelta(days=1)
    job_factory(user=biz, template=ta, datasource=ds, status=JOB_SUCCESS,
                duration_ms=1_000, created_at=base)
    job_factory(user=biz, template=hive_tmpl, datasource=hive_ds, status=JOB_SUCCESS,
                duration_ms=900_000, created_at=base)

    got = analytics_service.health(db, _platform(db, plat), WINDOW)["duration_by_engine"]
    assert got["mysql"] == {"samples": 1, "p90_ms": 1_000}
    assert got["hive"] == {"samples": 1, "p90_ms": 900_000}


def test_duration_only_counts_successful_runs(db, plat, biz, ds, ta, job_factory):
    """失败分支不写 duration_ms;真要有值也不该混进「跑得多快」里。"""
    base = NOW - timedelta(days=1)
    job_factory(user=biz, template=ta, datasource=ds, status=JOB_SUCCESS,
                duration_ms=5_000, created_at=base)
    job_factory(user=biz, template=ta, datasource=ds, status=JOB_FAILED,
                duration_ms=1, error="boom", created_at=base)

    got = analytics_service.health(db, _platform(db, plat), WINDOW)["duration_by_engine"]
    assert got["mysql"]["samples"] == 1
    assert got["mysql"]["p90_ms"] == 5_000


def test_zero_row_successes_are_surfaced(db, plat, biz, ds, ta, job_factory):
    """跑通了却没有数据 —— 多半是参数填错,零成本就能看见。"""
    job_factory(user=biz, template=ta, datasource=ds, status=JOB_SUCCESS,
                row_count=0, created_at=NOW - timedelta(days=1))
    job_factory(user=biz, template=ta, datasource=ds, status=JOB_SUCCESS,
                row_count=500, created_at=NOW - timedelta(days=1))

    got = analytics_service.health(db, _platform(db, plat), WINDOW)
    assert got["zero_row_jobs"]["value"] == 1


def test_by_datasource_ranks_by_volume(db, plat, biz, ds, hive_ds, ta, dev, team_a,
                                       job_factory, template_factory):
    hive_tmpl = template_factory(dev, hive_ds, team_a, "ahe-hive 任务2")
    base = NOW - timedelta(days=1)
    for _ in range(3):
        job_factory(user=biz, template=ta, datasource=ds, status=JOB_SUCCESS, created_at=base)
    job_factory(user=biz, template=hive_tmpl, datasource=hive_ds, status=JOB_FAILED,
                error="thrift", created_at=base)

    rows = analytics_service.health(db, _platform(db, plat), WINDOW)["by_datasource"]
    assert rows[0]["name"] == "ahe-mysql" and rows[0]["total"] == 3
    assert rows[1]["fail_rate"] == 1.0


# ---------------------------------------------------------------- 路由层


def test_route_scopes_to_team_for_team_admin(db, a_admin, team_a):
    got = analytics_health(db=db, user=a_admin)
    assert got["scope"]["level"] == "team"
    assert got["scope"]["team_id"] == team_a.id

"""板块①「采纳与活跃」的口径。

这块板要回答的是「**业务方**真的在自助取数吗」,所以最要紧的一条是活跃人数**不含试跑**
—— 否则开发者调试很勤就会被读成业务很活跃,而这块板恰恰是为了戳破这种假象而存在的。

其余几条都是「改了不报错、只是数字悄悄变了」的地方:系统账号有没有被排除、source 三分
有没有串台、半开区间的边界归谁、以及平台专属指标在团队视角下是不是**键都不存在**。
"""
from datetime import datetime, timedelta

import pytest

from app.api.routes.analytics import analytics_adoption
from app.core.timewindow import Window
from app.models.query_job import (
    SOURCE_RUN, SOURCE_SUBSCRIBE, SOURCE_TEST,
)
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER
from app.services import analytics_service

pytestmark = pytest.mark.usefixtures("clean_jobs")

# ID 段 9320–9325
PLAT, A_ADMIN, DEV, BIZ1, BIZ2, B_ADMIN = 9320, 9321, 9322, 9323, 9324, 9325

NOW = datetime(2026, 6, 15, 12, 0)
WINDOW = Window(NOW - timedelta(days=30), NOW)


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("aad-mysql")


@pytest.fixture
def plat(user_factory):
    return user_factory(PLAT, ROLE_ADMIN, "平台管理员", prefix="aad")


@pytest.fixture
def a_admin(user_factory):
    return user_factory(A_ADMIN, ROLE_DEVELOPER, "甲队团队管理员", prefix="aad")


@pytest.fixture
def b_admin(user_factory):
    return user_factory(B_ADMIN, ROLE_DEVELOPER, "乙队团队管理员", prefix="aad")


@pytest.fixture
def dev(user_factory):
    return user_factory(DEV, ROLE_DEVELOPER, "开发者", prefix="aad")


@pytest.fixture
def biz1(user_factory):
    return user_factory(BIZ1, ROLE_USER, "业务甲", prefix="aad")


@pytest.fixture
def biz2(user_factory):
    return user_factory(BIZ2, ROLE_USER, "业务乙", prefix="aad")


@pytest.fixture
def team_a(db, a_admin, dev, team_factory):
    return team_factory("aad-team-A", [(a_admin, True), (dev, False)])


@pytest.fixture
def team_b(db, b_admin, team_factory):
    return team_factory("aad-team-B", [(b_admin, True)])




@pytest.fixture
def ta(db, dev, ds, team_a, template_factory):
    return template_factory(dev, ds, team_a, "aad-甲队任务")


@pytest.fixture
def tb(db, b_admin, ds, team_b, template_factory):
    return template_factory(b_admin, ds, team_b, "aad-乙队任务")


def _platform(db, plat):
    return analytics_service.resolve_scope(db, plat)


def _team(db, admin):
    return analytics_service.resolve_scope(db, admin)


# ---------------------------------------------------------------- 活跃人数的口径


def test_test_runs_do_not_count_as_active_users(db, plat, dev, biz1, ds, ta, job_factory):
    """作者试跑再多也不算「活跃取数人」。

    这是这块板的立身之本:把试跑算进去,等于允许开发者自己刷高「业务在用」这个数字。
    开发侧活跃单列成 active_authors,不埋没谁的工作量。
    """
    for _ in range(5):
        job_factory(user=dev, template=ta, datasource=ds,
                    source=SOURCE_TEST, created_at=NOW - timedelta(days=1))
    job_factory(user=biz1, template=ta, datasource=ds,
                source=SOURCE_RUN, created_at=NOW - timedelta(days=1))

    got = analytics_service.adoption(db, _platform(db, plat), WINDOW)

    assert got["active_users"]["value"] == 1, "试跑被算进活跃取数人了"
    assert got["active_authors"]["value"] == 1
    assert got["test_jobs"]["value"] == 5
    assert got["run_jobs"]["value"] == 1


def test_system_scheduler_is_excluded_from_head_count(
    db, plat, biz1, ds, ta, job_factory, system_user
):
    """定时运行挂在系统账号名下 —— 它不是一个「人」,算进去会永远活跃。"""
    job_factory(user=system_user, template=ta, datasource=ds,
                source=SOURCE_SUBSCRIBE, created_at=NOW - timedelta(days=2))
    job_factory(user=biz1, template=ta, datasource=ds,
                source=SOURCE_RUN, created_at=NOW - timedelta(days=2))

    got = analytics_service.adoption(db, _platform(db, plat), WINDOW)

    assert got["active_users"]["value"] == 1
    assert got["scheduled_jobs"]["value"] == 1


def test_sources_do_not_bleed_into_each_other(db, plat, dev, biz1, ds, ta, job_factory,
                                              system_user):
    """三种来源的次数各算各的,**永不合并成一个「总运行次数」**。"""
    job_factory(user=biz1, template=ta, datasource=ds, source=SOURCE_RUN,
                created_at=NOW - timedelta(days=3))
    job_factory(user=dev, template=ta, datasource=ds, source=SOURCE_TEST,
                created_at=NOW - timedelta(days=3))
    job_factory(user=system_user, template=ta, datasource=ds, source=SOURCE_SUBSCRIBE,
                created_at=NOW - timedelta(days=3))

    got = analytics_service.adoption(db, _platform(db, plat), WINDOW)
    assert (got["run_jobs"]["value"], got["test_jobs"]["value"],
            got["scheduled_jobs"]["value"]) == (1, 1, 1)


# ---------------------------------------------------------------- 时间窗边界


def test_window_is_half_open(db, plat, biz1, ds, ta, job_factory):
    """[start, end):落在 start 上的算进来,落在 end 上的不算。

    没有这条,相邻两个窗口会把边界那一刻的运行各算一次,环比与留存就都偏了。
    """
    job_factory(user=biz1, template=ta, datasource=ds, source=SOURCE_RUN,
                created_at=WINDOW.start)
    job_factory(user=biz1, template=ta, datasource=ds, source=SOURCE_RUN,
                created_at=WINDOW.end)
    job_factory(user=biz1, template=ta, datasource=ds, source=SOURCE_RUN,
                created_at=WINDOW.start - timedelta(microseconds=1))

    got = analytics_service.adoption(db, _platform(db, plat), WINDOW)
    assert got["run_jobs"]["value"] == 1


# ---------------------------------------------------------------- 团队收窄


def test_team_scope_only_sees_own_team(db, a_admin, biz1, biz2, ds, ta, tb, job_factory):
    job_factory(user=biz1, template=ta, datasource=ds, source=SOURCE_RUN,
                created_at=NOW - timedelta(days=1))
    job_factory(user=biz2, template=tb, datasource=ds, source=SOURCE_RUN,
                created_at=NOW - timedelta(days=1))

    got = analytics_service.adoption(db, _team(db, a_admin), WINDOW)
    assert got["run_jobs"]["value"] == 1
    assert got["active_users"]["value"] == 1


def test_platform_only_metrics_are_absent_for_team_admin(db, plat, a_admin, ds, ta):
    """团队管理员看不到跨团队人头 —— **键不存在**,而不是给个 0 或 null。

    渲染成 0 会让人去猜后面藏了什么,反而更想知道全平台是多少;键不存在,
    前端就整块不渲染。
    """
    team_view = analytics_service.adoption(db, _team(db, a_admin), WINDOW)
    plat_view = analytics_service.adoption(db, _platform(db, plat), WINDOW)

    assert "new_users" not in team_view
    assert "retention_rate" not in team_view
    assert "new_task_users" in team_view, "团队视角要有自己的替代指标"

    assert "new_users" in plat_view
    assert "retention_rate" in plat_view


def test_scope_travels_with_the_numbers(db, plat, a_admin, team_a):
    """范围永远跟着数字一起下发 —— 分两个接口发,迟早会配错。

    下发的是**事实**(哪一档、哪个队),不是面向用户的那句中文:板块标题在骨架屏阶段
    就要显示范围,那时数据还没到,标签只能由前端拼 —— 后端再给一份就是第二种拼法。
    """
    plat_scope = analytics_service.adoption(db, _platform(db, plat), WINDOW)["scope"]
    assert plat_scope == {"level": "platform", "team_id": None, "team_name": None}
    assert "label" not in plat_scope

    got = analytics_service.adoption(db, _team(db, a_admin), WINDOW)["scope"]
    assert got == {"level": "team", "team_id": team_a.id, "team_name": team_a.name}


# ---------------------------------------------------------------- 复用与自动化


def test_self_service_ratio_counts_only_business_users(
    db, plat, dev, biz1, ds, ta, job_factory
):
    """自助率 = 普通用户发起的占比。开发者代跑的那部分不算「自助」。"""
    job_factory(user=biz1, template=ta, datasource=ds, source=SOURCE_RUN,
                created_at=NOW - timedelta(days=1))
    job_factory(user=biz1, template=ta, datasource=ds, source=SOURCE_RUN,
                created_at=NOW - timedelta(days=1))
    job_factory(user=dev, template=ta, datasource=ds, source=SOURCE_RUN,
                created_at=NOW - timedelta(days=1))

    got = analytics_service.adoption(db, _platform(db, plat), WINDOW)
    assert got["self_service_ratio"]["value"] == pytest.approx(2 / 3, abs=1e-4)


def test_reuse_multiple_is_runs_over_distinct_tasks(
    db, a_admin, biz1, ds, ta, team_a, dev, datasource_factory, job_factory
, template_factory):
    """复用倍数 = 一次开发被跑了几次。"""
    other = template_factory(dev, ds, team_a, "aad-甲队任务2")
    for _ in range(4):
        job_factory(user=biz1, template=ta, datasource=ds, source=SOURCE_RUN,
                    created_at=NOW - timedelta(days=1))
    job_factory(user=biz1, template=other, datasource=ds, source=SOURCE_RUN,
                created_at=NOW - timedelta(days=1))

    got = analytics_service.adoption(db, _team(db, a_admin), WINDOW)
    assert got["reuse_multiple"]["value"] == pytest.approx(2.5)


# ---------------------------------------------------------------- 三态


def test_metric_is_none_when_nothing_ever_happened(db, a_admin, team_a):
    """从来没有过 ≠ 这个区间是 0。前者该说「还没有数据」并指路。"""
    got = analytics_service.adoption(db, _team(db, a_admin), WINDOW)
    assert got["run_jobs"]["has_data"] is False
    assert got["self_service_ratio"]["value"] is None, "0 次取数的自助率不是 0%,是没得算"


def test_zero_in_window_but_has_history_says_so(db, a_admin, biz1, ds, ta, job_factory):
    """窗口内 0 次、但历史上有过 —— 该显示「最近一次在 X」,而不是「还没有数据」。"""
    long_ago = NOW - timedelta(days=200)
    job_factory(user=biz1, template=ta, datasource=ds, source=SOURCE_RUN, created_at=long_ago)

    got = analytics_service.adoption(db, _team(db, a_admin), WINDOW)
    assert got["run_jobs"]["value"] == 0
    assert got["run_jobs"]["has_data"] is True
    assert got["run_jobs"]["last_event_at"] is not None


# ---------------------------------------------------------------- 路由层


def test_route_refuses_plain_developer(db, dev, team_a):
    from app.core.exceptions import PermissionDeniedError

    with pytest.raises(PermissionDeniedError):
        analytics_adoption(db=db, user=dev)


def test_route_defaults_to_last_30_days(db, plat, biz1, ds, ta, job_factory):
    """不传任何时间参数时的默认区间。"""
    job_factory(user=biz1, template=ta, datasource=ds, source=SOURCE_RUN,
                created_at=datetime.now() - timedelta(days=1))
    got = analytics_adoption(db=db, user=plat)
    assert got["window"]["days"] == 30
    assert got["run_jobs"]["value"] >= 1

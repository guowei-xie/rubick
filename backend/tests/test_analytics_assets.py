"""板块③「任务资产治理」的口径。

最要紧的一条是 test_idle_matches_the_task_list:闲置数必须与任务列表页**逐个相等**。
运营板说「12 个闲置」、点进去列表只有 9 个,这个看板就再也没人信了 —— 一致性优先于
「这里再严格一点」。所以它不是自己数一遍,而是复用 template_service 的同一对函数。

其余几条钉的是几个会虚报的写法:只看 schedule.enabled 不看任务状态、把「从未跑过」
混进「跑过但很久没跑」、以及把长尾算成 0 次而不是 ≤1 次。
"""
from datetime import datetime, timedelta

import pytest

from app.api.routes.analytics import analytics_assets
from app.api.routes.tasks import list_tasks
from app.core.config import settings
from app.core.timewindow import Window
from app.models.query_job import SOURCE_RUN
from app.models.subscription import TaskSchedule
from app.models.template import STATUS_ARCHIVED, STATUS_DRAFT, STATUS_PUBLISHED, SqlTemplate
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER
from app.services import analytics_service

# 本文件的断言都是「整个范围里有几张任务/跑了几次」,两张残留就全错了
pytestmark = pytest.mark.usefixtures("clean_jobs", "clean_templates")

# ID 段 9340–9345
PLAT, A_ADMIN, DEV, BIZ, B_ADMIN = 9340, 9341, 9342, 9343, 9344

NOW = datetime(2026, 6, 15, 12, 0)
WINDOW = Window(NOW - timedelta(days=30), NOW)


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("aas-mysql")


@pytest.fixture
def plat(user_factory):
    return user_factory(PLAT, ROLE_ADMIN, "平台管理员", prefix="aas")


@pytest.fixture
def a_admin(user_factory):
    return user_factory(A_ADMIN, ROLE_DEVELOPER, "甲队团队管理员", prefix="aas")


@pytest.fixture
def dev(user_factory):
    return user_factory(DEV, ROLE_DEVELOPER, "开发者", prefix="aas")


@pytest.fixture
def biz(user_factory):
    return user_factory(BIZ, ROLE_USER, "业务使用者", prefix="aas")


@pytest.fixture
def team_a(db, a_admin, dev, team_factory):
    return team_factory("aas-team-A", [(a_admin, True), (dev, False)])


@pytest.fixture
def clean_templates(db, team_a):
    """本文件的断言全是「整个范围里有几张任务」,残留会让它们全错。

    按团队清而不是清全表:别的文件的任务挂在别的团队下,不该被这里波及。
    """
    db.query(SqlTemplate).filter(SqlTemplate.team_id == team_a.id).delete()
    db.commit()
    yield




def _team(db, admin):
    return analytics_service.resolve_scope(db, admin)


# ---------------------------------------------------------------- 状态分布与浪费信号


def test_status_distribution(db, a_admin, dev, ds, team_a, template_factory):
    template_factory(dev, ds, team_a, "aas-上线", status=STATUS_PUBLISHED)
    template_factory(dev, ds, team_a, "aas-草稿", status=STATUS_DRAFT)
    template_factory(dev, ds, team_a, "aas-回收站", status=STATUS_ARCHIVED)

    got = analytics_assets(db=db, user=a_admin)["as_of"]
    assert got["total"]["value"] == 3
    assert got["published"]["value"] == 1
    assert got["draft"]["value"] == 1
    assert got["archived"]["value"] == 1
    # 全是「此刻」的快照,切时间范围不该变
    assert all(got[k]["windowed"] is False for k in ("total", "published", "draft", "archived"))


def test_never_run_is_separate_from_idle(db, a_admin, dev, biz, ds, team_a, job_factory, template_factory):
    """「上线至今没人跑过」比「跑过但很久没跑」更该被看见,所以单列一个数。"""
    never = template_factory(dev, ds, team_a, "aas-从没跑过")
    ran = template_factory(dev, ds, team_a, "aas-跑过")
    job_factory(user=biz, template=ran, datasource=ds, created_at=datetime.now())

    got = analytics_assets(db=db, user=a_admin)["as_of"]
    assert got["never_run"]["value"] == 1


def test_idle_matches_the_task_list(db, a_admin, dev, ds, team_a, job_factory, biz, template_factory):
    """闲置数必须与任务列表页给出的**完全一致**。

    两处各数一遍的下场是:顶栏说 3 个闲置、点进去列表里只找得到 2 个,而这个看板的全部
    价值就是让人相信这些数字。所以这里复用 template_service.idle_days / is_idle,
    这条断言就是那个复用的凭据。
    """
    assert settings.TASK_IDLE_DAYS > 0, "阈值关着这条用例没有意义"
    old = template_factory(dev, ds, team_a, "aas-很久没跑",
                created_at=datetime.now() - timedelta(days=settings.TASK_IDLE_DAYS + 30))
    fresh = template_factory(dev, ds, team_a, "aas-刚跑过")
    job_factory(user=biz, template=fresh, datasource=ds, created_at=datetime.now())

    board = analytics_assets(db=db, user=a_admin)["as_of"]
    from_list = [t for t in list_tasks(db=db, user=a_admin) if t.is_idle]

    assert board["idle"]["value"] == len(from_list)
    assert {r["template_id"] for r in analytics_assets(db=db, user=a_admin)["idle_list"]} == {
        t.id for t in from_list
    }


def test_idle_threshold_is_published_so_the_card_can_hide_itself(db, a_admin, team_a):
    """阈值一并下发:功能关闭(0)时前端必须把「闲置」卡与它的下钻一起隐藏,
    否则点过去是一张空列表。"""
    got = analytics_assets(db=db, user=a_admin)["as_of"]
    assert got["idle_threshold_days"] == settings.TASK_IDLE_DAYS


# ---------------------------------------------------------------- 排行与长尾


def test_top_templates_carry_reuse_signal(db, a_admin, dev, biz, plat, ds, team_a, job_factory,
                                          user_factory, template_factory):
    """「几个人在跑」比「跑了多少次」更能说明问题:只有作者自己在跑 = 没被业务用起来。"""
    hot = template_factory(dev, ds, team_a, "aas-热门")
    lonely = template_factory(dev, ds, team_a, "aas-自娱自乐")
    biz2 = user_factory(9345, ROLE_USER, "业务乙", prefix="aas")
    base = NOW - timedelta(days=1)
    for u in (biz, biz2, biz):
        job_factory(user=u, template=hot, datasource=ds, source=SOURCE_RUN, created_at=base)
    job_factory(user=dev, template=lonely, datasource=ds, source=SOURCE_RUN, created_at=base)

    rows = analytics_service.assets(db, _team(db, a_admin), WINDOW)["top_templates"]
    top = {r["template_id"]: r for r in rows}
    assert top[hot.id]["run_count"] == 3 and top[hot.id]["distinct_users"] == 2
    assert top[lonely.id]["distinct_users"] == 1


def test_tail_counts_tasks_run_at_most_once(db, a_admin, dev, biz, ds, team_a, job_factory, template_factory):
    """长尾口径是 ≤1 次,不是 0 次 —— 跑过一次就再没人碰的任务同样是长尾。"""
    once = template_factory(dev, ds, team_a, "aas-只跑过一次")
    never = template_factory(dev, ds, team_a, "aas-一次没跑")
    busy = template_factory(dev, ds, team_a, "aas-常跑")
    base = NOW - timedelta(days=1)
    job_factory(user=biz, template=once, datasource=ds, source=SOURCE_RUN, created_at=base)
    for _ in range(5):
        job_factory(user=biz, template=busy, datasource=ds, source=SOURCE_RUN, created_at=base)

    got = analytics_service.assets(db, _team(db, a_admin), WINDOW)
    assert got["tail_count"]["value"] == 2
    assert got["top10_share"]["value"] == 1.0  # 只有三张任务,Top10 就是全部


# ---------------------------------------------------------------- 订阅


def test_schedule_count_requires_the_task_to_be_published(db, a_admin, dev, ds, team_a, template_factory):
    """下线即暂停(TaskSchedule 刻意没有 paused 列)。只看 enabled 会虚报正在跑的定时任务。"""
    live = template_factory(dev, ds, team_a, "aas-在跑的定时", status=STATUS_PUBLISHED)
    offline = template_factory(dev, ds, team_a, "aas-下线的定时", status=STATUS_ARCHIVED)
    for t in (live, offline):
        db.add(TaskSchedule(template_id=t.id, enabled=True, freq="daily", at_time="09:00"))
    db.commit()

    got = analytics_assets(db=db, user=a_admin)["as_of"]
    assert got["schedules_enabled"]["value"] == 1, "下线任务的定时计划被算成还在跑"


# ---------------------------------------------------------------- 存量 vs 流量


def test_as_of_and_window_are_separated(db, a_admin, team_a):
    """这两组数性质不同,必须分开下发 —— 否则用户切了时间范围看见任务总数不变会当成 bug。"""
    got = analytics_assets(db=db, user=a_admin)
    assert set(got["as_of"]) & set(got["window_changes"]) == set()
    assert all(
        m["windowed"] is True
        for k, m in got["window_changes"].items()
        if isinstance(m, dict) and "windowed" in m
    )


def test_publishes_is_platform_only(db, plat, a_admin, team_a):
    """上线次数只能从审计里数,而团队管理员不读审计 —— 这一项因此仅平台视角有。"""
    assert "publishes" in analytics_assets(db=db, user=plat)["window_changes"]
    assert "publishes" not in analytics_assets(db=db, user=a_admin)["window_changes"]

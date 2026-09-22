"""护栏:各板块各自的 SQL 次数上限。

运营分析全是聚合,最容易退化的方式是「在循环里补名字」——  Top10 排行里逐行 db.get 作者与
团队,一眼看不出来,数据一多就是几十次往返。这个文件用 SQLAlchemy 的 before_cursor_execute
数真实发出的语句条数,把它钉死。

上限取的是当前实现的条数 + 少量余量:它要挡的是**量级**退化(多出十几条),不是禁止将来多加
一个指标。真要超了,先问一句「是不是在循环里查了」,确认无误再连同这里的数字一起调。
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from app.core.database import engine
from app.core.timewindow import Window
from app.models.query_job import SOURCE_RUN
from app.models.template import SqlTemplate
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER
from app.services import analytics_service

pytestmark = pytest.mark.usefixtures("clean_jobs")

# ID 段 9360–9363
PLAT, DEV, BIZ, A_ADMIN = 9360, 9361, 9362, 9363

NOW = datetime(2026, 6, 15, 12, 0)
WINDOW = Window(NOW - timedelta(days=30), NOW)

# 当前实现的条数 + 3 条余量(实测,平台/团队两种视角:adoption 10/10、health 10/10、
# assets 11/10、governance 14/9、api_usage 5/5)。留余量是为了「多加一个指标」不必连带改这里,
# 但多出十几条一定会被挡住。
#
# 上限要**跟着实现往下走**:一次重构把条数砍掉三分之一后不收紧,这里就成了一个再也拦不住
# 任何东西的数字 —— 它挡的是回归,不是绝对值。
#
# 真正判 N+1 的是后面两条:test_budget_does_not_grow_with_data_volume 要求数据翻倍后条数
# **完全不变**,test_meta_does_not_query_per_managed_team 要求它不随管的团队数增长。
# 那两个才是硬判据,这里的上限只是量级护栏。
BUDGET = {"adoption": 13, "health": 13, "assets": 14, "governance": 17, "api_usage": 8}


class _Counter:
    def __init__(self):
        self.n = 0

    def __call__(self, *_a, **_kw):
        self.n += 1


def count_queries(fn):
    c = _Counter()
    event.listen(engine, "before_cursor_execute", c)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", c)
    return c.n


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("abg-mysql")


@pytest.fixture
def plat(user_factory):
    return user_factory(PLAT, ROLE_ADMIN, "平台管理员", prefix="abg")


@pytest.fixture
def a_admin(user_factory):
    return user_factory(A_ADMIN, ROLE_DEVELOPER, "甲队团队管理员", prefix="abg")


@pytest.fixture
def dev(user_factory):
    return user_factory(DEV, ROLE_DEVELOPER, "开发者", prefix="abg")


@pytest.fixture
def biz(user_factory):
    return user_factory(BIZ, ROLE_USER, "业务使用者", prefix="abg")


@pytest.fixture
def team_a(db, a_admin, dev, team_factory):
    return team_factory("abg-team-A", [(a_admin, True), (dev, False)])


@pytest.fixture
def loaded(db, dev, biz, ds, team_a, job_factory):
    """铺一批任务与运行 —— 空库量不出 N+1,排行榜为空时根本不会去补名字。"""
    from sqlalchemy import select

    base = NOW - timedelta(days=2)
    for i in range(12):
        name = f"abg-任务{i}"
        t = db.scalar(select(SqlTemplate).where(SqlTemplate.name == name))
        if t is None:
            t = SqlTemplate(
                name=name, datasource_id=ds.id, dialect="mysql", status="published",
                author_id=dev.id, team_id=team_a.id,
            )
            db.add(t)
            db.commit()
            db.refresh(t)
        for _ in range(3):
            job_factory(user=biz, template=t, datasource=ds, source=SOURCE_RUN,
                        created_at=base)
    return True


@pytest.mark.parametrize("board", ["adoption", "health", "assets", "governance", "api_usage"])
def test_board_stays_within_query_budget_platform(db, plat, loaded, board):
    fn = getattr(analytics_service, board)
    scope = analytics_service.resolve_scope(db, plat)
    n = count_queries(lambda: fn(db, scope, WINDOW))
    assert n <= BUDGET[board], (
        f"{board} 发了 {n} 条 SQL(上限 {BUDGET[board]})。"
        "先确认不是在循环里补名字 —— 排行榜的名字要批量 IN 查询,不能逐行 db.get"
    )


@pytest.mark.parametrize("board", ["adoption", "health", "assets", "governance", "api_usage"])
def test_board_stays_within_query_budget_team(db, a_admin, loaded, board):
    """团队视角会多几条子查询,但**不该随任务数增长**。"""
    fn = getattr(analytics_service, board)
    scope = analytics_service.resolve_scope(db, a_admin)
    n = count_queries(lambda: fn(db, scope, WINDOW))
    assert n <= BUDGET[board], f"{board}(团队视角)发了 {n} 条 SQL,上限 {BUDGET[board]}"


def test_budget_does_not_grow_with_data_volume(db, plat, loaded, dev, biz, ds, team_a,
                                               job_factory):
    """同一个板块,数据翻几倍,SQL 条数必须纹丝不动 —— 这才是 N+1 真正的判据。"""
    scope = analytics_service.resolve_scope(db, plat)
    before = count_queries(lambda: analytics_service.assets(db, scope, WINDOW))

    base = NOW - timedelta(days=2)
    for i in range(12, 30):
        t = SqlTemplate(
            name=f"abg-任务{i}", datasource_id=ds.id, dialect="mysql", status="published",
            author_id=dev.id, team_id=team_a.id,
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        job_factory(user=biz, template=t, datasource=ds, source=SOURCE_RUN, created_at=base)

    after = count_queries(lambda: analytics_service.assets(db, scope, WINDOW))
    assert after == before, f"任务从 12 张涨到 30 张,SQL 从 {before} 条涨到了 {after} 条"


def test_meta_does_not_query_per_managed_team(db, a_admin, team_factory, loaded):
    """管三个队的人打开页面,SQL 条数与管一个队时**一样** —— 范围选项的名字要批量 IN 查。

    上面那两个预算用例看不见这条:它们的团队管理员只管一个队,循环里查库与批量查库
    发出的条数恰好相等。这是首屏必打的接口,逐队一次往返正是最容易溜进去的 N+1。
    """
    # 两次都先空跑一遍再计数:team_factory 的 commit 会让 session 里的团队行过期,
    # 紧接着的第一次调用要多补一条 refresh —— 那是 identity map 的事,不是 N+1。
    # 不预热的话这个用例量到的是「上一句有没有 commit」,与它要挡的东西毫无关系
    def measure():
        analytics_service.meta(db, a_admin)
        return count_queries(lambda: analytics_service.meta(db, a_admin))

    one = measure()
    team_factory("abg-team-C", [(a_admin, True)])
    team_factory("abg-team-D", [(a_admin, True)])
    three = measure()
    assert three == one, f"管的队从 1 个涨到 3 个,meta 的 SQL 从 {one} 条涨到了 {three} 条"

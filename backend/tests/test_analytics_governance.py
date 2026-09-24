"""板块④「权限与配置治理」的口径。

两条最值钱:

  · test_dormant_grants_pair_user_with_task —— 「授权了但从没跑过」必须按 (人, 任务) 配对,
    而不是分别数人和数任务。配错了会给出一个看起来合理、实际毫无意义的数字;
  · test_team_view_has_no_platform_keys —— 团队视角下平台级字段**一个键都不存在**。
    这是防泄漏的关键断言:漏一个键,团队管理员就看到了全平台的人头结构,
    而页面上它和本团队的数字长得一模一样。
"""
from datetime import datetime, timedelta

import pytest

from app.api.routes.analytics import analytics_governance
from app.core.timewindow import Window
from app.models.permission import ACTION_EDIT, ACTION_RUN, Permission
from app.models.query_job import SOURCE_RUN, SOURCE_TEST
from app.models.template import SqlTemplate
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER
from app.services import analytics_service

# ID 段 9350–9356
PLAT, A_ADMIN, B_ADMIN, DEV, BIZ1, BIZ2, OUTSIDER = 9350, 9351, 9352, 9353, 9354, 9355, 9356

NOW = datetime(2026, 6, 15, 12, 0)
WINDOW = Window(NOW - timedelta(days=30), NOW)


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("agv-mysql")


@pytest.fixture
def plat(user_factory):
    return user_factory(PLAT, ROLE_ADMIN, "平台管理员", prefix="agv")


@pytest.fixture
def a_admin(user_factory):
    return user_factory(A_ADMIN, ROLE_DEVELOPER, "甲队团队管理员", prefix="agv")


@pytest.fixture
def dev(user_factory):
    return user_factory(DEV, ROLE_DEVELOPER, "开发者", prefix="agv")


@pytest.fixture
def biz1(user_factory):
    return user_factory(BIZ1, ROLE_USER, "业务甲", prefix="agv")


@pytest.fixture
def biz2(user_factory):
    return user_factory(BIZ2, ROLE_USER, "业务乙", prefix="agv")


@pytest.fixture
def outsider(user_factory):
    """被授了编辑权、但从来不在这个团队里的人 —— 僵尸编辑权的主角。"""
    return user_factory(OUTSIDER, ROLE_DEVELOPER, "已离队的人", prefix="agv")


@pytest.fixture
def team_a(db, a_admin, dev, team_factory):
    return team_factory("agv-team-A", [(a_admin, True), (dev, False)])


@pytest.fixture
def clean_grants(db):
    db.query(Permission).delete()
    db.commit()
    yield


@pytest.fixture
def clean_templates(db, team_a):
    db.query(SqlTemplate).filter(SqlTemplate.team_id == team_a.id).delete()
    db.commit()
    yield


pytestmark = pytest.mark.usefixtures("clean_jobs", "clean_grants", "clean_templates")




def _grant(db, user, tmpl, action=ACTION_RUN, by=None):
    db.add(Permission(
        subject_type="user", subject_id=str(user.id),
        resource_type="template", resource_id=str(tmpl.id),
        action=action, granted_by=by.id if by else None,
    ))
    db.commit()


@pytest.fixture
def ta(db, dev, ds, team_a, template_factory):
    return template_factory(dev, ds, team_a, "agv-任务")


# ---------------------------------------------------------------- 空转授权


def test_dormant_grants_pair_user_with_task(
    db, a_admin, dev, biz1, biz2, ds, ta, team_a, job_factory
, template_factory):
    """必须按 (人, 任务) 配对 —— 分别数人和数任务会给出一个看起来合理但毫无意义的数。

    这里刻意造一个交叉:业务甲跑过 A 没跑过 B,业务乙跑过 B 没跑过 A。
    按人数看两个人都活跃、按任务看两张任务都被跑过,而真实的空转授权是 2 条。
    """
    tb = template_factory(dev, ds, team_a, "agv-任务B")
    for u in (biz1, biz2):
        _grant(db, u, ta, by=dev)
        _grant(db, u, tb, by=dev)
    job_factory(user=biz1, template=ta, datasource=ds, source=SOURCE_RUN,
                created_at=NOW - timedelta(days=1))
    job_factory(user=biz2, template=tb, datasource=ds, source=SOURCE_RUN,
                created_at=NOW - timedelta(days=1))

    got = analytics_service.governance(db, analytics_service.resolve_scope(db, a_admin), WINDOW)
    assert got["as_of"]["grants_total"]["value"] == 4
    assert got["as_of"]["dormant_grants"]["value"] == 2
    pairs = {(r["user_id"], r["template_id"]) for r in got["dormant_detail"]}
    assert pairs == {(biz1.id, tb.id), (biz2.id, ta.id)}


def test_dormant_is_all_time_not_windowed(db, a_admin, dev, biz1, ds, ta, job_factory):
    """三个月前用过的人不是僵尸 —— 授权是存量事实,套时间窗会把他误报进来。"""
    _grant(db, biz1, ta, by=dev)
    job_factory(user=biz1, template=ta, datasource=ds, source=SOURCE_RUN,
                created_at=NOW - timedelta(days=200))  # 远在窗口之外

    got = analytics_service.governance(db, analytics_service.resolve_scope(db, a_admin), WINDOW)
    assert got["as_of"]["dormant_grants"]["value"] == 0
    # windowed=False 让卡片挂上「此刻」标记;而「为什么不吃时间范围」这句话由
    # METRIC_NOTES 下发到 tooltip —— 两者缺一个,用户都会以为它跟着上面的时间控件走
    assert got["as_of"]["dormant_grants"]["windowed"] is False
    note = next(n for n in analytics_service.METRIC_NOTES if n["key"] == "dormant_grants")
    assert "全期" in note["note"]


def test_test_runs_do_not_redeem_a_grant(db, a_admin, dev, biz1, ds, ta, job_factory):
    """只有正式取数算「用过」。作者试跑不能替业务方把一条授权洗白。"""
    _grant(db, biz1, ta, by=dev)
    job_factory(user=biz1, template=ta, datasource=ds, source=SOURCE_TEST,
                created_at=NOW - timedelta(days=1))

    got = analytics_service.governance(db, analytics_service.resolve_scope(db, a_admin), WINDOW)
    assert got["as_of"]["dormant_grants"]["value"] == 1


def test_non_numeric_subject_ids_are_skipped(db, a_admin, ta, team_a):
    """subject_id 是自由字符串列(将来可能是组 id)。非数字的一律跳过,不能让它把整块算崩。"""
    db.add(Permission(
        subject_type="user", subject_id="group:growth",
        resource_type="template", resource_id=str(ta.id), action=ACTION_RUN,
    ))
    db.commit()

    got = analytics_service.governance(db, analytics_service.resolve_scope(db, a_admin), WINDOW)
    assert got["as_of"]["grants_total"]["value"] == 0


def test_stale_edit_grants_spot_people_who_left_the_team(
    db, a_admin, dev, outsider, ds, ta, team_a
):
    """授过 edit、人却已不在该任务的团队里 —— can_edit 因此静默失效,行还留着。"""
    _grant(db, dev, ta, action=ACTION_EDIT, by=a_admin)        # 还在队里
    _grant(db, outsider, ta, action=ACTION_EDIT, by=a_admin)   # 不在队里

    got = analytics_service.governance(db, analytics_service.resolve_scope(db, a_admin), WINDOW)
    assert got["as_of"]["stale_edit_grants"]["value"] == 1


# ---------------------------------------------------------------- 可见范围


def test_team_view_has_no_platform_keys(db, plat, a_admin, team_a):
    """**防泄漏的关键断言。** 团队管理员这里一个平台级键都不该有。

    渲染成 0 或「无权限」会让人去猜后面藏了什么;键不存在,前端就整块不渲染。
    """
    team_view = analytics_governance(db=db, user=a_admin)
    plat_view = analytics_governance(db=db, user=plat)

    assert "platform" not in team_view
    assert set(plat_view["platform"]) == {"teams_without_admin"}


def test_credentials_block_carries_no_account_names(db, a_admin, plat, team_a, ds):
    """取数账号那一块**只有计数,没有任何字符串** —— 看板从不需要库账号名。

    用「结构上装不下字符串」来钉,而不是逐个断言 username 为 None:后者只挡得住今天
    这一种字段名,而库账号名是半机密(见 credential_service 的模块 docstring),
    少一个泄密面比多一条断言值钱。两种视角都要成立。
    """
    def leaves(v):
        if isinstance(v, dict):
            return [x for sub in v.values() for x in leaves(sub)]
        return [v]

    for user in (a_admin, plat):
        block = analytics_governance(db=db, user=user)["as_of"]["credentials"]
        assert set(block) == {"configured", "required", "not_ready", "coverage"}
        assert not [v for v in leaves(block) if isinstance(v, str)]


def test_grants_are_scoped_to_the_team(db, a_admin, b_admin_team, biz1, ds, dev, team_a, template_factory):
    """别队任务上的授权不进本团队的数。"""
    mine = template_factory(dev, ds, team_a, "agv-本队任务")
    theirs = template_factory(dev, ds, b_admin_team, "agv-别队任务")
    _grant(db, biz1, mine, by=dev)
    _grant(db, biz1, theirs, by=dev)

    got = analytics_governance(db=db, user=a_admin)
    assert got["as_of"]["grants_total"]["value"] == 1


@pytest.fixture
def b_admin_team(db, user_factory, team_factory):
    b = user_factory(B_ADMIN, ROLE_DEVELOPER, "乙队团队管理员", prefix="agv")
    return team_factory("agv-team-B", [(b, True)])

"""采纳板里开放 API 那几项的口径(API 调用、token 发放与活跃)。

开放 API 原先单立一块板,瘦身后并进采纳板 —— 它只是运行来源中的一种。三条最值钱:

  · test_active_window_is_fixed_seven_days_not_the_page_window —— 活跃判据固定看 7 天,
    页面把范围拖到 90 天也不放宽。它是全页唯一一个既不是「本区间」也不是「此刻」的窗口;
  · test_team_view_narrows_tokens_to_members —— token 按团队成员、运行按任务归属,
    同一块板里两套收窄口径并存是有意为之,不是等着被「修正」的不一致;
  · test_active_has_data_follows_issued —— 界面「没接过 API 就不摆这两张卡」压在
    这两个 has_data 上,判据一变,没开张的平台就会多出两张「—」。
"""
from datetime import datetime, timedelta

import pytest

from app.core.timewindow import Window
from app.models.query_job import SOURCE_API, SOURCE_RUN, SOURCE_SUBSCRIBE, SOURCE_TEST
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER
from app.services import analytics_service, api_token_service

pytestmark = pytest.mark.usefixtures("clean_jobs", "clean_tokens")

# ID 段 9370–9374
PLAT, A_ADMIN, DEV, CALLER, OUTSIDER = 9370, 9371, 9372, 9373, 9374

NOW = datetime(2026, 6, 15, 12, 0)
WINDOW = Window(NOW - timedelta(days=30), NOW)
# 「固定 7 天」那条用例要的对照窗口:页面范围开到 90 天,活跃判据也不该跟着变宽
WIDE_WINDOW = Window(NOW - timedelta(days=90), NOW)


@pytest.fixture
def clean_tokens(db):
    """每个用例从「没有任何 token」起步。

    用户是按固定 id get-or-create 的、跨用例复用,而 token 是写在 User 三列上的状态 ——
    上一个用例发的 token 会原封不动地落进下一个用例的发放数里(同 clean_jobs 的取舍)。
    """
    from app.models.user import User

    db.query(User).update(
        {"api_token_hash": None, "api_token_issued_at": None, "api_token_last_used_at": None}
    )
    db.commit()
    yield


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("aap-mysql")


@pytest.fixture
def plat(user_factory):
    return user_factory(PLAT, ROLE_ADMIN, "平台管理员", prefix="aap")


@pytest.fixture
def a_admin(user_factory):
    return user_factory(A_ADMIN, ROLE_DEVELOPER, "甲队团队管理员", prefix="aap")


@pytest.fixture
def dev(user_factory):
    return user_factory(DEV, ROLE_DEVELOPER, "开发者", prefix="aap")


@pytest.fixture
def caller(user_factory):
    """队内的调用方:既持 token,又发起 API 运行。"""
    return user_factory(CALLER, ROLE_USER, "队内调用方", prefix="aap")


@pytest.fixture
def outsider(user_factory):
    """队外的调用方:持 token,但不属于甲队 —— 团队视角下不该计入发放数。"""
    return user_factory(OUTSIDER, ROLE_USER, "队外调用方", prefix="aap")


@pytest.fixture
def team_a(db, a_admin, dev, caller, team_factory):
    return team_factory("aap-team-A", [(a_admin, True), (dev, False), (caller, False)])


@pytest.fixture
def ta(db, dev, ds, team_a, template_factory):
    return template_factory(dev, ds, team_a, "aap-甲队任务")


def _platform(db, plat):
    return analytics_service.resolve_scope(db, plat)


def _team(db, admin):
    return analytics_service.resolve_scope(db, admin)


def _give_token(db, user, *, used_days_ago=None):
    """真的签发一枚(api_token_service.issue),只有「最近使用」手工盖。

    签发的写入形状交还给服务层 —— 手抄三列的话,口径哪天从「hash 非空」收紧成
    「hash 非空且形状合法」,本文件全部用例会一起失真而不报错。
    要绕开的只有 last_used_at:它只能经 authenticate 盖上当下,造不出「8 天前」。

    used_days_ago 从**真实时钟**起算,不是本文件那个固定的 NOW:「近 7 天用过」是
    windowed=False 的此刻快照,服务端就是拿 datetime.now() 回看 7 天的 —— 这正是
    它不吃页面时间范围的原因,测试必须站在同一个时钟上。
    """
    api_token_service.issue(db, user)
    if used_days_ago is not None:
        user.api_token_last_used_at = datetime.now() - timedelta(days=used_days_ago)
        db.commit()


# ---------------------------------------------------------------- token(此刻口径)


def test_tokens_count_only_live_hashes(db, plat, caller, outsider):
    """以 hash 非空为唯一判据:吊销(三列一起清空)后立刻不再计入发放数。"""
    _give_token(db, caller)
    _give_token(db, outsider)
    assert analytics_service.adoption(db, _platform(db, plat), WINDOW)["tokens_issued"]["value"] == 2

    api_token_service.revoke(db, outsider)  # 「三列同生同灭」的定义住在它那里
    assert analytics_service.adoption(db, _platform(db, plat), WINDOW)["tokens_issued"]["value"] == 1


def test_active_window_is_fixed_seven_days_not_the_page_window(db, plat, caller, outsider):
    """**本文件最值钱的一条**:活跃判据固定看 7 天,页面把范围开到 90 天也不放宽。

    否则「近 7 天用过」会随着用户拖时间范围而变大,而它的标题却一直写着「近 7 天」。
    """
    _give_token(db, caller, used_days_ago=6)     # 在 7 天内
    _give_token(db, outsider, used_days_ago=8)   # 刚过 7 天

    # windowed=False 是前端挂「此刻」标记的依据,与窗口无关,断言一次即可
    assert analytics_service.adoption(
        db, _platform(db, plat), WINDOW
    )["tokens_active_7d"]["windowed"] is False

    for window in (WINDOW, WIDE_WINDOW):
        got = analytics_service.adoption(db, _platform(db, plat), window)
        assert got["tokens_active_7d"]["value"] == 1, "活跃窗口跟着页面范围变宽了"


def test_active_has_data_follows_issued(db, plat, caller):
    """**前端空态的地基**:发过 token 却没人用过 ⇒ value=0 且 has_data=True。

    界面据此说「本区间没有发生」(绿色,正向结果),而不是「还没有数据」(灰色「—」)。
    一枚都没发过、也从没有过 API 调用时,采纳板干脆不摆这两张卡。
    """
    got = analytics_service.adoption(db, _platform(db, plat), WINDOW)
    assert got["tokens_issued"]["value"] == 0 and got["tokens_issued"]["has_data"] is False
    assert got["tokens_active_7d"]["has_data"] is False

    _give_token(db, caller)  # 发了,但从没用过
    got = analytics_service.adoption(db, _platform(db, plat), WINDOW)
    assert got["tokens_issued"]["has_data"] is True
    assert got["tokens_active_7d"]["value"] == 0
    assert got["tokens_active_7d"]["has_data"] is True, "发过 token 就不该说「还没有数据」"


# ---------------------------------------------------------------- API 调用(窗口口径)


def test_api_runs_counts_only_source_api(db, plat, caller, dev, ds, ta, job_factory):
    """只认 source=api,另三种来源一条都不许串。"""
    base = NOW - timedelta(days=1)
    for src in (SOURCE_RUN, SOURCE_TEST, SOURCE_SUBSCRIBE):
        job_factory(user=dev, template=ta, datasource=ds, source=src, created_at=base)
    job_factory(user=caller, template=ta, datasource=ds, source=SOURCE_API, created_at=base)

    got = analytics_service.adoption(db, _platform(db, plat), WINDOW)
    assert got["api_runs"]["value"] == 1


def test_api_runs_has_data_looks_at_all_time(db, plat, caller, ds, ta, job_factory):
    """窗口内 0 次、但两百天前调过 ⇒ value=0 且 has_data=True。

    「这段时间没人调」与「从来没人调过」指向不同的下一步,界面必须说得出区别。
    """
    job_factory(user=caller, template=ta, datasource=ds, source=SOURCE_API,
                created_at=NOW - timedelta(days=200))
    got = analytics_service.adoption(db, _platform(db, plat), WINDOW)
    assert got["api_runs"]["value"] == 0
    assert got["api_runs"]["has_data"] is True


# ---------------------------------------------------------------- 团队视角


def test_team_view_narrows_tokens_to_members(db, a_admin, caller, outsider, ds, ta, job_factory):
    """**两套收窄口径并存是有意为之**:token 按团队成员,运行按任务归属。

    于是队外的人持 token 不计入发放数,但他跑本队任务的那次 API 调用照样计入 ——
    团队视角下「token 少、调用多」并不矛盾。
    """
    _give_token(db, caller, used_days_ago=1)
    _give_token(db, outsider, used_days_ago=1)
    job_factory(user=outsider, template=ta, datasource=ds, source=SOURCE_API,
                created_at=NOW - timedelta(days=1))

    got = analytics_service.adoption(db, _team(db, a_admin), WINDOW)
    assert got["tokens_issued"]["value"] == 1, "队外的人的 token 被算进来了"
    assert got["tokens_active_7d"]["value"] == 1
    assert got["api_runs"]["value"] == 1, "运行该按任务归属收窄,不是按发起人是否在队里"

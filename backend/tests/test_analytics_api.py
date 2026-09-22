"""板块⑤「开放 API」的口径。

这块板要回答的是「平台的取数能力有没有真的被脚本与 Agent 接走」。它有两个别处没有的特点,
本文件大半篇幅都在钉这两条:

① **「近 7 天用过」不吃页面的时间范围**。它是全页唯一一个既不是「本区间」也不是「此刻」的
   窗口 —— 问的是「这些长期凭证还活着吗」(安全卫生),而不是「这段时间 API 用得多不多」。
   把页面窗口调到 90 天,活跃判据仍然只看 7 天。
② **同一块板里并存两套收窄口径**:token 按**团队成员**收窄(token 属于人),运行与下载按
   **任务归属**收窄。于是团队视角下「3 枚 token / 100 次调用」并不矛盾。强行统一会让其中
   一个算错,所以这里用一条用例把「两套并存」钉成有意为之,而不是等人来「修正」。

另有一条是前端的地基:整块空态压在 `tokens_issued.has_data` / `api_runs.has_data` 上,
而前者走的是 metric() 的默认推导。哪天后端给它显式传 has_data,界面的空态就会失真。
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import update

from app.api.routes.analytics import analytics_api
from app.core.exceptions import PermissionDeniedError
from app.core.timewindow import Window
from app.models.audit import VIA_API, VIA_WEB, DownloadEvent
from app.models.query_job import (
    SOURCE_API, SOURCE_RUN, SOURCE_SUBSCRIBE, SOURCE_TEST,
)
from app.models.template import SqlTemplate
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER
from app.services import analytics_service

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


@pytest.fixture
def tb(db, dev, ds, team_a, template_factory):
    return template_factory(dev, ds, team_a, "aap-甲队任务2")


def _platform(db, plat):
    return analytics_service.resolve_scope(db, plat)


def _team(db, admin):
    return analytics_service.resolve_scope(db, admin)


def _give_token(db, user, *, used_days_ago=None):
    """直接写 User 的三列,**不走 api_token_service.issue**。

    issue 会写审计,而 last_used_at 只能经 authenticate 盖上 —— 那样就没法造出
    「8 天前用过」这种时刻,而「固定 7 天」恰恰是本文件最要紧的一条。

    used_days_ago 从**真实时钟**起算,不是本文件那个固定的 NOW:「近 7 天用过」是
    windowed=False 的此刻快照,服务端就是拿 datetime.now() 回看 7 天的 —— 这正是
    它不吃页面时间范围的原因,测试必须站在同一个时钟上。
    """
    user.api_token_hash = f"hash-{user.id}"
    user.api_token_issued_at = datetime.now() - timedelta(days=30)
    user.api_token_last_used_at = (
        None if used_days_ago is None else datetime.now() - timedelta(days=used_days_ago)
    )
    db.commit()


# ---------------------------------------------------------------- token(此刻口径)


def test_tokens_count_only_live_hashes(db, plat, caller, outsider):
    """以 hash 非空为唯一判据:吊销(三列一起清空)后立刻不再计入发放数。"""
    _give_token(db, caller)
    _give_token(db, outsider)
    assert analytics_service.api_usage(db, _platform(db, plat), WINDOW)["tokens_issued"]["value"] == 2

    # 吊销 = 三列同生同灭(见 api_token_service.revoke)
    outsider.api_token_hash = None
    outsider.api_token_issued_at = None
    outsider.api_token_last_used_at = None
    db.commit()
    assert analytics_service.api_usage(db, _platform(db, plat), WINDOW)["tokens_issued"]["value"] == 1


def test_active_window_is_fixed_seven_days_not_the_page_window(db, plat, caller, outsider):
    """**本文件最值钱的一条**:活跃判据固定看 7 天,页面把范围开到 90 天也不放宽。

    否则「近 7 天用过」会随着用户拖时间范围而变大,而它的标题却一直写着「近 7 天」。
    """
    _give_token(db, caller, used_days_ago=6)     # 在 7 天内
    _give_token(db, outsider, used_days_ago=8)   # 刚过 7 天

    for window in (WINDOW, WIDE_WINDOW):
        got = analytics_service.api_usage(db, _platform(db, plat), window)
        assert got["tokens_issued"]["value"] == 2
        assert got["tokens_active_7d"]["value"] == 1, "活跃窗口跟着页面范围变宽了"
        # windowed=False 是前端挂「此刻」标记的依据
        assert got["tokens_active_7d"]["windowed"] is False


def test_active_has_data_follows_issued(db, plat, caller):
    """**前端整块空态的地基**:发过 token 却没人用过 ⇒ value=0 且 has_data=True。

    界面据此说「本区间没有发生」(绿色,正向结果),而不是「还没有数据」(灰色「—」)。
    一枚都没发过时两者都为空,整块才换成「还没开张」的指引。
    """
    got = analytics_service.api_usage(db, _platform(db, plat), WINDOW)
    assert got["tokens_issued"]["value"] == 0 and got["tokens_issued"]["has_data"] is False
    assert got["tokens_active_7d"]["has_data"] is False

    _give_token(db, caller)  # 发了,但从没用过
    got = analytics_service.api_usage(db, _platform(db, plat), WINDOW)
    assert got["tokens_issued"]["has_data"] is True
    assert got["tokens_active_7d"]["value"] == 0
    assert got["tokens_active_7d"]["has_data"] is True, "发过 token 就不该说「还没有数据」"


# ---------------------------------------------------------------- 运行与下载(窗口口径)


def test_api_runs_counts_only_source_api(db, plat, caller, dev, ds, ta, job_factory):
    """界面正式取数 / 作者试跑 / 订阅定时,一条都不许串进 API 调用数。"""
    base = NOW - timedelta(days=1)
    for src in (SOURCE_RUN, SOURCE_TEST, SOURCE_SUBSCRIBE):
        job_factory(user=dev, template=ta, datasource=ds, source=src, created_at=base)
    job_factory(user=caller, template=ta, datasource=ds, source=SOURCE_API, created_at=base)

    got = analytics_service.api_usage(db, _platform(db, plat), WINDOW)
    assert got["api_runs"]["value"] == 1


def test_api_run_share_denominator_is_all_runs(db, plat, caller, dev, ds, ta, job_factory):
    """分母是窗口内**全部**运行(含试跑与定时),不是只有正式取数。"""
    base = NOW - timedelta(days=1)
    for src in (SOURCE_RUN, SOURCE_TEST, SOURCE_SUBSCRIBE):
        job_factory(user=dev, template=ta, datasource=ds, source=src, created_at=base)
    job_factory(user=caller, template=ta, datasource=ds, source=SOURCE_API, created_at=base)

    got = analytics_service.api_usage(db, _platform(db, plat), WINDOW)
    assert got["api_run_share"]["value"] == pytest.approx(0.25)


def test_api_run_share_is_none_when_nothing_ran(db, plat):
    """一次运行都没有时是「没得算」(None),不是 0% —— 同 ratio 的既有口径。"""
    got = analytics_service.api_usage(db, _platform(db, plat), WINDOW)
    assert got["api_run_share"]["value"] is None
    assert got["api_run_share"]["has_data"] is False


def test_api_runs_has_data_looks_at_all_time(db, plat, caller, ds, ta, job_factory):
    """窗口内 0 次、但两百天前调过 ⇒ value=0 且 has_data=True。

    「这段时间没人调」与「从来没人调过」指向不同的下一步,界面必须说得出区别。
    """
    job_factory(user=caller, template=ta, datasource=ds, source=SOURCE_API,
                created_at=NOW - timedelta(days=200))
    got = analytics_service.api_usage(db, _platform(db, plat), WINDOW)
    assert got["api_runs"]["value"] == 0
    assert got["api_runs"]["has_data"] is True


def test_downloads_count_only_via_api(db, plat, caller, ds, ta, job_factory):
    """同一次运行上两条下载事件,只数走开放 API 那条;界面导出记 web,不计入。"""
    base = NOW - timedelta(days=1)
    job = job_factory(user=caller, template=ta, datasource=ds, source=SOURCE_API, created_at=base)
    db.add(DownloadEvent(user_id=caller.id, job_id=job.id, row_count=1, via=VIA_API))
    db.add(DownloadEvent(user_id=caller.id, job_id=job.id, row_count=1, via=VIA_WEB))
    db.commit()
    db.execute(update(DownloadEvent).values(created_at=base))
    db.commit()

    got = analytics_service.api_usage(db, _platform(db, plat), WINDOW)
    assert got["api_downloads"]["value"] == 1


# ---------------------------------------------------------------- Top 任务


def test_top_tasks_ranked_by_api_calls_only(db, plat, caller, dev, ds, ta, tb, job_factory):
    """排行只按 API 调用次数倒序;同一张任务在界面上被跑多少次都不参与排序。"""
    base = NOW - timedelta(days=1)
    for _ in range(3):
        job_factory(user=caller, template=tb, datasource=ds, source=SOURCE_API, created_at=base)
    job_factory(user=caller, template=ta, datasource=ds, source=SOURCE_API, created_at=base)
    # ta 在界面上被跑了很多次 —— 不该因此排到前面
    for _ in range(10):
        job_factory(user=dev, template=ta, datasource=ds, source=SOURCE_RUN, created_at=base)

    top = analytics_service.api_usage(db, _platform(db, plat), WINDOW)["top_tasks"]
    assert [(r["template_id"], r["run_count"]) for r in top] == [(tb.id, 3), (ta.id, 1)]
    assert len(top) <= analytics_service.TOP_N


def test_top_tasks_keeps_id_when_template_is_gone(db, plat, caller, ds, ta, job_factory):
    """任务被硬删后名字取不到,但编号与次数仍在 —— 前端那两列可空的契约由这条守着。

    编号还在才有得查:顺着它能在审计里找到这批调用是谁发起的。
    """
    base = NOW - timedelta(days=1)
    job_factory(user=caller, template=ta, datasource=ds, source=SOURCE_API, created_at=base)
    tid = ta.id
    db.delete(db.get(SqlTemplate, tid))
    db.commit()

    top = analytics_service.api_usage(db, _platform(db, plat), WINDOW)["top_tasks"]
    assert len(top) == 1
    assert top[0]["template_id"] == tid and top[0]["run_count"] == 1
    assert top[0]["name"] is None and top[0]["team_name"] is None


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

    got = analytics_service.api_usage(db, _team(db, a_admin), WINDOW)
    assert got["tokens_issued"]["value"] == 1, "队外的人的 token 被算进来了"
    assert got["tokens_active_7d"]["value"] == 1
    assert got["api_runs"]["value"] == 1, "运行该按任务归属收窄,不是按发起人是否在队里"


def test_team_view_has_same_keys_as_platform(db, plat, a_admin, caller):
    """两种视角的键集合**完全相同** —— 这块板没有平台专属键。

    有了它,将来谁加了 platform-only 键会立刻被挡下;前端也就永远不必写那条
    「团队视角下这个键不存在」的分支(Adoption / Governance 都为此写过)。
    """
    _give_token(db, caller)
    plat_keys = set(analytics_service.api_usage(db, _platform(db, plat), WINDOW))
    team_keys = set(analytics_service.api_usage(db, _team(db, a_admin), WINDOW))
    assert plat_keys == team_keys


# ---------------------------------------------------------------- 路由层


def test_route_refuses_plain_developer(db, dev):
    """权限只在 _scoped 里判一次,这条是它的唯一守卫:普通开发者进不来。"""
    with pytest.raises(PermissionDeniedError):
        analytics_api(db=db, user=dev)


def test_route_defaults_to_last_30_days(db, plat):
    """不传任何时间参数时窗口是最近 30 天(与其余四个端点同一缺省)。"""
    got = analytics_api(db=db, user=plat)
    assert got["window"]["days"] == 30

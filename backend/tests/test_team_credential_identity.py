"""取数身份:每一条取数链路实际用的是**任务所属团队**的库账号。

这是团队功能最核心的一组断言 —— 它保证「数据边界 = 团队」这句话在代码里真的成立:
不是发起人的账号、不是作者的账号、更不是能看全库的数据源公共账号。

与仓库既有测试同风格:直接调服务/路由函数,不起 TestClient;取数全部经 spy_connector。
"""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.api.routes.templates import create_template, enum_sql, publish, update_template
from app.core.exceptions import CredentialRequiredError, PermissionDeniedError
from app.models.audit import ACTION_RUN_QUERY, ACTION_RUN_QUERY_FAILED
from app.models.notification import Notification
from app.models.query_job import JOB_FAILED, JOB_SUCCESS, SOURCE_TEST, QueryJob
from app.models.template import SqlTemplate, TemplateVersion
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER
from app.schemas.common import ParamDef
from app.schemas.template import EnumSqlIn, PublishIn, TemplateCreateIn, TemplateUpdateIn, TestRunIn
from app.services import (
    credential_service,
    enum_cache_service,
    notify_service,
    permission_service,
    query_service,
    template_service,
)
from tests.conftest import assert_never_public, latest_audit

pytestmark = pytest.mark.usefixtures("clean_credentials")

SQL = "SELECT c FROM o WHERE d = :d"

# ID 段 8640–8646
ADMIN, A_ADMIN, A_AUTHOR, B_ADMIN, BIZ = 8640, 8641, 8642, 8643, 8644





@pytest.fixture
def ds(db, datasource_factory):
    return datasource_factory("tid-mysql")


@pytest.fixture
def admin(db, user_factory):
    return user_factory(ADMIN, ROLE_ADMIN, "平台管理员", prefix="tid")


@pytest.fixture
def a_admin(db, user_factory):
    return user_factory(A_ADMIN, ROLE_DEVELOPER, "甲队团队管理员", prefix="tid")


@pytest.fixture
def author(db, user_factory):
    return user_factory(A_AUTHOR, ROLE_DEVELOPER, "甲队作者", prefix="tid")


@pytest.fixture
def b_admin(db, user_factory):
    return user_factory(B_ADMIN, ROLE_DEVELOPER, "乙队团队管理员", prefix="tid")


@pytest.fixture
def biz(db, user_factory):
    return user_factory(BIZ, ROLE_USER, "业务使用者", prefix="tid")




@pytest.fixture
def team_a(db, a_admin, author, team_factory):
    return team_factory("tid-team-A", [(a_admin, True), (author, False)])


@pytest.fixture
def team_b(db, b_admin, author, team_factory):
    """作者**同时**属于乙队:用来证明取数身份跟着任务走,而不是跟着作者的其它团队漂移。"""
    return team_factory("tid-team-B", [(b_admin, True), (author, False)])


@pytest.fixture
def ready(db, ds, team_a, team_b, team_credential):
    """两队都在同一个数据源上配好并测通账号 —— 用不同的用户名以便断言用了哪一个。"""
    team_credential(team_a, ds, username="teamA_acct")
    team_credential(team_b, ds, username="teamB_acct")


def _published(db, author, ds, team, *, name) -> SqlTemplate:
    tmpl = create_template(
        TemplateCreateIn(
            name=name, team_id=team.id, datasource_id=ds.id, sql_text=SQL,
            params=[ParamDef(name="d", kind="single", label="日期")],
        ),
        db, author, ip=None,
    )
    publish(tmpl.id, PublishIn(note="上线"), db, author, ip=None)
    return tmpl


def _grant_run(db, tmpl, user, granter):
    permission_service.grant(
        db, subject_type="user", subject_id=str(user.id),
        resource_type="template", resource_id=str(tmpl.id),
        actions=["view", "run", "download"], granted_by=granter.id,
    )


# ---------------------------------------------------------------- 正式取数


def test_run_uses_the_tasks_team_account(
    db, ds, author, biz, team_a, team_b, ready, spy_connector, monkeypatch
):
    """**核心断言**:业务用户跑甲队的任务,用的是甲队的账号。

    不是发起人(业务用户根本没有库账号)、不是作者的另一个团队乙队的账号、
    也不是数据源的公共账号。
    """
    seen = spy_connector()
    monkeypatch.setattr(query_service.settings, "RUN_INLINE", True)
    tmpl = _published(db, author, ds, team_a, name="tid-甲队任务")
    _grant_run(db, tmpl, biz, author)

    job = query_service.enqueue(db, biz, tmpl.id, {"d": "2026-08-01"})

    assert job.status == JOB_SUCCESS
    assert [c.username for c in seen] == ["teamA_acct"]
    assert "teamB_acct" not in [c.username for c in seen], "身份不该随作者的其它团队漂移"
    assert_never_public(seen)
    # 固化在运行记录上,审计才答得出「这次数据是哪个团队的账号取的」
    assert (job.run_as_team_id, job.run_as_username) == (team_a.id, "teamA_acct")
    assert latest_audit(db, ACTION_RUN_QUERY).detail["run_as"] == {
        "team_id": team_a.id,
        "db_username": "teamA_acct",
    }


def test_enqueue_preflight_names_the_team(db, ds, author, biz, team_a, spy_connector):
    """入队时就探一次身份:业务用户当场就知道该找谁,而不是排队半天再失败。"""
    spy_connector()
    # 先配好账号让任务能上线,再删掉账号模拟「团队管理员回收了凭证」
    cred, _changed = credential_service.upsert(
        db, team_id=team_a.id, datasource_id=ds.id,
        username="teamA_acct", password="pw", updated_by=A_ADMIN,
    )
    credential_service.verify(db, cred)
    tmpl = _published(db, author, ds, team_a, name="tid-账号被回收的任务")
    _grant_run(db, tmpl, biz, author)
    credential_service.delete(db, team_a.id, ds.id)

    with pytest.raises(CredentialRequiredError) as e:
        query_service.enqueue(db, biz, tmpl.id, {"d": "2026-08-01"})
    assert team_a.name in str(e.value) and ds.name in str(e.value)


def test_enqueue_block_notifies_team_admins(
    db, ds, author, biz, a_admin, team_a, ready, spy_connector
):
    """入队即被拦下时,**团队管理员照样要收到通知**。

    这是「账号没配/失效」的常见落点:worker 那条只在排队期间被改掉时才触发。
    少了这条,业务用户拿到一句「找团队管理员」就没下文了,而团队管理员毫不知情。
    通知不依赖运行记录行 —— 此时 job 还没建出来。
    """
    spy_connector()
    tmpl = _published(db, author, ds, team_a, name="tid-入队即被拦下")
    _grant_run(db, tmpl, biz, author)
    credential_service.delete(db, team_a.id, ds.id)
    since_note = db.scalar(select(func.max(Notification.id))) or 0

    with pytest.raises(CredentialRequiredError):
        query_service.enqueue(db, biz, tmpl.id, {"d": "2026-08-01"})

    db.expire_all()
    notes = db.scalars(select(Notification).where(Notification.id > since_note)).all()
    assert {n.user_id for n in notes} == {a_admin.id}, "只通知能修的人,发起人已同步拿到报错"
    assert notes[0].job_id is None and notes[0].template_id == tmpl.id


def test_credential_block_alert_is_deduped_within_the_cooldown(
    db, ds, author, biz, a_admin, team_a, ready, spy_connector
):
    """同一任务反复撞上「缺团队账号」,冷却窗内只告警一次。

    这条告警说的是一个**等人去修的状态**,不是一次性事件:账号配好之前,每一次运行都会再撞
    一次同一件事。开放 API 之后这不再是理论问题 —— 一个循环重试的 Agent 能在几分钟内把团队
    管理员的铃铛刷满,而那几十条指向的是同一个修法。
    """
    spy_connector()
    tmpl = _published(db, author, ds, team_a, name="tid-反复撞缺账号")
    _grant_run(db, tmpl, biz, author)
    credential_service.delete(db, team_a.id, ds.id)
    floor = db.scalar(select(func.max(Notification.id))) or 0

    for _ in range(3):
        with pytest.raises(CredentialRequiredError):
            query_service.enqueue(db, biz, tmpl.id, {"d": "2026-08-01"})

    db.expire_all()
    notes = db.scalars(select(Notification).where(Notification.id > floor)).all()
    assert [n.user_id for n in notes] == [a_admin.id], "三次运行撞的是同一件事,只该告警一次"

    # 冷却窗过去、状态仍没修好 ⇒ 重新告警。去重是「别刷屏」,不是「只提醒一次就算了」。
    notes[0].created_at -= timedelta(
        minutes=notify_service.CREDENTIAL_ALERT_COOLDOWN_MINUTES + 1
    )
    db.commit()
    with pytest.raises(CredentialRequiredError):
        query_service.enqueue(db, biz, tmpl.id, {"d": "2026-08-01"})

    db.expire_all()
    assert len(db.scalars(select(Notification).where(Notification.id > floor)).all()) == 2


def test_worker_rechecks_identity_and_notifies_team_admins(
    db, ds, author, biz, a_admin, team_a, ready, spy_connector, monkeypatch
):
    """worker 侧纵深防御:排队期间账号被回收 ⇒ 失败,且通知落到**团队管理员**。

    通知的是能修的人 —— 作者本人可能无权配团队账号。不通知他们,业务用户会一直干等。
    """
    spy_connector()
    monkeypatch.setattr(query_service.settings, "RUN_INLINE", False)
    tmpl = _published(db, author, ds, team_a, name="tid-排队期间被回收")
    _grant_run(db, tmpl, biz, author)
    job = query_service.enqueue(db, biz, tmpl.id, {"d": "2026-08-01"})

    credential_service.delete(db, team_a.id, ds.id)  # 排队期间凭证被回收
    since_note = db.scalar(select(func.max(Notification.id))) or 0
    query_service.execute_job(job.id)

    db.expire_all()
    job = db.get(QueryJob, job.id)
    assert job.status == JOB_FAILED
    notes = db.scalars(select(Notification).where(Notification.id > since_note)).all()
    recipients = {n.user_id for n in notes}
    assert biz.id in recipients, "发起人恒收"
    assert a_admin.id in recipients, "能修的人(团队管理员)也要收到"


def test_job_error_does_not_leak_the_team_db_username(
    db, ds, author, biz, team_a, ready, spy_connector, monkeypatch
):
    """`job.error` 是面向用户的(经 JobOut 展示、还会推进飞书通知),不能带库账号名。

    引擎的鉴权报错长这样 `Access denied for user 'teamA_acct'@...`,而业务用户根本不属于
    这个团队 —— Hive auth=NONE 下那个用户名就是完整凭证。
    审计 detail 反过来要**保留原文**:那是 admin-only 的取证面。
    """
    spy_connector(fail="Access denied for user 'teamA_acct'@'10.0.0.5' (using password: YES)")
    monkeypatch.setattr(query_service.settings, "RUN_INLINE", True)
    tmpl = _published(db, author, ds, team_a, name="tid-鉴权失败的任务")
    _grant_run(db, tmpl, biz, author)

    job = query_service.enqueue(db, biz, tmpl.id, {"d": "2026-08-01"})
    db.expire_all()
    job = db.get(QueryJob, job.id)

    assert job.status == JOB_FAILED
    assert "teamA_acct" not in (job.error or ""), "库账号名泄漏给了业务用户"
    assert "***" in job.error
    # 审计保留原文,否则查不出「哪个账号被拒了」
    assert "teamA_acct" in latest_audit(db, ACTION_RUN_QUERY_FAILED).detail["error"]


# ---------------------------------------------------------------- 枚举候选值


def test_enum_refresh_uses_the_tasks_team_account(
    db, ds, author, biz, team_a, ready, spy_connector
):
    """业务用户点「更新枚举值」跑的是任务里的 enum_sql —— 身份必须跟任务走。
    业务用户根本不是团队成员,没有自己的库账号。"""
    seen = spy_connector(rows=(("a",), ("b",)))
    tmpl = create_template(
        TemplateCreateIn(
            name="tid-枚举任务", team_id=team_a.id, datasource_id=ds.id,
            sql_text="SELECT * FROM o WHERE c IN (:cs)",
            params=[ParamDef(name="cs", kind="list", enum_sql="SELECT DISTINCT c FROM dim")],
        ),
        db, author, ip=None,
    )
    publish(tmpl.id, PublishIn(note="上线"), db, author, ip=None)
    ver = db.get(TemplateVersion, tmpl.published_version_id)
    pdef = ver.params[0]

    seen.clear()
    enum_cache_service.refresh(db, tmpl, pdef, biz)
    assert [c.username for c in seen] == ["teamA_acct"]
    assert_never_public(seen)


# ---------------------------------------------------------------- 编辑器试跑


def test_test_run_uses_the_selected_teams_account(
    db, ds, author, team_a, team_b, ready, spy_connector
):
    """试跑与正式取数**同一套身份**。作者同属两队,选甲队就用甲队的账号。

    这修掉了个人账号时代的一个真实缺陷:那时试跑用本人、正式取数用作者,
    「试跑通过」并不代表「上线后能跑」。
    """
    seen = spy_connector()
    template_service.test_run(
        db,
        TestRunIn(team_id=team_a.id, datasource_id=ds.id, sql_text="SELECT 1", values={}),
        author,
    )
    assert [c.username for c in seen] == ["teamA_acct"]

    seen.clear()
    template_service.test_run(
        db,
        TestRunIn(team_id=team_b.id, datasource_id=ds.id, sql_text="SELECT 1", values={}),
        author,
    )
    assert [c.username for c in seen] == ["teamB_acct"]
    assert_never_public(seen)


def test_test_run_refuses_a_team_you_are_not_in(db, ds, a_admin, team_b, ready, spy_connector):
    """非成员传别队的 team_id ⇒ 403,且**一次库都没连** —— 校验发生在取数之前。"""
    seen = spy_connector()
    with pytest.raises(PermissionDeniedError, match="不是团队"):
        template_service.test_run(
            db,
            TestRunIn(team_id=team_b.id, datasource_id=ds.id, sql_text="SELECT 1", values={}),
            a_admin,
        )
    assert seen == []


def test_test_run_records_the_team_on_the_job(
    db, ds, author, team_a, ready, spy_connector
):
    spy_connector()
    tmpl = _published(db, author, ds, team_a, name="tid-试跑留痕的任务")
    template_service.test_run(
        db,
        TestRunIn(
            team_id=team_a.id, datasource_id=ds.id, sql_text=SQL,
            params=[ParamDef(name="d", kind="single", label="日期")],
            values={"d": "2026-08-01"}, template_id=tmpl.id,
        ),
        author,
    )
    db.expire_all()
    job = db.scalars(
        select(QueryJob)
        .where(QueryJob.template_id == tmpl.id, QueryJob.source == SOURCE_TEST)
        .order_by(QueryJob.id.desc())
    ).first()
    assert job is not None
    assert (job.run_as_team_id, job.run_as_username) == (team_a.id, "teamA_acct")


def test_enum_sql_route_enforces_membership(db, ds, author, a_admin, team_a, team_b, ready, spy_connector):
    """测枚举 SQL 与试跑同理:成员放行、非成员 403。"""
    seen = spy_connector(rows=(("x",),))
    out = enum_sql(
        EnumSqlIn(team_id=team_a.id, datasource_id=ds.id, sql="SELECT DISTINCT c FROM dim"),
        db, author,
    )
    assert out.values == ["x"]
    assert [c.username for c in seen] == ["teamA_acct"]

    seen.clear()
    with pytest.raises(PermissionDeniedError, match="不是团队"):
        enum_sql(
            EnumSqlIn(team_id=team_b.id, datasource_id=ds.id, sql="SELECT DISTINCT c FROM dim"),
            db, a_admin,
        )
    assert seen == []


# ---------------------------------------------------------------- 上线卡点


def test_publish_gate_blocks_only_until_team_account_exists(
    db, ds, author, team_a, spy_connector
):
    """两段:未登记账号拦下 → 一登记就放行,**不要求先点「测试连接」**。

    卡点只拦「压根没有账号」:那时连都没得连。测通与否不拦 —— 否则团队管理员改完密码
    忘了点一下,该数据源上全团队的任务就集体下线,而测通也只代表那一刻连得上。
    """
    spy_connector()
    tmpl = create_template(
        TemplateCreateIn(
            name="tid-卡点任务", team_id=team_a.id, datasource_id=ds.id, sql_text=SQL,
            params=[ParamDef(name="d", kind="single", label="日期")],
        ),
        db, author, ip=None,
    )
    with pytest.raises(CredentialRequiredError, match="任务上线被拦下"):
        publish(tmpl.id, PublishIn(note="上线"), db, author, ip=None)

    cred, _ = credential_service.upsert(
        db, team_id=team_a.id, datasource_id=ds.id,
        username="teamA_acct", password="pw", updated_by=A_ADMIN,
    )
    assert cred.verified is False, "刻意不测:上线不该要求先测通"
    publish(tmpl.id, PublishIn(note="上线"), db, author, ip=None)
    db.refresh(tmpl)
    assert tmpl.published_version_id is not None


def test_editing_a_published_task_is_also_gated_and_writes_nothing(
    db, ds, author, team_a, ready, spy_connector
):
    """编辑已上线任务会让新版本自动接替上线 —— 那同样是一次上线,故同样要过卡点。

    关键:被拦下时**一行都不该写进库**(否则作者的编辑先 flush 出去、再被回滚掉,白丢输入)。
    """
    spy_connector()
    tmpl = _published(db, author, ds, team_a, name="tid-已上线再编辑")
    before_no = template_service.latest_version(db, tmpl.id).version_no

    credential_service.delete(db, team_a.id, ds.id)  # 账号被回收
    with pytest.raises(CredentialRequiredError, match="任务上线被拦下"):
        update_template(
            tmpl.id, TemplateUpdateIn(sql_text="SELECT 2"), db, author, ip=None
        )

    db.expire_all()
    assert template_service.latest_version(db, tmpl.id).version_no == before_no, (
        "上线卡点必须在写库之前生效"
    )

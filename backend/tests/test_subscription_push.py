"""补推:把一条已确认的运行结果推给订阅者,作为本期订阅结果。

重点钉三件事:
  1. 订阅者**真的看得到**补推来的那一期 —— 尤其是只有 view 授权的代订阅者,他们看不见
     操作人自己跑的那条记录,所以补推必须另建一条 subscribe 记录,且通知深链指向它;
  2. 替换本期不多算未看期数(看过任一版本都算看过本期);
  3. 资格校验与权限:本期内 / 当前版本 / 成功 / 正式取数 / 没推过 / 有编辑权。
"""
from datetime import datetime, timedelta

import pytest

from app.api.routes.query import load_job, preview_payload
from app.api.routes.subscriptions import push_job_to_subscribers as push_route
from app.api.routes.tasks import task_run_records
from app.core.config import settings
from app.core.exceptions import NotFoundError, PermissionDeniedError, RubicError
from app.models.audit import ACTION_TASK_SUBSCRIPTION_PUSH
from app.models.query_job import (
    JOB_FAILED,
    JOB_QUEUED,
    JOB_SUCCESS,
    SOURCE_SUBSCRIBE,
    SOURCE_TEST,
    QueryJob,
)
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER
from app.services import (
    permission_service,
    query_service,
    result_service,
    subscription_service,
    template_service,
)
from tests.conftest import (
    db_now,
    max_audit_id,
    new_notifications,
    note_floor,
    one_audit_row,
    subscription_row,
)

# ID 段 9480–9486
AUTHOR, MEMBER_SUB, OUTSIDER_SUB, EDITOR, ADMIN, TEAM_ADMIN, VIEWER = range(9480, 9487)


@pytest.fixture
def author(user_factory):
    return user_factory(AUTHOR, ROLE_DEVELOPER, "补推作者", prefix="psh")


@pytest.fixture
def member_sub(user_factory):
    return user_factory(MEMBER_SUB, ROLE_USER, "补推团队内订阅者", prefix="psh")


@pytest.fixture
def outsider_sub(user_factory):
    """不在团队里、只有 view 授权的代订阅者 —— 最容易「收到通知却看不到」的那种人。"""
    return user_factory(OUTSIDER_SUB, ROLE_USER, "补推外部订阅者", prefix="psh")


@pytest.fixture
def editor(user_factory):
    """团队里的普通开发者,被单独授予了这个任务的编辑权。"""
    return user_factory(EDITOR, ROLE_DEVELOPER, "补推编辑人", prefix="psh")


@pytest.fixture
def admin(user_factory):
    return user_factory(ADMIN, ROLE_ADMIN, "补推平台管理员", prefix="psh")


@pytest.fixture
def team_admin(user_factory):
    return user_factory(TEAM_ADMIN, ROLE_DEVELOPER, "补推团队管理员", prefix="psh")


@pytest.fixture
def viewer(user_factory):
    """只有 view 授权、没订阅的人:能看任务,但无权补推。"""
    return user_factory(VIEWER, ROLE_USER, "补推旁观者", prefix="psh")


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("psh-mysql")


@pytest.fixture
def team(db, ds, author, member_sub, editor, team_admin, team_factory, team_credential):
    t = team_factory(
        "psh-team",
        [(author, False), (member_sub, False), (editor, False), (team_admin, True)],
    )
    team_credential(t, ds, username="psh_team_acct")
    return t


@pytest.fixture
def task(db, author, ds, team, outsider_sub, member_sub, system_user, subscribed_task_factory):
    """已上线、每天 09:00 的订阅任务;团队内与团队外各一个订阅者,本期起点在一小时前。"""
    tmpl = subscribed_task_factory(author, ds, team, f"补推任务-{datetime.now():%H%M%S%f}")
    permission_service.grant(
        db, subject_type="user", subject_id=str(outsider_sub.id),
        resource_type="template", resource_id=str(tmpl.id),
        actions=["view"], granted_by=author.id,
    )
    subscription_service.subscribe(db, tmpl, member_sub)
    subscription_service.subscribe(db, tmpl, outsider_sub)
    _set_period_start(db, tmpl, datetime.now() - timedelta(hours=1))
    return tmpl


def _set_period_start(db, tmpl, at):
    """本期起点按**应用时钟**写(与 tick 一致);运行记录的 created_at 是库时钟 ——
    测试库 SQLite 的 CURRENT_TIMESTAMP 是 UTC,两边天然差一个时区,正好把换算钉住。"""
    sched = subscription_service.get_schedule(db, tmpl.id)
    sched.last_planned_at = at
    db.commit()


def _run(db, user, task, spy_connector, rows=((1,), (2,), (3,))):
    """操作人正常取数一次(走真实的入队 + 执行链,落一个真结果文件)。"""
    spy_connector(rows=rows)
    job = query_service.enqueue(db, user, task.id, {})
    query_service.execute_job(job.id)
    db.expire_all()
    return db.get(QueryJob, job.id)


def _push(db, user, task, job):
    return push_route(task.id, job.id, db=db, user=user, ip=None)


# ---------------------------------------------------------------- 失败后补推 + 可见性


def test_push_after_failed_period_reaches_and_is_visible_to_every_subscriber(
    db, author, member_sub, outsider_sub, task, system_user, spy_connector, subscribe_job_factory
):
    prev = subscribe_job_factory(task, with_file=True, created_at=db_now(db) - timedelta(days=1))
    subscribe_job_factory(task, status=JOB_FAILED)  # 本期定时运行失败
    source = _run(db, author, task, spy_connector)
    floor, audit_floor = note_floor(db), max_audit_id(db)

    out = _push(db, author, task, source)

    pushed = db.get(QueryJob, out["job"].id)
    assert out["replaced"] is False and out["notified"] == 2
    assert pushed.source == SOURCE_SUBSCRIBE and pushed.status == JOB_SUCCESS
    assert pushed.user_id == system_user.id  # 不是操作人:否则 can_access_job 会走本人短路
    assert pushed.pushed_by_id == author.id and pushed.pushed_from_job_id == source.id
    assert pushed.replaces_job_id is None
    assert pushed.result_object_key == source.result_object_key  # 共用文件,不重跑
    assert pushed.row_count == 3 and pushed.started_at is None and pushed.duration_ms is None
    db.expire_all()
    assert db.get(QueryJob, prev.id).superseded_at is not None  # 上一期照常被取代

    # 通知:两个订阅者都收到「已补发」,深链指向新记录(不是来源记录)
    notes = new_notifications(db, floor)
    assert sorted(n.user_id for n in notes) == sorted([member_sub.id, outsider_sub.id])
    for n in notes:
        assert n.title == "订阅数据已补发"
        # 站内通知的深链由前端按 (template_id, job_id) 拼出,飞书卡片同源(_records_link)
        assert n.job_id == pushed.id and n.template_id == task.id

    audit = one_audit_row(db, audit_floor)
    assert audit.action == ACTION_TASK_SUBSCRIPTION_PUSH and audit.user_id == author.id
    assert audit.detail["source_job_id"] == source.id and audit.detail["job_id"] == pushed.id
    assert audit.detail["notified"] == 2

    # 两类订阅者:列表看得到、单条取得到、能预览、能下载;预览即计入「看过本期」
    for sub in (member_sub, outsider_sub):
        listed = {j.id for j in task_run_records(task.id, db=db, user=sub)}
        assert pushed.id in listed, sub.name
        assert load_job(db, sub, pushed.id).id == pushed.id
        assert preview_payload(db, sub, pushed)["row_count"] == 3
        assert permission_service.can_download_job(db, sub, pushed)
        assert subscription_row(db, task, sub).last_consumed_job_id == pushed.id

    # 团队外订阅者在运行记录里列不到操作人自己跑的那条,也取不走它的文件 ——
    # 这正是必须另建订阅记录、而不是把来源记录直接发给他的原因
    listed = {j.id for j in task_run_records(task.id, db=db, user=outsider_sub)}
    assert source.id not in listed
    assert not permission_service.can_download_job(db, outsider_sub, source)


def test_run_records_show_who_pushed_and_offer_the_button_only_to_editors(
    db, author, outsider_sub, task, system_user, spy_connector
):
    source = _run(db, author, task, spy_connector)

    rows = {j.id: j for j in task_run_records(task.id, db=db, user=author)}
    assert rows[source.id].can_push is True and rows[source.id].push_hint is None

    pushed_id = _push(db, author, task, source)["job"].id

    rows = {j.id: j for j in task_run_records(task.id, db=db, user=author)}
    assert rows[pushed_id].pushed_by_name == author.name
    assert rows[pushed_id].pushed_from_job_id == source.id
    assert rows[source.id].can_push is False and "已经推送过" in rows[source.id].push_hint
    # 订阅者(无编辑权)一律拿不到按钮
    for j in task_run_records(task.id, db=db, user=outsider_sub):
        assert j.can_push is False and j.push_hint is None


# ---------------------------------------------------------------- 替换本期


def test_push_after_delivered_period_replaces_without_counting_a_miss(
    db, author, member_sub, outsider_sub, task, system_user, spy_connector, subscribe_job_factory
):
    first = subscribe_job_factory(task, with_file=True)  # 本期已经准时推过
    subscription_service.mark_consumed(db, member_sub.id, first)  # 团队内的人看过旧版
    source = _run(db, author, task, spy_connector)
    floor = note_floor(db)

    out = _push(db, author, task, source)

    pushed = db.get(QueryJob, out["job"].id)
    assert out["replaced"] is True and pushed.replaces_job_id == first.id
    db.expire_all()
    assert db.get(QueryJob, first.id).superseded_at is not None  # 旧版进入保留期
    assert subscription_row(db, task, member_sub).miss_streak == 0
    assert subscription_row(db, task, outsider_sub).miss_streak == 0  # 没看旧版也不 +1
    assert {n.title for n in new_notifications(db, floor)} == {"订阅数据已更新"}

    # 下一期定时成功:只看过旧版的人算看过本期,两版都没看的人才 +1
    nxt = subscribe_job_factory(task, with_file=True)
    subscription_service.settle_on_success(db, nxt)
    assert subscription_row(db, task, member_sub).miss_streak == 0
    assert subscription_row(db, task, outsider_sub).miss_streak == 1


def test_replacing_twice_still_points_at_the_first_version_of_the_period(
    db, author, editor, task, system_user, spy_connector, subscribe_job_factory
):
    permission_service.grant_edit(db, template_id=task.id, user_id=editor.id, granted_by=author.id)
    first = subscribe_job_factory(task, with_file=True)
    v2 = _push(db, author, task, _run(db, author, task, spy_connector))["job"]
    v3 = _push(db, editor, task, _run(db, editor, task, spy_connector))["job"]

    db.expire_all()
    assert db.get(QueryJob, v2.id).replaces_job_id == first.id
    assert db.get(QueryJob, v3.id).replaces_job_id == first.id
    assert db.get(QueryJob, v2.id).superseded_at is not None


# ---------------------------------------------------------------- 资格校验


def test_rejects_ineligible_records(db, author, task, system_user, spy_connector, subscribe_job_factory):
    def rejected(job, fragment, status=400):
        with pytest.raises(RubicError) as ei:
            _push(db, author, task, job)
        assert fragment in str(ei.value) and ei.value.status_code == status, str(ei.value)

    # 上一期的结果:早于本期计划时刻
    stale = _run(db, author, task, spy_connector)
    stale.created_at = db_now(db) - timedelta(hours=2)
    db.commit()
    rejected(stale, "早于本期计划时刻")

    # 试跑
    test_job = _run(db, author, task, spy_connector)
    test_job.source = SOURCE_TEST
    db.commit()
    rejected(test_job, "只能推送正式取数")

    # 失败的
    bad = _run(db, author, task, spy_connector)
    bad.status, bad.result_object_key = JOB_FAILED, None
    db.commit()
    rejected(bad, "运行成功")

    # 文件已不在盘上
    gone = _run(db, author, task, spy_connector)
    gone.result_object_key = "jobs/nope/missing.csv"
    db.commit()
    rejected(gone, "已过期")

    # 已经推过的
    ok = _run(db, author, task, spy_connector)
    _push(db, author, task, ok)
    rejected(ok, "已经推送过", status=409)

    # 本期定时运行还在排队
    queued = subscribe_job_factory(task, status=JOB_QUEUED)
    rejected(_run(db, author, task, spy_connector), "正在进行", status=409)
    queued.status = JOB_FAILED
    db.commit()


def test_rejects_result_from_an_older_version(db, author, task, spy_connector):
    old = _run(db, author, task, spy_connector)
    old.template_version_id = None  # 不是当前上线版本跑出来的
    db.commit()
    with pytest.raises(RubicError) as ei:
        _push(db, author, task, old)
    assert "旧版本" in str(ei.value)


def test_rejects_when_task_has_no_subscribers_or_is_offline(
    db, author, member_sub, outsider_sub, task, spy_connector
):
    source = _run(db, author, task, spy_connector)
    subscription_service.unsubscribe(db, task.id, member_sub)
    subscription_service.unsubscribe(db, task.id, outsider_sub)
    with pytest.raises(RubicError) as ei:
        _push(db, author, task, source)
    assert "没有订阅者" in str(ei.value)

    subscription_service.subscribe(db, task, member_sub)
    template_service.archive(db, task)
    with pytest.raises(RubicError) as ei:
        _push(db, author, task, source)
    assert "未上线" in str(ei.value)


# ---------------------------------------------------------------- 权限


def test_everyone_with_edit_rights_can_push(
    db, author, editor, admin, team_admin, task, spy_connector
):
    permission_service.grant_edit(db, template_id=task.id, user_id=editor.id, granted_by=author.id)
    for who in (author, editor, admin, team_admin):
        source = _run(db, author, task, spy_connector)
        assert _push(db, who, task, source)["job"].pushed_by_id == who.id


def test_subscribers_and_viewers_cannot_push(
    db, author, member_sub, outsider_sub, viewer, task, spy_connector
):
    permission_service.grant(
        db, subject_type="user", subject_id=str(viewer.id),
        resource_type="template", resource_id=str(task.id),
        actions=["view", "run"], granted_by=author.id,
    )
    source = _run(db, author, task, spy_connector)
    for who in (member_sub, outsider_sub, viewer):
        with pytest.raises(PermissionDeniedError):
            _push(db, who, task, source)


def test_record_of_another_task_is_not_found(db, author, ds, team, task, subscribed_task_factory, spy_connector):
    other = subscribed_task_factory(author, ds, team, f"补推别的任务-{datetime.now():%H%M%S%f}")
    foreign = _run(db, author, other, spy_connector)
    with pytest.raises(NotFoundError):
        _push(db, author, task, foreign)


# ---------------------------------------------------------------- 文件保留


def test_shared_file_outlives_the_source_records_retention(
    db, author, outsider_sub, task, system_user, spy_connector
):
    source = _run(db, author, task, spy_connector)
    pushed = db.get(QueryJob, _push(db, author, task, source)["job"].id)

    # 来源记录过了常规保留期:它自己显示过期,但文件受订阅记录保护,订阅者照常取得到
    source.created_at = db_now(db) - timedelta(days=settings.RESULT_RETENTION_DAYS + 1)
    db.commit()
    assert source.result_expired
    assert source.result_object_key in subscription_service.protected_result_keys()
    assert not result_service.is_gone(pushed)
    assert preview_payload(db, outsider_sub, pushed)["row_count"] == 3


def test_run_records_tell_the_confirm_dialog_who_and_whether_it_replaces(
    db, author, task, system_user, spy_connector, subscribe_job_factory
):
    source = _run(db, author, task, spy_connector)
    row = {j.id: j for j in task_run_records(task.id, db=db, user=author)}[source.id]
    assert row.push_subscriber_count == 2 and row.push_replaces is False

    subscribe_job_factory(task, with_file=True)  # 本期已经推过一版
    row = {j.id: j for j in task_run_records(task.id, db=db, user=author)}[source.id]
    assert row.push_replaces is True


def test_push_is_a_delivery_not_an_execution_in_analytics(
    db, author, admin, task, subscribe_job_factory, spy_connector
):
    """补推记录不算一次执行:否则失败的那一期补推后,定时运行成功率会被报成 50%。"""
    from app.core.timewindow import Window
    from app.models.query_job import SOURCE_SUBSCRIBE as SUB
    from app.services import analytics_service

    scope = analytics_service.resolve_scope(db, admin)
    window = Window(datetime.now() - timedelta(days=30), datetime.now() + timedelta(days=1))
    subscribe_job_factory(task, status=JOB_FAILED)
    source = _run(db, author, task, spy_connector)
    before = analytics_service.health(db, scope, window)["by_source"][SUB]

    _push(db, author, task, source)

    assert analytics_service.health(db, scope, window)["by_source"][SUB] == before

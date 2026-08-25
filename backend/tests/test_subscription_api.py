"""订阅接口层:订阅资格守卫、幂等、名单/留痕守卫、任务列表的订阅标记。

与仓库既有测试同风格:直接调路由函数,不起 TestClient。
"""
import pytest
from sqlalchemy import select

from app.api.routes.subscriptions import (
    subscribe_task,
    task_subscribers,
    task_subscription_events,
    unsubscribe_task,
)
from app.api.routes.tasks import list_tasks
from app.core.exceptions import PermissionDeniedError, RubicError
from app.models import audit as A
from app.models.permission import ACTION_VIEW, RESOURCE_TEMPLATE, SUBJECT_USER
from app.models.user import ROLE_DEVELOPER, ROLE_USER
from app.services import permission_service, subscription_service, template_service
from tests.conftest import max_audit_id, new_audit_rows

# ID 段 9210–9213
AUTHOR, MEMBER, VIEWER, OUTSIDER = 9210, 9211, 9212, 9213


@pytest.fixture
def author(user_factory):
    return user_factory(AUTHOR, ROLE_DEVELOPER, "接口作者", prefix="sapi")


@pytest.fixture
def member(user_factory):
    return user_factory(MEMBER, ROLE_DEVELOPER, "普通队员", prefix="sapi")


@pytest.fixture
def viewer(user_factory):
    return user_factory(VIEWER, ROLE_USER, "被授权业务", prefix="sapi")


@pytest.fixture
def outsider(user_factory):
    return user_factory(OUTSIDER, ROLE_USER, "无关路人", prefix="sapi")


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("sapi-mysql")


@pytest.fixture
def team(db, ds, author, member, team_factory, team_credential):
    t = team_factory("sapi-team", [(author, True), (member, False)])
    team_credential(t, ds, username="sapi_team_acct")
    return t


def _grant_view(db, tmpl, user):
    permission_service.grant(
        db, subject_type=SUBJECT_USER, resource_type=RESOURCE_TEMPLATE,
        resource_id=str(tmpl.id), actions=[ACTION_VIEW],
        granted_by=None, subject_id=str(user.id),
    )


# ---------------------------------------------------------------- 订阅/退订


def test_outsider_cannot_subscribe(db, author, outsider, ds, team, subscribed_task_factory):
    tmpl = subscribed_task_factory(author, ds, team, at_time="08:00", name="sapi-守卫")
    with pytest.raises(PermissionDeniedError):
        subscribe_task(tmpl.id, db=db, user=outsider, ip=None)


def test_subscribe_requires_enabled_schedule(db, author, member, ds, team, subscribed_task_factory):
    tmpl = subscribed_task_factory(author, ds, team, at_time="08:00", name="sapi-未开订阅", enabled=False)
    with pytest.raises(RubicError):
        subscribe_task(tmpl.id, db=db, user=member, ip=None)


def test_subscribe_requires_published(db, author, member, ds, team, subscribed_task_factory):
    tmpl = subscribed_task_factory(author, ds, team, at_time="08:00", name="sapi-草稿", publish=False)
    with pytest.raises(PermissionDeniedError):  # can_subscribe = can_view 且已上线
        subscribe_task(tmpl.id, db=db, user=member, ip=None)


def test_granted_viewer_can_subscribe_idempotently_with_audit(db, author, viewer, ds, team, subscribed_task_factory):
    tmpl = subscribed_task_factory(author, ds, team, at_time="08:00", name="sapi-授权订阅")
    _grant_view(db, tmpl, viewer)
    floor = max_audit_id(db)

    assert subscribe_task(tmpl.id, db=db, user=viewer, ip=None) == {"ok": True, "created": True}
    assert subscribe_task(tmpl.id, db=db, user=viewer, ip=None) == {"ok": True, "created": False}

    rows = [r for r in new_audit_rows(db, floor) if r.action == A.ACTION_TASK_SUBSCRIBE]
    assert len(rows) == 1  # 幂等重订不再记审计


def test_unsubscribe_is_idempotent_and_needs_no_permission(db, author, viewer, ds, team, subscribed_task_factory):
    """权限被撤的人更应该退得出去 —— 退订不设资格守卫,只动本人的行。"""
    tmpl = subscribed_task_factory(author, ds, team, at_time="08:00", name="sapi-退订")
    _grant_view(db, tmpl, viewer)
    subscribe_task(tmpl.id, db=db, user=viewer, ip=None)
    # 模拟权限被撤:直接清掉授权行
    from app.models.permission import Permission

    db.query(Permission).filter_by(subject_id=str(viewer.id)).delete()
    db.commit()

    assert unsubscribe_task(tmpl.id, db=db, user=viewer, ip=None)["removed"] is True
    assert unsubscribe_task(tmpl.id, db=db, user=viewer, ip=None)["removed"] is False


# ---------------------------------------------------------------- 名单与留痕


def test_subscribers_listing_requires_edit_rights(db, author, member, viewer, ds, team, subscribed_task_factory):
    tmpl = subscribed_task_factory(author, ds, team, at_time="08:00", name="sapi-名单")
    _grant_view(db, tmpl, viewer)
    subscribe_task(tmpl.id, db=db, user=viewer, ip=None)

    out = task_subscribers(tmpl.id, db=db, user=author)  # 作者(团队管理员)可看
    assert out.threshold >= 1
    assert [i.user_id for i in out.items] == [viewer.id]
    assert out.items[0].miss_streak == 0

    # 普通队员既非作者/团队管理员、也没被授 edit → 看不了
    with pytest.raises(PermissionDeniedError):
        task_subscribers(tmpl.id, db=db, user=member)
    with pytest.raises(PermissionDeniedError):
        task_subscription_events(tmpl.id, db=db, user=viewer)


def test_events_trail_is_descending_and_labeled(db, author, viewer, ds, team, subscribed_task_factory):
    tmpl = subscribed_task_factory(author, ds, team, at_time="08:00", name="sapi-留痕")
    _grant_view(db, tmpl, viewer)
    subscribe_task(tmpl.id, db=db, user=viewer, ip=None)
    unsubscribe_task(tmpl.id, db=db, user=viewer, ip=None)
    subscribe_task(tmpl.id, db=db, user=viewer, ip=None)

    events = task_subscription_events(tmpl.id, db=db, user=author)

    assert [e.action for e in events] == ["subscribe", "unsubscribe", "subscribe"]  # 倒序
    assert events[0].id > events[1].id > events[2].id
    assert events[1].action_label == "退订"
    assert all(e.user_id == viewer.id and e.operator_id == viewer.id for e in events)


# ---------------------------------------------------------------- 任务列表标记


def test_list_tasks_carries_subscription_facts(db, author, member, viewer, outsider, ds, team, subscribed_task_factory):
    tmpl = subscribed_task_factory(author, ds, team, at_time="08:00", name="sapi-列表标记")
    _grant_view(db, tmpl, viewer)
    subscribe_task(tmpl.id, db=db, user=viewer, ip=None)

    def row_for(user):
        rows = list_tasks(db=db, user=user)
        return next((r for r in rows if r.id == tmpl.id), None)

    mine = row_for(viewer)
    assert mine.subscribe_enabled is True
    assert mine.schedule_desc == "每天 08:00"
    assert mine.subscribed is True
    assert mine.subscriber_count == 1
    assert mine.can_subscribe is True

    teammate = row_for(member)  # 团队内部人:可订但还没订
    assert teammate.subscribed is False and teammate.can_subscribe is True

    assert row_for(outsider) is None  # 无关路人根本看不到该任务

    # 计划关闭后 can_subscribe 熄灭(订阅行保留与否是另一回事,这里只看标记)
    sched = subscription_service.get_schedule(db, tmpl.id)
    sched.enabled = False
    db.commit()
    assert row_for(member).can_subscribe is False
    assert row_for(member).subscribe_enabled is False

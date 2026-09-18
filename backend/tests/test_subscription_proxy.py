"""代订阅:把任务订阅给业务方(含自动补 view 授权)、从名单里移除订阅者。

与仓库既有测试同风格:直接调路由函数,不起 TestClient。
"""
import pytest
from sqlalchemy import select

from app.api.routes.subscriptions import (
    remove_task_subscriber,
    subscribe_task,
    subscribe_task_for,
    task_subscribers,
    unsubscribe_task,
)
from app.core.exceptions import BatchRejectedError, PermissionDeniedError, RubicError
from app.models import audit as A
from app.models.permission import ACTION_VIEW, RESOURCE_TEMPLATE, SUBJECT_USER, Permission
from app.models.subscription import (
    SUB_EVENT_ADDED,
    SUB_EVENT_REMOVED,
    TaskSubscription,
    TaskSubscriptionEvent,
)
from app.models.user import ROLE_DEVELOPER, ROLE_USER, User
from app.schemas.permission import SubjectIn
from app.schemas.template import SubscribeForIn
from app.services import permission_service
from tests.conftest import max_audit_id, new_audit_rows, new_notifications, note_floor

# ID 段 9220–9226
AUTHOR, MEMBER, BIZ, INSIDER, LEAVER, OTHER_BIZ = 9220, 9221, 9222, 9223, 9224, 9225


@pytest.fixture
def author(user_factory):
    return user_factory(AUTHOR, ROLE_DEVELOPER, "代订阅作者", prefix="sfor")


@pytest.fixture
def member(user_factory):
    return user_factory(MEMBER, ROLE_DEVELOPER, "普通队员", prefix="sfor")


@pytest.fixture
def biz(user_factory):
    """团队外、零权限的业务方 —— 代订阅最典型的目标。"""
    return user_factory(BIZ, ROLE_USER, "业务方甲", prefix="sfor")


@pytest.fixture
def other_biz(user_factory):
    return user_factory(OTHER_BIZ, ROLE_USER, "业务方乙", prefix="sfor")


@pytest.fixture
def leaver(db, user_factory):
    u = user_factory(LEAVER, ROLE_USER, "已离职", prefix="sfor")
    u.is_active = False
    db.commit()
    return u


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("sfor-mysql")


@pytest.fixture
def team(db, ds, author, member, team_factory, team_credential):
    t = team_factory("sfor-team", [(author, True), (member, False)])
    team_credential(t, ds, username="sfor_team_acct")
    return t


@pytest.fixture
def tmpl(author, ds, team, subscribed_task_factory):
    """已上线、开着订阅计划的无参数任务 —— 除了两个专门测「草稿」「未开订阅」的用例,
    其余全用它。"""
    return subscribed_task_factory(author, ds, team, name="sfor-任务")


def _add(db, tmpl, *users, by):
    """代订阅一批人。"""
    return subscribe_task_for(
        tmpl.id,
        SubscribeForIn(subjects=[SubjectIn(subject_id=str(u.id)) for u in users]),
        db=db, user=by, ip=None,
    )


def _perm_rows(db, tmpl, user, action=None) -> int:
    """这个人在这个任务上有几条授权行;action=None 时不限动作(用来断言「只补了 view」)。"""
    clauses = [
        Permission.subject_type == SUBJECT_USER,
        Permission.subject_id == str(user.id),
        Permission.resource_type == RESOURCE_TEMPLATE,
        Permission.resource_id == str(tmpl.id),
    ]
    if action is not None:
        clauses.append(Permission.action == action)
    return len(list(db.scalars(select(Permission).where(*clauses))))


def _events(db, tmpl, action) -> list[TaskSubscriptionEvent]:
    return list(
        db.scalars(
            select(TaskSubscriptionEvent).where(
                TaskSubscriptionEvent.template_id == tmpl.id,
                TaskSubscriptionEvent.action == action,
            )
        )
    )


# ---------------------------------------------------------------- 代订阅主路径


def test_proxy_subscribe_grants_view_and_creates_row(db, author, biz, tmpl):
    """零权限的业务方被代订阅:订阅行 + 恰好一条 view 授权 + 一条留痕。"""
    floor = note_floor(db)

    out = _add(db, tmpl, biz, by=author)

    assert out.created == [biz.id] and out.skipped == [] and out.granted_view == [biz.id]
    # 只补 view,不顺手给 run/download
    assert _perm_rows(db, tmpl, biz, ACTION_VIEW) == 1
    assert _perm_rows(db, tmpl, biz) == 1
    sub = db.scalar(
        select(TaskSubscription).where(
            TaskSubscription.template_id == tmpl.id, TaskSubscription.user_id == biz.id
        )
    )
    assert sub is not None and sub.added_by == author.id
    ev = _events(db, tmpl, SUB_EVENT_ADDED)
    assert len(ev) == 1
    assert ev[0].user_id == biz.id and ev[0].operator_id == author.id
    assert ev[0].detail == {"granted_view": True}
    # 补了权就订得上 —— can_subscribe 此刻对他成立
    assert permission_service.can_subscribe(permission_service.team_scope(db, biz), tmpl)
    # 本人收到一条「已为你订阅」
    notes = [n for n in new_notifications(db, floor) if n.user_id == biz.id]
    assert len(notes) == 1 and "订阅" in notes[0].title


def test_proxy_subscribe_insider_does_not_grant_redundant_view(db, author, member, tmpl):
    """同团队成员本就 can_view,不该给他塞一条冗余授权行。"""
    out = _add(db, tmpl, member, by=author)

    assert out.created == [member.id] and out.granted_view == []
    assert _perm_rows(db, tmpl, member) == 0


def test_proxy_subscribe_skips_existing_subscriber(db, author, biz, tmpl):
    """已在名单里的人算跳过、不算失败,也不重复留痕(同自助订阅的幂等)。"""
    _add(db, tmpl, biz, by=author)
    floor = max_audit_id(db)

    out = _add(db, tmpl, biz, by=author)

    assert out.created == [] and out.skipped == [biz.id]
    assert len(_events(db, tmpl, SUB_EVENT_ADDED)) == 1
    assert new_audit_rows(db, floor) == []


def test_proxy_subscribe_multiple_shares_batch_id(db, author, biz, other_biz, tmpl):
    floor = max_audit_id(db)

    out = _add(db, tmpl, biz, other_biz, by=author)

    assert sorted(out.created) == sorted([biz.id, other_biz.id])
    rows = [r for r in new_audit_rows(db, floor) if r.action == A.ACTION_TASK_SUBSCRIBE_FOR]
    assert len(rows) == 2
    assert len({r.detail["batch_id"] for r in rows}) == 1
    assert {r.detail["batch_size"] for r in rows} == {2}
    # 补的 view 各记一条普通授权,来源在 detail 里标着
    grants = [r for r in new_audit_rows(db, floor) if r.action == A.ACTION_PERMISSION_GRANT]
    assert len(grants) == 2 and {g.detail["via"] for g in grants} == {"subscribe_for"}


def test_subscribers_listing_shows_proxy_origin(db, author, biz, other_biz, tmpl):
    """名单上直接读得出「自助订阅」还是「由谁代订」。"""
    _add(db, tmpl, biz, by=author)
    permission_service.grant_view(db, template_id=tmpl.id, user_id=other_biz.id, granted_by=None)
    db.commit()  # grant_view 不提交,跟随调用方事务
    subscribe_task(tmpl.id, db=db, user=other_biz, ip=None)

    rows = {i.user_id: i for i in task_subscribers(tmpl.id, db=db, user=author).items}
    assert rows[biz.id].added_by == author.id
    assert rows[biz.id].added_by_name == author.name
    assert rows[other_biz.id].added_by is None and rows[other_biz.id].added_by_name is None


# ---------------------------------------------------------------- 拒绝口径


def test_proxy_subscribe_requires_edit_rights(db, author, member, biz, tmpl):
    with pytest.raises(PermissionDeniedError):
        _add(db, tmpl, biz, by=member)


def test_proxy_subscribe_rejects_draft_task(db, author, biz, ds, team, subscribed_task_factory):
    """草稿任务:补了 view 也照样 can_view=False(published 是它的前提),必须前置拦下,
    否则会造出一批「有权限却永远收不到推送」的僵尸订阅行。"""
    tmpl = subscribed_task_factory(author, ds, team, name="sfor-草稿", publish=False)
    with pytest.raises(RubicError, match="尚未上线"):
        _add(db, tmpl, biz, by=author)
    assert _perm_rows(db, tmpl, biz) == 0
    assert _events(db, tmpl, SUB_EVENT_ADDED) == []


def test_proxy_subscribe_rejects_schedule_off(db, author, biz, ds, team, subscribed_task_factory):
    tmpl = subscribed_task_factory(author, ds, team, name="sfor-未开订阅", enabled=False)
    with pytest.raises(RubicError, match="未开启订阅"):
        _add(db, tmpl, biz, by=author)


def test_proxy_subscribe_is_all_or_nothing(db, author, biz, leaver, tmpl):
    """一个人不合格 → 整批不生效:在职的那个也没被写进去。"""
    with pytest.raises(BatchRejectedError) as exc:
        _add(db, tmpl, biz, leaver, by=author)

    assert [r["code"] for r in exc.value.rejections] == ["inactive"]
    assert exc.value.rejections[0]["label"] == leaver.name  # 行首标签是人不是任务
    assert leaver.name in str(exc.value)
    assert task_subscribers(tmpl.id, db=db, user=author).items == []
    assert _perm_rows(db, tmpl, biz) == 0


def test_proxy_subscribe_rejects_system_scheduler(db, author, system_user, tmpl):
    with pytest.raises(BatchRejectedError) as exc:
        _add(db, tmpl, system_user, by=author)
    assert [r["code"] for r in exc.value.rejections] == ["system_user"]


def test_proxy_subscribe_rejects_over_limit(db, author, biz, tmpl):
    from app.schemas.template import SUBSCRIBE_FOR_LIMIT

    data = SubscribeForIn(
        subjects=[SubjectIn(subject_id=str(biz.id))] * (SUBSCRIBE_FOR_LIMIT + 1)
    )
    with pytest.raises(RubicError, match="分批"):
        subscribe_task_for(tmpl.id, data, db=db, user=author, ip=None)


# ---------------------------------------------------------------- 移除订阅者


def test_remove_subscriber_writes_event_and_keeps_view_grant(db, author, biz, tmpl):
    """移除只取消推送,**不收回查看权** —— 收权有它自己的入口。"""
    _add(db, tmpl, biz, by=author)
    floor, nfloor = max_audit_id(db), note_floor(db)

    assert remove_task_subscriber(tmpl.id, biz.id, db=db, user=author, ip=None) == {
        "ok": True, "removed": True
    }

    assert task_subscribers(tmpl.id, db=db, user=author).items == []
    assert _perm_rows(db, tmpl, biz, ACTION_VIEW) == 1  # 授权行仍在
    ev = _events(db, tmpl, SUB_EVENT_REMOVED)
    assert len(ev) == 1 and ev[0].user_id == biz.id and ev[0].operator_id == author.id
    rows = [r for r in new_audit_rows(db, floor) if r.action == A.ACTION_TASK_UNSUBSCRIBE_FOR]
    assert len(rows) == 1 and rows[0].detail["target_user_id"] == biz.id
    assert [n.user_id for n in new_notifications(db, nfloor)] == [biz.id]


def test_remove_subscriber_is_idempotent_and_silent(db, author, biz, tmpl):
    _add(db, tmpl, biz, by=author)
    remove_task_subscriber(tmpl.id, biz.id, db=db, user=author, ip=None)
    floor, nfloor = max_audit_id(db), note_floor(db)

    assert remove_task_subscriber(tmpl.id, biz.id, db=db, user=author, ip=None)["removed"] is False

    assert new_audit_rows(db, floor) == []  # 什么都没发生就不留一条误导性的撤销
    assert new_notifications(db, nfloor) == []


def test_remove_subscriber_requires_edit_rights(db, author, member, biz, tmpl):
    _add(db, tmpl, biz, by=author)
    with pytest.raises(PermissionDeniedError):
        remove_task_subscriber(tmpl.id, biz.id, db=db, user=member, ip=None)


def test_removed_user_can_resubscribe_himself(db, author, biz, tmpl):
    """被移除后自助入口不受影响:view 还在,他随时能自己订回来。"""
    _add(db, tmpl, biz, by=author)
    remove_task_subscriber(tmpl.id, biz.id, db=db, user=author, ip=None)

    assert subscribe_task(tmpl.id, db=db, user=biz, ip=None) == {"ok": True, "created": True}
    # 自助订回来的那行不再带代订阅来源
    row = [i for i in task_subscribers(tmpl.id, db=db, user=author).items if i.user_id == biz.id]
    assert row[0].added_by is None
    assert unsubscribe_task(tmpl.id, db=db, user=biz, ip=None)["removed"] is True


# ---------------------------------------------------------------- 与既有机制的交互


def test_proxy_subscriber_is_auto_unsubscribed_like_anyone_else(
    db, author, biz, ds, tmpl, system_user
):
    """「连续未消费自动退订」对代订阅的人一视同仁 —— 表里两种来源的行本就没有差别,
    这个用例把那条口径钉住,免得日后有人给代订阅开豁免。"""
    from app.core.config import settings
    from app.models.query_job import JOB_SUCCESS, SOURCE_SUBSCRIBE, QueryJob
    from app.models.subscription import SUB_EVENT_AUTO_UNSUBSCRIBE
    from app.services import subscription_service

    _add(db, tmpl, biz, by=author)

    def _job():
        job = QueryJob(
            user_id=system_user.id, template_id=tmpl.id, datasource_id=ds.id,
            params={}, status=JOB_SUCCESS, source=SOURCE_SUBSCRIBE,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job

    prev = _job()
    row = db.scalar(
        select(TaskSubscription).where(
            TaskSubscription.template_id == tmpl.id, TaskSubscription.user_id == biz.id
        )
    )
    row.miss_streak = settings.SUBSCRIPTION_MISS_LIMIT - 1
    row.created_at = prev.created_at  # 上期开跑前就已在册
    db.commit()

    removed = subscription_service.settle_on_success(db, _job())

    assert removed == [biz.id]
    assert _events(db, tmpl, SUB_EVENT_AUTO_UNSUBSCRIBE)


def test_proxy_subscribe_resolves_targets_by_open_id(db, author, biz, other_biz, tmpl):
    """前端挑通讯录候选时传的是 open_id(此刻可能还没落库),解析走批量口。

    两种主体混在一批里也要各归各位 —— 这条路径重构成「先批量预取、再内存配对」之后,
    最容易错的就是两个分支的配对。
    """
    out = subscribe_task_for(
        tmpl.id,
        SubscribeForIn(
            subjects=[
                SubjectIn(subject_open_id=biz.feishu_open_id, subject_name="改过的名"),
                SubjectIn(subject_id=str(other_biz.id)),
            ]
        ),
        db=db, user=author, ip=None,
    )

    assert sorted(out.created) == sorted([biz.id, other_biz.id])
    # 解析到既有用户,没有造出第二个壳
    assert len(db.scalars(select(User).where(User.feishu_open_id == biz.feishu_open_id)).all()) == 1


def test_proxy_subscribe_dedups_the_same_person_picked_twice(db, author, biz, tmpl):
    """同一个人被两种形式各勾一次:只订一行、只记一条留痕。"""
    out = subscribe_task_for(
        tmpl.id,
        SubscribeForIn(
            subjects=[
                SubjectIn(subject_id=str(biz.id)),
                SubjectIn(subject_open_id=biz.feishu_open_id),
            ]
        ),
        db=db, user=author, ip=None,
    )

    assert out.created == [biz.id]
    assert len(_events(db, tmpl, SUB_EVENT_ADDED)) == 1

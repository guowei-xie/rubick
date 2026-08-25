"""订阅与任务生命周期的卡点:开订阅的任务不能有变量(创建/编辑双向)、
显式关订阅放行变量并清退+通知、下线暂停通知、离队连带退订。
"""
import pytest
from sqlalchemy import select

from app.core.exceptions import RubicError
from app.models.notification import Notification
from app.models.query_job import JOB_SUCCESS, SOURCE_SUBSCRIBE, QueryJob
from app.models.subscription import (
    SUB_EVENT_CLOSED_UNSUBSCRIBE,
    SUB_EVENT_MEMBER_REMOVED,
    TaskSubscription,
    TaskSubscriptionEvent,
)
from app.models.user import ROLE_DEVELOPER, ROLE_USER
from app.schemas.common import ParamDef
from app.schemas.template import (
    SubscriptionScheduleIn,
    TemplateCreateIn,
    TemplateUpdateIn,
)
from app.services import team_service, template_service

# ID 段 9230–9232
AUTHOR, SUB_A, SUB_B = 9230, 9231, 9232


@pytest.fixture
def author(user_factory):
    return user_factory(AUTHOR, ROLE_DEVELOPER, "卡点作者", prefix="sgd")


@pytest.fixture
def sub_a(user_factory):
    return user_factory(SUB_A, ROLE_USER, "卡点订阅者甲", prefix="sgd")


@pytest.fixture
def sub_b(user_factory):
    # 开发者角色:离队用例要走 team_service.remove_member(它只对成员生效)
    return user_factory(SUB_B, ROLE_DEVELOPER, "卡点订阅者乙", prefix="sgd")


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("sgd-mysql")


@pytest.fixture
def team(db, ds, author, sub_a, sub_b, team_factory, team_credential):
    t = team_factory("sgd-team", [(author, True), (sub_a, False), (sub_b, False)])
    team_credential(t, ds, username="sgd_team_acct")
    return t


def _subs(db, template_id):
    db.expire_all()
    return list(
        db.scalars(select(TaskSubscription).where(TaskSubscription.template_id == template_id))
    )


# ---------------------------------------------------------------- 变量互斥卡点


def test_create_with_subscription_and_params_is_rejected(db, author, ds, team):
    with pytest.raises(RubicError, match="变量"):
        template_service.create_template(
            db, author,
            TemplateCreateIn(
                name="sgd-带参开订", team_id=team.id, datasource_id=ds.id,
                sql_text="SELECT 1 WHERE d = :d",
                params=[ParamDef(name="d", kind="single")],
                subscription=SubscriptionScheduleIn(enabled=True, freq="daily", at_time="09:00"),
            ),
        )


def test_adding_params_to_subscribed_task_is_rejected(db, author, sub_a, ds, team, subscribed_task_factory):
    """已开订阅(且有订阅者)的任务编辑时新增变量 → 阻止保存,订阅者一个不少。"""
    tmpl = subscribed_task_factory(author, ds, team, "sgd-加参被拒", subscribers=[sub_a])

    with pytest.raises(RubicError, match="订阅"):
        template_service.add_version(
            db, author, tmpl,
            TemplateUpdateIn(
                sql_text="SELECT 1 WHERE d = :d",
                params=[ParamDef(name="d", kind="single")],
            ),
        )
    assert len(_subs(db, tmpl.id)) == 1  # 没有静默清退


def test_net_state_guard_holds_without_subscription_payload(db, author, sub_a, ds, team, subscribed_task_factory):
    """本次保存不带 subscription 字段(维持现状=开启)时,加变量同样被拒。"""
    tmpl = subscribed_task_factory(author, ds, team, "sgd-缺省净状态", subscribers=[sub_a])
    with pytest.raises(RubicError, match="订阅"):
        template_service.add_version(
            db, author, tmpl,
            TemplateUpdateIn(
                sql_text="SELECT 1 WHERE d = :d",
                params=[ParamDef(name="d", kind="single")],
                subscription=None,
            ),
        )


def test_explicit_disable_clears_subscribers_and_allows_params(db, author, sub_a, ds, team, system_user, subscribed_task_factory):
    """显式 enabled=False 视为「先关订阅」:清退+留痕+通知本人,变量随之放行,
    最新一期订阅结果盖 superseded_at(回落常规保留期)。"""
    tmpl = subscribed_task_factory(author, ds, team, "sgd-关订放行", subscribers=[sub_a])
    last = QueryJob(
        user_id=system_user.id, template_id=tmpl.id, datasource_id=ds.id,
        params={}, status=JOB_SUCCESS, source=SOURCE_SUBSCRIBE,
        result_object_key="jobs/x/last.csv",
    )
    db.add(last)
    db.commit()

    version = template_service.add_version(
        db, author, tmpl,
        TemplateUpdateIn(
            sql_text="SELECT 1 WHERE d = :d",
            params=[ParamDef(name="d", kind="single")],
            subscription=SubscriptionScheduleIn(enabled=False, freq="daily", at_time="09:00"),
        ),
    )

    assert version.params[0]["name"] == "d"
    assert _subs(db, tmpl.id) == []
    ev = db.scalar(
        select(TaskSubscriptionEvent).where(
            TaskSubscriptionEvent.template_id == tmpl.id,
            TaskSubscriptionEvent.action == SUB_EVENT_CLOSED_UNSUBSCRIBE,
        )
    )
    assert ev is not None and ev.user_id == sub_a.id and ev.operator_id == author.id
    db.expire_all()
    assert db.get(QueryJob, last.id).superseded_at is not None
    note = db.scalar(
        select(Notification)
        .where(Notification.user_id == sub_a.id, Notification.template_id == tmpl.id)
        .order_by(Notification.id.desc()).limit(1)
    )
    assert note is not None and "取消" in note.title


# ---------------------------------------------------------------- 下线/离队


def test_archive_notifies_subscribers_but_keeps_them(db, author, sub_a, ds, team, subscribed_task_factory):
    tmpl = subscribed_task_factory(author, ds, team, "sgd-下线暂停", subscribers=[sub_a])

    template_service.archive(db, tmpl)

    assert len(_subs(db, tmpl.id)) == 1  # 订阅关系保留,重新上线自动恢复
    note = db.scalar(
        select(Notification)
        .where(Notification.user_id == sub_a.id, Notification.template_id == tmpl.id)
        .order_by(Notification.id.desc()).limit(1)
    )
    assert note is not None and "暂停" in note.title


def test_member_removal_drops_subscription_with_trail(db, author, sub_b, ds, team, subscribed_task_factory):
    """离队连带退订(与连带撤编辑权同一取舍),留 member_removed 事件。"""
    tmpl = subscribed_task_factory(author, ds, team, "sgd-离队清订", subscribers=[sub_b])

    team_service.remove_member(db, team, sub_b.id)

    assert _subs(db, tmpl.id) == []
    ev = db.scalar(
        select(TaskSubscriptionEvent).where(
            TaskSubscriptionEvent.template_id == tmpl.id,
            TaskSubscriptionEvent.user_id == sub_b.id,
            TaskSubscriptionEvent.action == SUB_EVENT_MEMBER_REMOVED,
        )
    )
    assert ev is not None and ev.detail["team_id"] == team.id

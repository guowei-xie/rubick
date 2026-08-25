"""订阅消费结算:下载/预览推水位、下一期成功时结算(归零/+1/达阈清退)、
失败期不结算、后订阅者不计、被清退者不再收成功通知、worker 全链路走通。
"""
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from app.api.routes.query import preview as preview_route
from app.core.config import settings
from app.models.audit import ACTION_TASK_AUTO_UNSUBSCRIBE, AuditLog
from app.models.notification import Notification
from app.models.query_job import (
    JOB_FAILED,
    JOB_SUCCESS,
    SOURCE_SUBSCRIBE,
    QueryJob,
)
from app.models.subscription import (
    SUB_EVENT_AUTO_UNSUBSCRIBE,
    TaskSubscription,
    TaskSubscriptionEvent,
)
from app.models.user import ROLE_DEVELOPER, ROLE_USER
from app.services import (
    notify_service,
    query_service,
    result_service,
    subscription_service,
)

# ID 段 9220–9222
AUTHOR, SUB_A, SUB_B = 9220, 9221, 9222


@pytest.fixture
def author(user_factory):
    return user_factory(AUTHOR, ROLE_DEVELOPER, "结算作者", prefix="stl")


@pytest.fixture
def sub_a(user_factory):
    return user_factory(SUB_A, ROLE_USER, "结算订阅者甲", prefix="stl")


@pytest.fixture
def sub_b(user_factory):
    return user_factory(SUB_B, ROLE_USER, "结算订阅者乙", prefix="stl")


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("stl-mysql")


@pytest.fixture
def team(db, ds, author, sub_a, sub_b, team_factory, team_credential):
    t = team_factory("stl-team", [(author, True), (sub_a, False), (sub_b, False)])
    team_credential(t, ds, username="stl_team_acct")
    return t


@pytest.fixture
def task(db, author, ds, team, subscribed_task_factory):
    return subscribed_task_factory(author, ds, team, "结算任务")


def _sub_job(db, system_user, task, ds, *, status=JOB_SUCCESS, with_file=False):
    """造一条订阅运行记录;with_file 时真的落一个结果 CSV(下载/预览用例需要)。"""
    job = QueryJob(
        user_id=system_user.id, template_id=task.id, datasource_id=ds.id,
        params={}, status=status, source=SOURCE_SUBSCRIBE,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    if with_file:
        filename = f"{task.name}_{job.id}.csv"
        key = f"jobs/{job.id}/{filename}"
        result_service.upload_csv(key, b"\xef\xbb\xbfc\n1\n")
        job.result_object_key = key
        job.result_filename = filename
        job.row_count = 1
        db.commit()
    return job


def _sub_row(db, task, user) -> TaskSubscription | None:
    db.expire_all()
    return db.scalar(
        select(TaskSubscription).where(
            TaskSubscription.template_id == task.id, TaskSubscription.user_id == user.id
        )
    )


# ---------------------------------------------------------------- 消费打点


def test_download_marks_consumption(db, author, sub_a, ds, team, task, system_user):
    """打点在路由层(与 preview 并排),故从 download 路由进。"""
    from app.api.routes.query import download as download_route

    subscription_service.subscribe(db, task, sub_a)
    job = _sub_job(db, system_user, task, ds, with_file=True)
    fake_request = type("R", (), {"headers": {}, "client": None})()

    download_route(job.id, fake_request, db=db, user=sub_a)

    assert _sub_row(db, task, sub_a).last_consumed_job_id == job.id


def test_preview_route_marks_consumption(db, author, sub_a, ds, team, task, system_user):
    subscription_service.subscribe(db, task, sub_a)
    job = _sub_job(db, system_user, task, ds, with_file=True)

    preview_route(job.id, db=db, user=sub_a)

    assert _sub_row(db, task, sub_a).last_consumed_job_id == job.id


def test_watermark_only_moves_forward_and_ignores_non_subscribe(db, author, sub_a, ds, team, task, system_user):
    subscription_service.subscribe(db, task, sub_a)
    early = _sub_job(db, system_user, task, ds)
    late = _sub_job(db, system_user, task, ds)

    subscription_service.mark_consumed(db, sub_a.id, late)
    subscription_service.mark_consumed(db, sub_a.id, early)  # 回拨无效
    assert _sub_row(db, task, sub_a).last_consumed_job_id == late.id

    # 非订阅 job / 未成功 job 一律不动水位
    normal = QueryJob(user_id=sub_a.id, template_id=task.id, datasource_id=ds.id,
                      params={}, status=JOB_SUCCESS)
    db.add(normal)
    failed = _sub_job(db, system_user, task, ds, status=JOB_FAILED)
    db.commit()
    subscription_service.mark_consumed(db, sub_a.id, normal)
    subscription_service.mark_consumed(db, sub_a.id, failed)
    assert _sub_row(db, task, sub_a).last_consumed_job_id == late.id


# ---------------------------------------------------------------- 结算


def test_settle_resets_consumer_and_increments_misser(db, author, sub_a, sub_b, ds, team, task, system_user):
    subscription_service.subscribe(db, task, sub_a)
    subscription_service.subscribe(db, task, sub_b)
    prev = _sub_job(db, system_user, task, ds)
    subscription_service.mark_consumed(db, sub_a.id, prev)  # 甲消费了上一期,乙没有
    cur = _sub_job(db, system_user, task, ds)

    removed = subscription_service.settle_on_success(db, cur)

    assert removed == []
    assert _sub_row(db, task, sub_a).miss_streak == 0
    assert _sub_row(db, task, sub_b).miss_streak == 1
    db.expire_all()
    assert db.get(QueryJob, prev.id).superseded_at is not None  # 上一期被取代
    assert db.get(QueryJob, cur.id).superseded_at is None


def test_settle_first_period_is_noop(db, author, sub_a, ds, team, task, system_user):
    subscription_service.subscribe(db, task, sub_a)
    first = _sub_job(db, system_user, task, ds)
    assert subscription_service.settle_on_success(db, first) == []
    assert _sub_row(db, task, sub_a).miss_streak == 0


def test_failed_period_does_not_settle(db, author, sub_a, ds, team, task, system_user):
    """失败的期不触发结算:notify_job_done 的失败分支不动 miss_streak、不盖 superseded_at。"""
    subscription_service.subscribe(db, task, sub_a)
    prev = _sub_job(db, system_user, task, ds)
    failed = _sub_job(db, system_user, task, ds, status=JOB_FAILED)
    failed.error = "目标库超时"
    db.commit()

    notify_service.notify_job_done(db, failed, RuntimeError("目标库超时"))

    assert _sub_row(db, task, sub_a).miss_streak == 0
    db.expire_all()
    assert db.get(QueryJob, prev.id).superseded_at is None


def test_late_subscriber_is_not_counted_for_previous_period(db, author, sub_a, ds, team, task, system_user):
    prev = _sub_job(db, system_user, task, ds)
    subscription_service.subscribe(db, task, sub_a)
    # 把订阅时间明确推到上一期之后(server_default 秒级粒度,同秒会判成同时)
    db.execute(
        update(TaskSubscription)
        .where(TaskSubscription.template_id == task.id, TaskSubscription.user_id == sub_a.id)
        .values(created_at=prev.created_at + timedelta(seconds=60))
    )
    db.commit()
    cur = _sub_job(db, system_user, task, ds)

    subscription_service.settle_on_success(db, cur)

    assert _sub_row(db, task, sub_a).miss_streak == 0  # 那期不算他的


def test_reaching_limit_auto_unsubscribes_with_trail_and_no_success_notice(
    db, author, sub_a, sub_b, ds, team, task, system_user
):
    """达到阈值:删订阅行 + 留痕事件 + 审计 + 通知本人;紧随其后的成功通知不再发给他。"""
    subscription_service.subscribe(db, task, sub_a)
    subscription_service.subscribe(db, task, sub_b)
    prev = _sub_job(db, system_user, task, ds)
    subscription_service.mark_consumed(db, sub_b.id, prev)  # 乙一直在看
    row = _sub_row(db, task, sub_a)
    row.miss_streak = settings.SUBSCRIPTION_MISS_LIMIT - 1  # 甲已连续错过 N-1 期
    db.commit()
    cur = _sub_job(db, system_user, task, ds, with_file=True)
    note_floor = db.scalar(select(func.max(Notification.id))) or 0

    # 从 notify_job_done 入口进:「先结算、再发成功通知」的顺序由它保证
    notify_service.notify_job_done(db, cur)

    # 甲被清退:订阅行没了,事件留痕 + 审计都在
    assert _sub_row(db, task, sub_a) is None
    ev = db.scalar(
        select(TaskSubscriptionEvent).where(
            TaskSubscriptionEvent.template_id == task.id,
            TaskSubscriptionEvent.user_id == sub_a.id,
            TaskSubscriptionEvent.action == SUB_EVENT_AUTO_UNSUBSCRIBE,
        )
    )
    assert ev is not None and ev.detail["miss_streak"] == settings.SUBSCRIPTION_MISS_LIMIT
    audit = db.scalar(
        select(AuditLog).where(AuditLog.action == ACTION_TASK_AUTO_UNSUBSCRIBE)
        .order_by(AuditLog.id.desc()).limit(1)
    )
    assert audit is not None and audit.detail["target_user_id"] == sub_a.id

    notes = list(db.scalars(select(Notification).where(Notification.id > note_floor)))
    a_titles = [n.title for n in notes if n.user_id == sub_a.id]
    b_titles = [n.title for n in notes if n.user_id == sub_b.id]
    assert any("自动取消" in t for t in a_titles), a_titles
    assert not any("已生成" in t for t in a_titles), "被清退者不该再收成功通知"
    assert any("已生成" in t for t in b_titles), b_titles


def test_scheduled_success_notifies_only_subscribers_never_the_system_user(
    db, author, sub_a, ds, team, task, system_user
):
    """2026-08-25 现场:一次成功的定时运行,「取数完成」落在了系统用户「定时运行」名下,
    订阅者一条都没收到 —— 那台执行它的进程是旧代码,没走 subscribe 分派。

    钉死两件事:收件人**只有订阅者**,且**任何通知都不会落到系统用户名下**
    (后者由 _push 的出口兜底,即便上游又把发起人当成收件人)。
    """
    subscription_service.subscribe(db, task, sub_a)
    job = _sub_job(db, system_user, task, ds, with_file=True)
    note_floor = db.scalar(select(func.max(Notification.id))) or 0

    notify_service.notify_job_done(db, job)

    notes = list(db.scalars(select(Notification).where(Notification.id > note_floor)))
    assert [n.user_id for n in notes] == [sub_a.id], [(n.user_id, n.title) for n in notes]
    assert "已生成" in notes[0].title
    assert not any(n.user_id == system_user.id for n in notes)


def test_push_drops_notifications_addressed_to_the_system_user(db, task, system_user):
    """出口兜底本身:系统用户登录不进来,给它的通知没有任何人会读到,所以直接丢弃。"""
    note_floor = db.scalar(select(func.max(Notification.id))) or 0
    assert (
        notify_service._push(
            db, user_id=system_user.id, title="取数完成", body="不该存在",
            level="success", link="http://x/", job_id=None, template_id=task.id,
        )
        is None
    )
    assert db.scalar(select(func.count(Notification.id)).where(Notification.id > note_floor)) == 0


# ---------------------------------------------------------------- worker 全链路


def test_scheduled_job_executes_end_to_end(db, author, sub_a, ds, team, task, system_user, spy_connector):
    """enqueue_scheduled → execute_job → 成功通知,与人发起的取数共用同一条执行链。"""
    subscription_service.subscribe(db, task, sub_a)
    seen = spy_connector(rows=((1,), (2,)))
    job = query_service.enqueue_scheduled(db, task)

    query_service.execute_job(job.id)

    db.expire_all()
    got = db.get(QueryJob, job.id)
    assert got.status == JOB_SUCCESS
    assert got.source == SOURCE_SUBSCRIBE
    assert got.result_object_key
    # 取数身份 = 团队账号(绝不回退公共账号)
    assert [c.username for c in seen if c.is_team_account] and all(
        c.username != "public_acct" for c in seen
    )
    notes = list(
        db.scalars(
            select(Notification).where(
                Notification.user_id == sub_a.id, Notification.job_id == job.id
            )
        )
    )
    assert any("已生成" in n.title for n in notes), [n.title for n in notes]

"""订阅调度(subscription_service.tick):水位去重、无订阅者不建 job、下线暂停/上线恢复、
停机只补最近一期、缺取数账号走通知流。

tick 自管 Session(worker 请求外调用),用例断言前一律 db.expire_all()。
"""
from datetime import datetime

import pytest
from sqlalchemy import select

from app.models.credential import TeamDataSourceCredential
from app.models.notification import Notification
from app.models.query_job import JOB_QUEUED, SOURCE_SUBSCRIBE, QueryJob
from app.models.subscription import TaskSchedule
from app.models.user import ROLE_DEVELOPER, ROLE_USER
from app.services import subscription_service, template_service

# ID 段 9200–9202
AUTHOR, SUB_A, SUB_B = 9200, 9201, 9202

NOW = datetime(2026, 8, 24, 10, 0)  # 周一 10:00;每日 09:00 的计划此刻已到期


@pytest.fixture
def author(user_factory):
    return user_factory(AUTHOR, ROLE_DEVELOPER, "订阅作者", prefix="tick")


@pytest.fixture
def sub_a(user_factory):
    return user_factory(SUB_A, ROLE_USER, "订阅者甲", prefix="tick")


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("tick-mysql")


@pytest.fixture
def team(db, ds, author, sub_a, team_factory, team_credential):
    # 订阅者也放进团队:can_view 靠团队内部人身份成立
    t = team_factory("tick-team", [(author, True), (sub_a, False)])
    team_credential(t, ds, username="tick_team_acct")
    return t


def _sub_jobs(db, template_id):
    db.expire_all()
    return list(
        db.scalars(
            select(QueryJob).where(
                QueryJob.template_id == template_id,
                QueryJob.source == SOURCE_SUBSCRIBE,
            ).order_by(QueryJob.id)
        )
    )


def _sched(db, template_id) -> TaskSchedule:
    db.expire_all()
    return subscription_service.get_schedule(db, template_id)


def test_due_schedule_with_subscriber_fires_one_queued_job(db, author, sub_a, ds, team, system_user, subscribed_task_factory):
    tmpl = subscribed_task_factory(author, ds, team, "tick-基础")
    subscription_service.subscribe(db, tmpl, sub_a)

    # tick 扫的是**全库**计划,共享测试库里可能有别的文件留下的到期计划一并触发,
    # 故不断言全局 fired 数,只断言本任务范围内的事实
    fired = subscription_service.tick(NOW)

    assert fired >= 1
    jobs = _sub_jobs(db, tmpl.id)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.status == JOB_QUEUED
    assert job.source == SOURCE_SUBSCRIBE
    assert job.user_id == system_user.id  # 系统身份,不是作者也不是订阅者
    assert job.params == {}
    assert _sched(db, tmpl.id).last_planned_at == datetime(2026, 8, 24, 9, 0)


def test_same_period_never_fires_twice(db, author, sub_a, ds, team, system_user, subscribed_task_factory):
    tmpl = subscribed_task_factory(author, ds, team, "tick-去重")
    subscription_service.subscribe(db, tmpl, sub_a)

    subscription_service.tick(NOW)
    subscription_service.tick(NOW)  # 同一期重复扫描(等价于重复轮询/多实例竞争后的败者)

    assert len(_sub_jobs(db, tmpl.id)) == 1


def test_watermark_guard_blocks_stale_fire(db, author, sub_a, ds, team, system_user, subscribed_task_factory):
    """水位已推进到本期之后 → 原子 UPDATE 命不中,一个 job 都不建。"""
    tmpl = subscribed_task_factory(author, ds, team, "tick-水位闸")
    subscription_service.subscribe(db, tmpl, sub_a)
    sched = _sched(db, tmpl.id)
    sched.last_planned_at = datetime(2026, 8, 24, 9, 0)  # 已消化本期
    db.commit()

    subscription_service.tick(NOW)
    assert _sub_jobs(db, tmpl.id) == []


def test_no_subscriber_advances_watermark_without_job(db, author, ds, team, system_user, subscribed_task_factory):
    """无订阅者:该期不运行(需求 4),但水位照常推进 —— 几小时后有人订阅,
    不该突然「补跑」一期陈旧计划。"""
    tmpl = subscribed_task_factory(author, ds, team, "tick-无人订")

    subscription_service.tick(NOW)
    assert _sub_jobs(db, tmpl.id) == []
    assert _sched(db, tmpl.id).last_planned_at == datetime(2026, 8, 24, 9, 0)


def test_archived_task_pauses_and_republish_catches_up(db, author, sub_a, ds, team, system_user, subscribed_task_factory):
    """下线即暂停(不推水位、不建 job);重新上线后下一次 tick 补跑最近一期 —— 这就是
    「自动恢复」的具体形态。"""
    tmpl = subscribed_task_factory(author, ds, team, "tick-下线暂停")
    subscription_service.subscribe(db, tmpl, sub_a)
    template_service.archive(db, tmpl)

    subscription_service.tick(NOW)
    assert _sub_jobs(db, tmpl.id) == []
    assert _sched(db, tmpl.id).last_planned_at is None  # 暂停期间不消化任何期

    template_service.publish(db, tmpl, author, "重新上线")
    subscription_service.tick(NOW)
    assert len(_sub_jobs(db, tmpl.id)) == 1


def test_downtime_of_multiple_periods_backfills_only_latest(db, author, sub_a, ds, team, system_user, subscribed_task_factory):
    """停机 3 天的每日计划:只补最近一期(8/24 09:00),更早的直接作废 ——
    订阅结果按期覆盖,旧期没有消费价值。"""
    tmpl = subscribed_task_factory(author, ds, team, "tick-停机补跑")
    subscription_service.subscribe(db, tmpl, sub_a)
    sched = _sched(db, tmpl.id)
    sched.last_planned_at = datetime(2026, 8, 21, 9, 0)  # 最后消化到 3 天前
    db.commit()

    subscription_service.tick(NOW)
    assert len(_sub_jobs(db, tmpl.id)) == 1
    assert _sched(db, tmpl.id).last_planned_at == datetime(2026, 8, 24, 9, 0)


def test_missing_credential_notifies_fixers_and_subscribers(db, author, sub_a, ds, team, system_user, subscribed_task_factory):
    """到期但团队账号被删:不建 job;能修的人收「缺账号」提醒,订阅者收「生成失败」简讯,
    且该期水位已推进(失败不重试,下期照常)。"""
    tmpl = subscribed_task_factory(author, ds, team, "tick-缺账号")
    subscription_service.subscribe(db, tmpl, sub_a)
    db.query(TeamDataSourceCredential).filter_by(team_id=team.id, datasource_id=ds.id).delete()
    db.commit()

    subscription_service.tick(NOW)

    assert _sub_jobs(db, tmpl.id) == []
    assert _sched(db, tmpl.id).last_planned_at == datetime(2026, 8, 24, 9, 0)
    db.expire_all()
    notes = list(db.scalars(select(Notification).where(Notification.template_id == tmpl.id)))
    # 订阅者甲:一条「本期生成失败」简讯
    sub_titles = [n.title for n in notes if n.user_id == sub_a.id]
    assert any("生成失败" in t for t in sub_titles), sub_titles
    # 作者是团队管理员 = 能修的人:收到失败详情(以及缺账号提醒)
    author_titles = [n.title for n in notes if n.user_id == author.id]
    assert any("失败" in t or "阻断" in t for t in author_titles), author_titles

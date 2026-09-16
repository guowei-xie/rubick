"""任务列表的「闲置」口径:哪些任务算长期没人跑,以及这条口径的几个边界。

判定本身在 template_service.idle_days / is_idle,这里连着真实路由 list_tasks 一起验 ——
一条口径在服务层对、在路由层没接上,等于没做(同 test_task_team_scoping 的思路)。

口径里几条刻意的决定,每条都由一个用例钉住,免得以后被「顺手优化」掉:
  · 只判定**已上线**的任务(草稿还没给人用,已下线的已经下线了);
  · 从未运行过的从**创建时间**起算,不是空值 —— 「上线至今没人跑过」最该被看见;
  · 运行口径**含试跑与定时运行**,与卡片上显示的「最后运行」同一个时间。两份口径会让同一张卡
    一边写「最后运行 3 天前」一边写「闲置 167 天」;
  · TASK_IDLE_DAYS = 0 是关掉这项提示,不是「阈值 0 天 ⇒ 全都闲置」。
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import update

from app.api.routes.tasks import list_tasks
from app.api.routes.templates import archive, create_template, publish
from app.core.config import settings
from app.models.query_job import JOB_SUCCESS, SOURCE_RUN, SOURCE_SUBSCRIBE, SOURCE_TEST, QueryJob
from app.models.template import SqlTemplate
from app.models.user import ROLE_DEVELOPER
from app.schemas.template import PublishIn, TemplateCreateIn

pytestmark = pytest.mark.usefixtures("clean_credentials")

# 闲置只看「多久没运行」,与 SQL 怎么写、有没有变量无关 —— 用最小的无参 SQL,
# 免得每个用例都拖着一套跟被测口径无关的参数定义(同 conftest.subscribed_task_factory)
SQL = "SELECT 1"

# ID 段 9270–9271
AUTHOR, ADMIN = 9270, 9271

# 读默认值而不是抄一份:把 Settings 的默认改成 60 时,这些用例该跟着走,
# 而不是以一个与被测口径无关的理由红掉
THRESHOLD = settings.TASK_IDLE_DAYS


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("idle-mysql")


@pytest.fixture
def author(user_factory):
    return user_factory(AUTHOR, ROLE_DEVELOPER, "闲置用例作者", prefix="idle")


@pytest.fixture
def team(db, ds, author, team_factory, team_credential):
    t = team_factory("idle-team", [(author, True)])
    team_credential(t, ds, username="idle_acct")
    return t


def _task(db, author, ds, team, name, *, published=True, age_days=0):
    """建一个任务,可把**创建时间**回拨 age_days 天。

    created_at 有 server_default,插入时就被写死,所以只能建完再 UPDATE 回去
    —— 与 test_subscribe_retention._job 同一手法。
    """
    tmpl = create_template(
        TemplateCreateIn(name=name, team_id=team.id, datasource_id=ds.id, sql_text=SQL),
        db, author, ip=None,
    )
    if published:
        publish(tmpl.id, PublishIn(note="上线"), db, author, ip=None)
    if age_days:
        db.execute(
            update(SqlTemplate)
            .where(SqlTemplate.id == tmpl.id)
            .values(created_at=datetime.now() - timedelta(days=age_days))
        )
        db.commit()
    db.refresh(tmpl)
    return tmpl


def _run(db, user, ds, tmpl, *, age_days, source=SOURCE_RUN) -> None:
    """给任务补一条运行记录,时间回拨 age_days 天(同上,created_at 有 server_default)。"""
    job = QueryJob(
        user_id=user.id, template_id=tmpl.id, datasource_id=ds.id, params={},
        status=JOB_SUCCESS, source=source,
    )
    db.add(job)
    db.commit()
    db.execute(
        update(QueryJob)
        .where(QueryJob.id == job.id)
        .values(created_at=datetime.now() - timedelta(days=age_days))
    )
    db.commit()
    return job


def _row(db, actor, tmpl):
    """任务在某人的任务列表里的那一行。"""
    return next(r for r in list_tasks(db, actor) if r.id == tmpl.id)


# ---------------------------------------------------------------- 基本判定


def test_long_unrun_published_task_is_idle(db, ds, author, team):
    """已上线 + 最后一次运行在阈值之前 ⇒ 闲置,天数按最后那次运行算。"""
    tmpl = _task(db, author, ds, team, "idle-很久没跑", age_days=400)
    _run(db, author, ds, tmpl, age_days=THRESHOLD + 10)

    row = _row(db, author, tmpl)
    assert row.is_idle is True
    # 天数量的是**最后一次运行**,不是创建时间(任务建于 400 天前)
    assert row.idle_days == THRESHOLD + 10
    assert row.idle_threshold_days == THRESHOLD


def test_recently_run_task_is_not_idle(db, ds, author, team):
    """最近跑过就不闲置 —— 哪怕任务本身建于很久以前。"""
    tmpl = _task(db, author, ds, team, "idle-最近跑过", age_days=400)
    _run(db, author, ds, tmpl, age_days=3)

    row = _row(db, author, tmpl)
    assert row.is_idle is False
    assert row.idle_days == 3


def test_never_run_counts_from_created_at(db, ds, author, team):
    """从未运行过的从**创建时间**起算,而不是给个空值藏起来。

    「上线至今没人跑过」比「跑过但很久没跑」更该被看见 —— 它恰恰是最该被清理的那一种。
    """
    tmpl = _task(db, author, ds, team, "idle-从没跑过", age_days=THRESHOLD + 5)

    row = _row(db, author, tmpl)
    assert row.last_run_at is None
    assert row.idle_days == THRESHOLD + 5
    assert row.is_idle is True


# ---------------------------------------------------------------- 只判定已上线


def test_draft_never_idle(db, ds, author, team):
    """草稿不参与判定:它本来就还没给人用,标它「闲置」得不出任何下一步。"""
    tmpl = _task(db, author, ds, team, "idle-老草稿", published=False, age_days=400)

    row = _row(db, author, tmpl)
    assert row.idle_days is None
    assert row.is_idle is False


def test_archived_never_idle(db, ds, author, team):
    """已下线的也不参与判定:它已经在回收站里了,再标一次「该下线」是废话。"""
    tmpl = _task(db, author, ds, team, "idle-已下线", age_days=400)
    archive(tmpl.id, db, author, ip=None)

    row = _row(db, author, tmpl)
    assert row.idle_days is None
    assert row.is_idle is False


# ---------------------------------------------------------------- 运行口径


@pytest.mark.parametrize("source", [SOURCE_TEST, SOURCE_SUBSCRIBE])
def test_test_and_scheduled_runs_count_as_runs(db, ds, author, team, source):
    """试跑与定时运行**都算运行**。

    口径必须与卡片上显示的「最后运行」(last_run_at = MAX(QueryJob.created_at),含两者)
    完全一致 —— 另起一份「只算正式取数」会让同一张卡一边写「最后运行 3 天前」、
    一边写「闲置 167 天」。副作用也是对的:定时跑着的任务永远不闲置,它确实还在产出。
    """
    tmpl = _task(db, author, ds, team, f"idle-只有{source}", age_days=400)
    _run(db, author, ds, tmpl, age_days=3, source=source)

    row = _row(db, author, tmpl)
    assert row.last_run_at is not None
    assert row.is_idle is False


# ---------------------------------------------------------------- 阈值


def test_threshold_is_inclusive_boundary(db, ds, author, team, monkeypatch):
    """恰好等于阈值即闲置(>=),差一天则不是。"""
    monkeypatch.setattr(settings, "TASK_IDLE_DAYS", 10)
    on = _task(db, author, ds, team, "idle-正好卡线", age_days=10)
    off = _task(db, author, ds, team, "idle-差一天", age_days=9)

    assert _row(db, author, on).is_idle is True
    assert _row(db, author, off).is_idle is False


def test_threshold_zero_turns_the_hint_off(db, ds, author, team, monkeypatch):
    """TASK_IDLE_DAYS = 0 是**关掉这项提示**,不是「阈值 0 天 ⇒ 全都闲置」。"""
    monkeypatch.setattr(settings, "TASK_IDLE_DAYS", 0)
    tmpl = _task(db, author, ds, team, "idle-关掉提示", age_days=400)

    row = _row(db, author, tmpl)
    assert row.is_idle is False
    assert row.idle_days == 400  # 天数照算,只是不再判定为闲置
    # 阈值原样下发 0,前端据此整枚「闲置」筛选片都不渲染 —— 否则深链 ?idle=1 会渲染出
    # 一枚「0 闲置」、悬停写着「超过 0 天没有运行记录」,而功能其实是关的
    assert row.idle_threshold_days == 0


# ---------------------------------------------------------------- 给谁看是前端的事


def test_is_idle_does_not_depend_on_viewer(db, ds, author, team, user_factory):
    """服务端对谁都算同一个 is_idle:**给谁看是展示策略,不是权限**。

    前端只给 can_manage 的人显示(taskActions.showIdle)—— 与 credential_ready 完全同构。
    这里用平台管理员来看同一个任务(别团队的人看不到这个任务,根本拿不到这一行)。
    """
    from app.models.user import ROLE_ADMIN

    admin = user_factory(ADMIN, ROLE_ADMIN, "闲置用例管理员", prefix="idle")
    tmpl = _task(db, author, ds, team, "idle-口径与看客无关", age_days=THRESHOLD + 1)

    assert _row(db, author, tmpl).is_idle is True
    assert _row(db, admin, tmpl).is_idle is True

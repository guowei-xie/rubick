"""排队等待时长的来源:query_jobs.started_at。

`duration_ms` 只算**真正执行**的那一段。用户手册里那句「排了半小时、耗时写 12 秒,两个都对」
说的就是这件事 —— 而在 started_at 出现之前,前半句无处可查:created_at 是入队时刻,
updated_at 会被后续每一次状态流转覆盖(成功时再写一次、reclaim_stale_jobs 还会再写一次),
反推不出开始时刻。

这个文件钉住四条,每条都对应一种会让排队指标说谎的写法:
  · 盖章发生在 execute_job —— worker 路径与 RUN_INLINE 路径都经过它,一处就够;
  · 盖章**幂等** —— execute_job 会对已认领的 job 重入,无条件赋值会把开始时刻一路推后;
  · 试跑不盖章 —— 它同步执行、压根不入队,给它一个 0 会把排队分位数拉平;
  · 空值不是 0 —— queue_ms 对没有记录的行返回 None,而不是把历史全算成「零排队」。
"""
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from app import worker
from app.api.routes.templates import create_template, publish
from app.models.query_job import JOB_QUEUED, JOB_RUNNING, JOB_SUCCESS, SOURCE_TEST, QueryJob
from app.models.template import SqlTemplate
from app.models.user import ROLE_DEVELOPER, ROLE_USER
from app.schemas.common import ParamDef
from app.schemas.template import PublishIn, TemplateCreateIn, TestRunIn
from app.services import permission_service, query_service, template_service

pytestmark = pytest.mark.usefixtures("clean_credentials")

SQL = "SELECT c FROM o WHERE d = :d"

# ID 段 9300–9302
AUTHOR, T_ADMIN, BIZ = 9300, 9301, 9302


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("sat-mysql")


@pytest.fixture
def author(user_factory):
    return user_factory(AUTHOR, ROLE_DEVELOPER, "作者", prefix="sat")


@pytest.fixture
def t_admin(user_factory):
    return user_factory(T_ADMIN, ROLE_DEVELOPER, "团队管理员", prefix="sat")


@pytest.fixture
def biz(user_factory):
    return user_factory(BIZ, ROLE_USER, "业务使用者", prefix="sat")


@pytest.fixture
def team(db, author, t_admin, team_factory):
    return team_factory("sat-team", [(t_admin, True), (author, False)])


@pytest.fixture
def ready(db, ds, team, team_credential):
    team_credential(team, ds, username="sat_acct")


def _grant_run(db, tmpl, user, granter):
    permission_service.grant(
        db, subject_type="user", subject_id=str(user.id),
        resource_type="template", resource_id=str(tmpl.id),
        actions=["view", "run", "download"], granted_by=granter.id,
    )


@pytest.fixture
def tmpl(db, author, ds, team, ready) -> SqlTemplate:
    t = create_template(
        TemplateCreateIn(
            name="sat-任务", team_id=team.id, datasource_id=ds.id, sql_text=SQL,
            params=[ParamDef(name="d", kind="single", label="日期", test_value="2026-01-01")],
        ),
        db, author, ip=None,
    )
    publish(t.id, PublishIn(note="上线"), db, author, ip=None)
    return t


@pytest.fixture
def runnable(db, tmpl, biz, author):
    """业务使用者拿到该任务的 view/run/download —— 正式取数的前置。"""
    _grant_run(db, tmpl, biz, author)
    return tmpl


def test_execute_job_stamps_started_at(db, runnable, biz, spy_connector):
    """入队时还没有开始时刻,执行过后才有 —— 且不早于入队。"""
    spy_connector()
    job = query_service.enqueue(db, biz, runnable.id, {"d": "2026-01-01"})
    assert job.status == JOB_QUEUED
    assert job.started_at is None, "还在排队就有开始时刻,说明盖章盖早了"

    query_service.execute_job(job.id)
    db.refresh(job)

    assert job.status == JOB_SUCCESS
    assert job.started_at is not None
    assert job.started_at >= job.created_at
    assert job.queue_ms is not None and job.queue_ms >= 0


def test_stamp_is_idempotent_on_reentry(db, runnable, biz, spy_connector):
    """execute_job 对已认领(running)的 job 会重入 —— 重入不许把开始时刻推后。

    没有这条守卫,worker 认领与真正开跑之间每多一次重试,排队时长就被少算一截,
    而这个偏差永远是**朝着"看起来没排队"的方向**,最不容易被发现。
    """
    spy_connector()
    job = query_service.enqueue(db, biz, runnable.id, {"d": "2026-01-01"})
    # 模拟「一小时前入队、半小时前被 worker 认领并盖过章」:两个时刻一起往回推,
    # 只推 started_at 会让它早于 created_at,排队时长被夹成 0,反而测不出重入有没有覆盖
    now = db.scalar(select(func.now()))
    queued_at, earlier = now - timedelta(minutes=60), now - timedelta(minutes=30)
    db.execute(
        update(QueryJob).where(QueryJob.id == job.id)
        .values(status=JOB_RUNNING, created_at=queued_at, started_at=earlier)
    )
    db.commit()

    query_service.execute_job(job.id)
    db.refresh(job)

    assert job.status == JOB_SUCCESS
    # 允许毫秒级的存储精度差异,但绝不该被推到"刚刚"
    assert abs((job.started_at - earlier).total_seconds()) < 1
    assert job.queue_ms >= 30 * 60 * 1000 - 1000


def test_test_run_does_not_stamp(db, tmpl, author, spy_connector):
    """试跑同步执行、不入队 —— 没排过队就不该有排队记录。

    给它盖一个 created==started 的章看似"更完整",实则会往排队分位数里灌一堆 0,
    把真实的等待时间冲淡。排队统计因此一律排除 source='test',这里从源头上就留空。
    """
    spy_connector()
    template_service.test_run(
        db,
        TestRunIn(
            template_id=tmpl.id, team_id=tmpl.team_id, datasource_id=tmpl.datasource_id,
            sql_text=SQL,
            params=[ParamDef(name="d", kind="single", label="日期")],
            values={"d": "2026-01-01"},
        ),
        author,
    )

    job = db.scalars(
        select(QueryJob).where(QueryJob.template_id == tmpl.id, QueryJob.source == SOURCE_TEST)
        .order_by(QueryJob.id.desc())
    ).first()
    assert job is not None
    assert job.started_at is None
    assert job.queue_ms is None, "试跑不该报告排队时长"


def test_queue_ms_is_none_not_zero_without_a_stamp(db, ds, biz):
    """存量行(早于本列上线)的 queue_ms 必须是 None。

    兜底成 0 会让全部历史变成「零排队」—— 这个指标一上线就在说谎,而且看起来很健康,
    没人会去查。
    """
    job = QueryJob(
        user_id=biz.id, template_id=1, datasource_id=ds.id,
        params={}, status=JOB_SUCCESS, duration_ms=12_000,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    assert job.started_at is None
    assert job.queue_ms is None
    assert job.queue_ms != 0


def test_queue_ms_never_negative(db, ds, biz):
    """时钟回拨等极端情形下夹到 0,不抛也不给负数。"""
    now = db.scalar(select(func.now()))
    job = QueryJob(
        user_id=biz.id, template_id=1, datasource_id=ds.id, params={}, status=JOB_SUCCESS,
    )
    db.add(job)
    db.commit()
    db.execute(
        update(QueryJob).where(QueryJob.id == job.id)
        .values(created_at=now, started_at=now - timedelta(seconds=5))
    )
    db.commit()
    db.refresh(job)

    assert job.queue_ms == 0


def test_worker_path_also_stamps(db, runnable, biz, spy_connector):
    """走 worker 的执行路径(JobPool.submit → _run → execute_job),而不是直接调 execute_job。

    盖章刻意只放在 execute_job 里(worker 路径与 RUN_INLINE 路径都经过它,一处就够)——
    这条用例是那个决定的凭据:线上真实路径下这个 job **必须**拿到开始时刻,
    否则排队指标会是一整片空。

    刻意不走 worker.claim_and_submit:认领取的是**全库 id 最小的 queued 行**,而测试库是
    全套用例共用的,那样会抢到别的文件遗留的排队任务(隔离跑通过、全量跑失败)。
    认领本身已由 test_worker_queue 覆盖,这里要验的是它之后那一段。
    """
    spy_connector()
    job = query_service.enqueue(db, biz, runnable.id, {"d": "2026-01-01"})

    pool = worker.JobPool(1)
    try:
        assert pool.try_acquire()
        pool.submit(job.id)
    finally:
        pool.shutdown(wait=True)

    db.refresh(job)
    assert job.status == JOB_SUCCESS
    assert job.started_at is not None, "worker 执行后仍没有开始时刻"
    assert job.queue_ms is not None

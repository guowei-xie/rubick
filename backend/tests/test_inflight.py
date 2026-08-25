"""部署前的排空判据:「现在有没有正在跑的取数」。

守的坑:停进程会打断在跑的查询,而被打断的取数在业务侧是「运行中断,请重新运行」——
发起人可能已经等了半小时。deploy.sh 的 update/restart 据此决定「现在能不能停」,所以这个
判据错一点点都很贵:
- 把 queued 也算成「在跑」→ 排队一多就永远等不到零,部署被自己堵死;
- 把 running 漏掉 → 排空形同虚设,该拦的那次照样打断。
"""
import pytest
from sqlalchemy import update

from app import inflight
from app.models.query_job import JOB_FAILED, JOB_QUEUED, JOB_RUNNING, JOB_SUCCESS, QueryJob
from app.models.user import ROLE_DEVELOPER
from app.schemas.template import TemplateCreateIn
from app.services import query_service, template_service

# ID 段 9260
AUTHOR = 9260


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("inf-mysql")


@pytest.fixture
def author(user_factory):
    return user_factory(AUTHOR, ROLE_DEVELOPER, "排空用例作者", prefix="inf")


@pytest.fixture
def team(db, ds, author, team_factory, team_credential):
    t = team_factory("inf-team", [(author, True)])
    team_credential(t, ds, username="inf_team_acct")
    return t


@pytest.fixture
def task(db, author, ds, team):
    return template_service.create_template(
        db, author,
        TemplateCreateIn(
            name="排空用例任务", team_id=team.id, datasource_id=ds.id, sql_text="SELECT 1",
        ),
    )


@pytest.fixture(autouse=True)
def _running_baseline(db):
    """每个用例从「一个在跑的都没有」起步。

    这个判据本身是**全局**的(问的就是「整个平台现在有没有在跑的取数」),而库不按用例清理
    (见 conftest)—— 不抹掉上一个用例留下的 running 记录,退出码类断言就永远对不上。
    """
    db.execute(update(QueryJob).where(QueryJob.status == JOB_RUNNING).values(status=JOB_FAILED))
    db.commit()


def _job(db, author, ds, task, status: str) -> QueryJob:
    job = QueryJob(
        user_id=author.id, template_id=task.id, datasource_id=ds.id, params={}, status=status,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_only_running_jobs_count_as_inflight(db, ds, author, team, task):
    """queued / success / failed 都不算:重启只会让排队的多等一会儿,新 worker 照旧认领。"""
    running = _job(db, author, ds, task, JOB_RUNNING)
    for status in (JOB_QUEUED, JOB_SUCCESS, JOB_FAILED):
        _job(db, author, ds, task, status)

    ids = [row[0] for row in inflight.inflight(db)]

    assert ids == [running.id]


def test_inflight_says_which_task_and_who_started_it(db, ds, author, team, task):
    """报出来的东西要够操作人判断「值不值得等」:任务名、发起人、已经跑了多久。"""
    job = _job(db, author, ds, task, JOB_RUNNING)

    (jid, name, who, elapsed), = inflight.inflight(db)

    assert (jid, name, who) == (job.id, task.name, author.name)
    assert elapsed >= 0  # 库时钟算的,不掺本地时钟
    line = inflight.describe((jid, name, who, elapsed))
    assert task.name in line and author.name in line


def test_wait_returns_immediately_when_nothing_is_running(db, ds, author, team, task):
    _job(db, author, ds, task, JOB_QUEUED)

    assert inflight.wait_until_clear(timeout=0, interval=1, out=lambda *_a: None) is True


def test_wait_gives_up_instead_of_interrupting(db, ds, author, team, task):
    """等不到就返回 False —— **不打断**。deploy.sh 收到它就中止部署,线上留在旧版本上。"""
    _job(db, author, ds, task, JOB_RUNNING)

    said: list[str] = []
    assert inflight.wait_until_clear(timeout=0, interval=1, out=said.append) is False
    assert any("还在等" in s for s in said), "要说清在等什么,否则操作人只看到脚本卡住"


def test_default_timeout_is_the_same_ruler_as_orphan_reclaim(db):
    """等待上限与孤儿回收同一个口径:盖住最长可能的查询超时 + 缓冲。

    另编一个数的下场很具体:配了 6 小时超时的任务还在正常跑,而部署脚本已经等不下去了。
    """
    assert inflight.default_timeout_seconds() == query_service.stale_after_seconds(db)


def test_report_exit_code_tells_the_shell_whether_it_is_safe_to_stop(
    db, ds, author, team, task, capsys
):
    """deploy.sh 读的是退出码:0 = 可以安全停,3 = 有人在跑。"""
    assert inflight.main([]) == inflight.EXIT_CLEAR
    assert "可以安全停机" in capsys.readouterr().out

    _job(db, author, ds, task, JOB_RUNNING)
    assert inflight.main([]) == inflight.EXIT_BUSY
    assert task.name in capsys.readouterr().out


def test_exit_codes_avoid_1_so_a_crash_is_not_read_as_busy(db, ds, author, team, task):
    """三个码互不相同,而且**都不是 1** —— 1 是 Python 未捕获异常的默认码。

    混在一起的下场很具体:线上 config.ini 写错(或护栏拒绝启动)时命令崩了退 1,部署脚本
    把它读成「有任务在跑」,于是按一个不存在的理由白等一场再中止,真正的原因还在上面滚着。
    """
    codes = {inflight.EXIT_CLEAR, inflight.EXIT_WAIT_TIMEOUT, inflight.EXIT_BUSY}
    assert len(codes) == 3 and 1 not in codes

    _job(db, author, ds, task, JOB_RUNNING)
    assert inflight.main(["--wait", "--timeout", "0", "--interval", "1"]) == inflight.EXIT_WAIT_TIMEOUT

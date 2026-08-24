"""护栏:编辑器试跑的两道口子。

**F6 归属校验** —— test_run 原先直接 `db.get(SqlTemplate, data.template_id)` 就往上挂运行记录,
对这个任务**没有任何权限判定**(凭证走的是另一个字段 team_id,那头有成员校验)。于是甲队的
开发者能把一条 source=test、SQL 自己写的记录落到乙队某个任务名下,污染乙队的运行记录与审计面。
credential_service.for_team 的注释专门解释过「校验必须长在服务层,因为 team_id 是客户端传来的」
—— template_id 是同一个函数里漏掉的那一个。

**F5 超时口径** —— 试跑原先固定用 QUERY_TIMEOUT_SECONDS(120s),而正式取数走
query_service.effective_timeout(Hive 3600s,且尊重任务自己配的 timeout_seconds)。
credential.py 与 credential_service.py 反复承诺「试跑通过 = 上线后能跑」,这条承诺在超时这一维
上不成立:作者给任务配了 30 分钟,试跑照样 120 秒被砍,报错还只说「超时」不说是谁的上限。
"""
import pytest

from app.api.routes.templates import create_template
from app.core.exceptions import PermissionDeniedError, RubicError
from app.models.datasource import ENGINE_HIVE
from app.models.query_job import SOURCE_TEST, QueryJob
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER
from app.schemas.common import ParamDef
from app.schemas.template import TemplateCreateIn, TestRunIn
from app.services import template_service

pytestmark = pytest.mark.usefixtures("clean_credentials")

# ID 段 9130–9132
ADMIN, A_DEV, B_DEV = 9130, 9131, 9132
SQL = "SELECT c FROM o WHERE d = :d"
PARAMS = [ParamDef(name="d", kind="single", label="日期", test_value="2026-08-01")]


@pytest.fixture
def admin(user_factory):
    return user_factory(ADMIN, ROLE_ADMIN, "平台管理员", prefix="trg")


@pytest.fixture
def a_dev(user_factory):
    return user_factory(A_DEV, ROLE_DEVELOPER, "甲队开发者", prefix="trg")


@pytest.fixture
def b_dev(user_factory):
    return user_factory(B_DEV, ROLE_DEVELOPER, "乙队开发者", prefix="trg")


@pytest.fixture
def team_a(team_factory, a_dev):
    return team_factory("trg-team-A", [(a_dev, True)])


@pytest.fixture
def team_b(team_factory, b_dev):
    return team_factory("trg-team-B", [(b_dev, True)])


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("trg-mysql")


@pytest.fixture
def hive_ds(datasource_factory):
    return datasource_factory("trg-hive", engine=ENGINE_HIVE, port=10000)


@pytest.fixture
def ready(team_a, team_b, ds, hive_ds, team_credential):
    for team, name in ((team_a, "trgA_acct"), (team_b, "trgB_acct")):
        for d in (ds, hive_ds):
            team_credential(team, d, username=name)


@pytest.fixture
def task_b(db, b_dev, team_b, ds, ready):
    return create_template(
        TemplateCreateIn(
            name="trg-乙队的任务", team_id=team_b.id, datasource_id=ds.id,
            sql_text=SQL, params=PARAMS,
        ),
        db, b_dev, ip=None,
    )


class _TimeoutSpy:
    """记下每次 execute 拿到的 timeout_seconds —— conftest 的 spy_connector 只记身份。"""

    def __init__(self, monkeypatch, *, fail: str | None = None):
        self.seen: list[int] = []
        spy = self

        class Fake:
            def execute(self, sql, params=None, *, timeout_seconds, max_rows):
                spy.seen.append(timeout_seconds)
                if fail:
                    raise RuntimeError(fail)
                from app.connectors.base import QueryResult

                return QueryResult(columns=["c"], rows=[(1,)], meta={"duration_ms": 1})

        monkeypatch.setattr(template_service, "get_connector", lambda ds, cred: Fake())


# ---------------------------------------------------------------- F6 归属校验


def test_cannot_attach_test_run_to_another_teams_task(db, a_dev, team_a, ds, task_b, monkeypatch):
    """甲队开发者拿自己团队的账号,却想把记录挂到乙队的任务上 —— 必须拒绝。"""
    _TimeoutSpy(monkeypatch)
    with pytest.raises(PermissionDeniedError) as e:
        template_service.test_run(
            db,
            TestRunIn(
                team_id=team_a.id, datasource_id=ds.id, sql_text=SQL,
                params=PARAMS, values={"d": "x"}, template_id=task_b.id,
            ),
            a_dev,
        )
    assert "乙队的任务" in str(e.value) or "无权" in str(e.value)
    assert db.query(QueryJob).filter(QueryJob.template_id == task_b.id).count() == 0, (
        "被拒绝的试跑不许留下任何运行记录"
    )


def test_owner_can_attach_test_run_to_own_task(db, b_dev, team_b, ds, task_b, monkeypatch):
    """本队作者照常:记录挂上去、source=test。"""
    _TimeoutSpy(monkeypatch)
    template_service.test_run(
        db,
        TestRunIn(
            team_id=team_b.id, datasource_id=ds.id, sql_text=SQL,
            params=PARAMS, values={"d": "x"}, template_id=task_b.id,
        ),
        b_dev,
    )
    jobs = db.query(QueryJob).filter(QueryJob.template_id == task_b.id).all()
    assert [j.source for j in jobs] == [SOURCE_TEST]


def test_platform_admin_may_test_run_across_teams(db, admin, team_a, ds, task_b, monkeypatch):
    """平台管理员不受团队约束 —— 校验用的是 can_edit,不是「team_id 必须相等」。

    这条是防过度收紧:管理员在编辑器里给任务换团队后还没保存就点测试运行,不该被拦。
    """
    _TimeoutSpy(monkeypatch)
    template_service.test_run(
        db,
        TestRunIn(
            team_id=team_a.id, datasource_id=ds.id, sql_text=SQL,
            params=PARAMS, values={"d": "x"}, template_id=task_b.id,
        ),
        admin,
    )
    assert db.query(QueryJob).filter(QueryJob.template_id == task_b.id).count() == 1


def test_unsaved_task_still_runs_without_a_record(db, a_dev, team_a, ds, ready, monkeypatch):
    """新建未保存的任务没有 template_id,照旧只返回样例行、不留痕。"""
    _TimeoutSpy(monkeypatch)
    out = template_service.test_run(
        db,
        TestRunIn(team_id=team_a.id, datasource_id=ds.id, sql_text=SQL,
                  params=PARAMS, values={"d": "x"}),
        a_dev,
    )
    assert out["row_count"] == 1


# ---------------------------------------------------------------- F5 超时口径


def test_hive_test_run_gets_more_than_the_mysql_default(db, b_dev, team_b, hive_ds, ready, monkeypatch):
    """Hive 试跑不该套用 MySQL 的 120 秒即时默认,但也不能一等一小时。"""
    spy = _TimeoutSpy(monkeypatch)
    template_service.test_run(
        db,
        TestRunIn(team_id=team_b.id, datasource_id=hive_ds.id, sql_text=SQL,
                  params=PARAMS, values={"d": "x"}),
        b_dev,
    )
    assert spy.seen == [template_service.TEST_RUN_TIMEOUT_CEILING_SECONDS]


def test_task_timeout_is_honoured_up_to_the_foreground_ceiling(
    db, b_dev, team_b, ds, ready, monkeypatch
):
    """任务自己配的超时要生效,但被前台上限夹住 —— 试跑是有人在等的同步请求。"""
    spy = _TimeoutSpy(monkeypatch)
    low = create_template(
        TemplateCreateIn(name="trg-短超时任务", team_id=team_b.id, datasource_id=ds.id,
                         sql_text=SQL, params=PARAMS, timeout_seconds=30),
        db, b_dev, ip=None,
    )
    template_service.test_run(
        db,
        TestRunIn(team_id=team_b.id, datasource_id=ds.id, sql_text=SQL, params=PARAMS,
                  values={"d": "x"}, template_id=low.id),
        b_dev,
    )
    assert spy.seen == [30], "配得比上限低就该按配的来"


def test_timeout_error_says_whose_limit_it_was(db, b_dev, team_b, hive_ds, ready, monkeypatch):
    """超时报错必须说清「这是试跑的上限,正式运行时是另一个数」,否则作者会以为配置没生效。"""
    _TimeoutSpy(monkeypatch, fail="Hive 查询超时(>180s)已被终止")
    long_task = create_template(
        TemplateCreateIn(name="trg-长超时Hive任务", team_id=team_b.id, datasource_id=hive_ds.id,
                         sql_text=SQL, params=PARAMS, timeout_seconds=1800),
        db, b_dev, ip=None,
    )
    with pytest.raises(RubicError) as e:
        template_service.test_run(
            db,
            TestRunIn(team_id=team_b.id, datasource_id=hive_ds.id, sql_text=SQL, params=PARAMS,
                      values={"d": "x"}, template_id=long_task.id),
            b_dev,
        )
    msg = str(e.value)
    assert "1800" in msg and str(template_service.TEST_RUN_TIMEOUT_CEILING_SECONDS) in msg, (
        f"报错要同时给出两个上限,实际:{msg}"
    )

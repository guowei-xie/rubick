"""取数行数上限:默认**不限**,配了才截断;以及「不限」得以成立的前提。

这次改动之前是硬上限 100000,而且是静默截断:超出的行被直接丢掉,界面上没有任何标记,
业务用户唯一的线索是「行数比预期少」—— 一份少了行的报表比一次失败危险得多。
它当初还兼着「别把进程撑爆」的活,但那份保护是假的:MySQL 的默认游标在 execute 那一刻
就把整个结果集下载进了内存,截断发生在下载之后。

所以这里守三件事:
1. 默认配置下一行都不许丢,审计里的 truncated 恒 false;
2. 配成正数时行为回到「截断 + 审计记 truncated」—— 而界面依旧不提示,那条审计是唯一痕迹;
3. **整条链路不攒行**:连接器边取边吐、write_csv 边写边落盘。哪天有人在中间补一句
   list(rows),下面的用例会红 —— 那句话恰好就是 OOM 的形状,而后端是单进程 uvicorn。
"""
import csv

import pytest

from app.connectors.base import ConnectionConfig
from app.core.config import settings
from app.models.audit import ACTION_RUN_QUERY
from app.models.query_job import JOB_SUCCESS
from app.models.template import SqlTemplate
from app.models.user import ROLE_DEVELOPER, ROLE_USER
from app.schemas.template import TemplateCreateIn, TestRunIn
from app.services import permission_service, query_service, result_service, template_service
from tests.conftest import latest_audit

pytestmark = pytest.mark.usefixtures("clean_credentials")

# ID 段 9250–9251
AUTHOR, BIZ = 9250, 9251

SQL = "SELECT c FROM o"


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("cap-mysql")


@pytest.fixture
def author(user_factory):
    return user_factory(AUTHOR, ROLE_DEVELOPER, "上限用例作者", prefix="cap")


@pytest.fixture
def biz(user_factory):
    return user_factory(BIZ, ROLE_USER, "上限用例业务方", prefix="cap")


@pytest.fixture
def team(db, ds, author, biz, team_factory, team_credential):
    t = team_factory("cap-team", [(author, True), (biz, False)])
    team_credential(t, ds, username="cap_team_acct")
    return t


@pytest.fixture
def task(db, author, biz, ds, team) -> SqlTemplate:
    """已上线的无参数任务,并把运行权授给业务方(取数走的就是这条路)。"""
    tmpl = template_service.create_template(
        db, author,
        TemplateCreateIn(name="上限用例任务", team_id=team.id, datasource_id=ds.id, sql_text=SQL),
    )
    template_service.publish(db, tmpl, author, None)
    permission_service.grant(
        db, subject_type="user", subject_id=str(biz.id), resource_type="template",
        resource_id=str(tmpl.id), actions=["view", "run", "download"], granted_by=author.id,
    )
    return tmpl


def _run(db, biz, task, monkeypatch):
    monkeypatch.setattr(query_service.settings, "RUN_INLINE", True)
    return query_service.enqueue(db, biz, task.id, {})


def _result_rows(job) -> list[list[str]]:
    """把落盘的结果 CSV 读回来(不走预览的前 50 行,要看的是整份文件)。"""
    path = result_service.local_path(job.result_object_key)
    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        return list(csv.reader(fp))


# ---------------------------------------------------------------- 默认:不限


def test_default_config_has_no_row_cap():
    """默认配置里没有这一项,生效上限就是「不限」。

    0 → None 的换算只住在 settings.result_row_cap:取数各处都读它,免得有人自己判 <= 0
    时漏一处 —— 那种漏判不会报错,只会算出 min(50, 0) = 0 这种「一行都不给」。
    """
    assert settings.MAX_RESULT_ROWS == 0
    assert settings.result_row_cap is None


def test_a_result_far_past_the_old_limit_is_written_whole(
    db, ds, author, biz, team, task, spy_connector, monkeypatch
):
    """比从前那个上限还大的结果,一行都不许少。

    行数刻意跨过 CSV 的分片批(500 行)与从前的截断点,而不是取个 10 行了事 ——
    这条用例的意义在于「多到会分片」时也照旧完整。
    """
    n = 1200
    spy_connector(rows=[(i,) for i in range(n)])
    job = _run(db, biz, task, monkeypatch)

    assert job.status == JOB_SUCCESS
    assert job.row_count == n
    rows = _result_rows(job)
    assert rows[0] == ["c"]  # 表头
    assert len(rows) == n + 1
    assert rows[1] == ["0"] and rows[-1] == [str(n - 1)]
    assert latest_audit(db, ACTION_RUN_QUERY).detail["truncated"] is False, "没配上限就不该出现截断"


def test_a_configured_cap_still_truncates_and_the_audit_is_the_only_trace(
    db, ds, author, biz, team, task, spy_connector, monkeypatch
):
    """把上限配回正数:截断照旧发生,且**只有审计**记下了「这份不是全部」。

    界面不提示这件事没变(那是另一笔账),所以这条断言也是在钉住 config 里那句提醒:
    要配上限,就得自己想清楚谁去告诉业务用户。
    """
    spy_connector(rows=[(i,) for i in range(50)])
    monkeypatch.setattr(query_service.settings, "MAX_RESULT_ROWS", 3)
    job = _run(db, biz, task, monkeypatch)

    assert job.row_count == 3
    assert len(_result_rows(job)) == 4  # 表头 + 3 行
    assert latest_audit(db, ACTION_RUN_QUERY).detail["truncated"] is True


def test_test_run_sampling_is_not_zeroed_when_the_platform_is_uncapped(
    db, ds, author, team, spy_connector
):
    """试跑取样 50 行,不该因为平台「不限行数」而变成 0 行。

    这是 min(data.limit, MAX_RESULT_ROWS) 在上限为 0 时的算法陷阱:不报错,只是
    编辑器里点测试运行永远返回空表 —— 作者会以为自己的 SQL 写错了。
    """
    calls: list = []
    spy_connector(calls=calls)
    template_service.test_run(
        db,
        TestRunIn(team_id=team.id, datasource_id=ds.id, sql_text=SQL, params=[], values={}),
        author,
    )
    assert [c["max_rows"] for c in calls] == [50]


# ---------------------------------------------------------------- 不攒行


def test_write_csv_stops_early_without_draining_the_rows():
    """write_csv 是**惰性**消费行的:给它一条无穷的行流 + 上限,它必须停下来。

    这同时证明了它没有先把行收成列表 —— 那样做的话这个用例会挂死,而不是失败。
    """
    produced = {"n": 0}

    def endless():
        while True:
            produced["n"] += 1
            yield (produced["n"],)

    written, truncated = result_service.write_csv(
        "jobs/cap-test/endless.csv", ["c"], endless(), max_rows=5
    )
    assert (written, truncated) == (5, True)
    assert produced["n"] <= 6, "多探一行判断截断可以,再多就是在攒行了"
    path = result_service.local_path("jobs/cap-test/endless.csv")
    assert len(path.read_text(encoding="utf-8-sig").splitlines()) == 6  # 表头 + 5 行


def test_a_failure_midway_leaves_no_half_written_result():
    """写到一半失败(引擎报错 / 超时):不许留下一份看着正常、其实只有前半截的 CSV。"""
    def blow_up():
        yield (1,)
        raise RuntimeError("引擎中途断了")

    key = "jobs/cap-test/broken.csv"
    with pytest.raises(RuntimeError):
        result_service.write_csv(key, ["c"], blow_up())
    path = result_service.local_path(key)
    assert not path.exists()
    assert not path.with_name(path.name + ".part").exists()


def test_mysql_streams_server_side_and_drops_a_half_read_connection(monkeypatch):
    """MySQL 连接器必须要服务端游标,并且**丢弃**没读完的连接。

    两件事各有理由:服务端游标让内存与行数无关(默认的缓冲游标在 execute 那一刻就把
    整个结果集拉进了内存);而把没读完的连接还回池里会先关游标,关一个未读完的 SSCursor
    会把剩下的行全从服务端拉过来丢掉 —— 只要 50 行的试跑会因此等一个几百万行的查询走完。
    """
    from app.connectors import mysql as mysql_mod

    state = {"invalidated": False, "options": None}

    class _Result:
        def keys(self):
            return ["c"]

        def __iter__(self):
            return iter([(1,), (2,), (3,)])

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def exec_driver_sql(self, sql):
            return None

        def execute(self, stmt, params=None):
            state["options"] = stmt.get_execution_options()
            return _Result()

        def invalidate(self):
            state["invalidated"] = True

    monkeypatch.setattr(
        mysql_mod, "_get_engine",
        lambda url, ct: type("E", (), {"connect": lambda _s: _Conn()})(),
    )
    c = mysql_mod.MySQLConnector(ConnectionConfig(
        host="h", port=3306, database="demo", username="u", password="p", extra={},
    ))

    # 「我们要了服务端游标」和「这个驱动给得起」是两件事,都得钉住:换掉驱动而新驱动
    # 不支持时,stream_results 会被静默忽略 —— 于是内存保护无声无息地没了。
    from sqlalchemy import create_engine

    assert create_engine(
        "mysql+pymysql://u:p@h:3306/db"
    ).dialect.supports_server_side_cursors, "驱动不支持服务端游标,stream_results 会被忽略"

    full = c.execute(SQL, None, timeout_seconds=5, max_rows=None)
    assert state["options"]["stream_results"] is True
    assert state["options"]["max_row_buffer"] == mysql_mod._STREAM_BATCH_ROWS
    assert full.row_count == 3 and full.truncated is False
    assert state["invalidated"] is False, "读完了的连接照旧还回池里"

    part = c.execute(SQL, None, timeout_seconds=5, max_rows=1)
    assert (part.row_count, part.truncated) == (1, True)
    assert state["invalidated"] is True, "没读完的连接必须丢弃,不能还回池里"


def test_hive_fetches_in_batches_and_cancels_when_the_caller_stops(monkeypatch):
    """Hive 连接器按批取(不是一把 fetchmany(全部)),提前不取了就取消那个操作。

    也钉住 arraysize:pyhive 拿它当 Thrift 的 maxRows,不设它的话「一批多少行」这个常量
    只影响从缓冲里切多少,一次真实取多少行仍是驱动的默认值。
    """
    from app.connectors import hive as hive_mod

    class _Cursor:
        description = [("c",)]

        def __init__(self):
            self.rows = [(i,) for i in range(3)]
            self.batches: list[int] = []
            self.cancelled = False
            self.arraysize = 1  # pyhive 的真实默认是 1000;给个错的值,好看出有没有被设

        def execute(self, *a, **kw):
            return None

        def poll(self):
            from TCLIService.ttypes import TOperationState

            return type("R", (), {"operationState": TOperationState.FINISHED_STATE})()

        def fetchmany(self, n):
            self.batches.append(n)
            batch, self.rows = self.rows[:n], self.rows[n:]
            return batch

        def cancel(self):
            self.cancelled = True

    cursors: list[_Cursor] = []

    class _Conn:
        def cursor(self):
            cursors.append(_Cursor())
            return cursors[-1]

        def close(self):
            return None

    c = hive_mod.HiveConnector(ConnectionConfig(
        host="h", port=10000, database="demo", username="u", password=None, extra={},
    ))
    monkeypatch.setattr(c, "_connect", lambda: _Conn())

    full = c.execute(SQL, None, timeout_seconds=5, max_rows=None)
    assert full.row_count == 3 and full.truncated is False
    assert cursors[-1].batches == [hive_mod._FETCH_BATCH_ROWS] * 2  # 一批数据 + 一批空
    assert cursors[-1].arraysize == hive_mod._FETCH_BATCH_ROWS, "一批多少行要真的发给数仓"
    assert cursors[-1].cancelled is False

    part = c.execute(SQL, None, timeout_seconds=5, max_rows=1)
    assert (part.row_count, part.truncated) == (1, True)
    assert cursors[-1].cancelled is True, "不取了就该让数仓别再为这个结果集干活"

"""默认库进不去时的绕开:数据源上配的库是**首选**,不是**前提**。

线上事故:团队账号没有 `business_analysis` 的 [USE] 权限,而 pyhive 在建连期就执行
`USE <库>`,于是任务 SQL 一行都没跑就失败 —— 哪怕那条 SQL 写了全限定表名、上线后本来跑得动。
这直接反向破坏了 credential_service 立的承诺:「试跑通过即代表上线后能跑」。

绕开只认鉴权拒绝:密码错、库不存在、网络不通都要原样报出来,否则真故障会被藏成一次静默重试。
"""
import pytest

from app.connectors.base import ConnectionConfig
from tests.conftest import HIVE_PERM_DENIED, hive_thrift_error

DB = "business_analysis"


# ---------------------------------------------------------------- Hive


class _FakeHive:
    """假 pyhive 模块:记录每次尝试的库名,并按库名决定抛什么。

    模拟的是 pyhive 的真实行为 —— `USE <db>` 在 Connection() 构造期执行,所以库权限
    不足时连对象都拿不到(见 pyhive/hive.py 的 Connection.__init__)。
    """

    def __init__(self, *, denied=(), error=None):
        self.denied = set(denied)  # 这些库会以「鉴权拒绝」失败
        self.error = error  # 非 None 时:任何库都抛这个(模拟传输层/认证故障)
        self.attempts: list[str] = []

    def Connection(self, **kwargs):  # noqa: N802 -- 对齐 pyhive 的类名
        db = kwargs["database"]
        self.attempts.append(db)
        if self.error is not None:
            raise self.error
        if db in self.denied:
            raise hive_thrift_error(info_messages=[HIVE_PERM_DENIED])
        return _FakeConn()


class _FakeConn:
    def cursor(self):
        return _FakeCursor()

    def close(self):
        pass


class _FakeCursor:
    description = [("c",)]

    def execute(self, *a, **kw):
        pass

    def fetchone(self):
        return (1,)

    def fetchmany(self, n):
        return [(1,)]

    def poll(self):
        """异步提交后 execute() 会轮询状态 —— 直接报「已完成」。"""
        from TCLIService.ttypes import TOperationState

        return type("R", (), {"operationState": TOperationState.FINISHED_STATE})()


def _hive(**kw):
    from app.connectors.hive import HiveConnector

    conn = HiveConnector(ConnectionConfig(
        host="h", port=10000, database=DB, username="team_acct", password=None, extra={},
    ))
    conn._hive = _FakeHive(**kw)
    return conn


def test_bypasses_the_database_when_use_is_denied():
    """默认库被鉴权拒 → 换 default 再连一次,连通,并把「哪个库被绕开」这个事实留下。"""
    c = _hive(denied=[DB])
    assert c.test_connection() is None  # 连通:这不算失败
    assert c._hive.attempts == [DB, "default"]
    assert c.bypassed_database == DB


def test_bypass_lets_the_task_sql_decide():
    """试跑/取数走同一条建连:绕开后 execute 照常返回 —— 成败重新由任务 SQL 决定。"""
    c = _hive(denied=[DB])
    res = c.execute("SELECT 1 FROM business_analysis.t", None, timeout_seconds=5, max_rows=10)
    assert res.columns == ["c"]
    assert c._hive.attempts == [DB, "default"]


def test_nothing_bypassed_when_database_is_reachable():
    """有权限的场景必须完全不变:只连一次,没有绕开。"""
    c = _hive()
    assert c.test_connection() is None
    assert c._hive.attempts == [DB]
    assert c.bypassed_database is None


def test_reports_the_first_database_when_both_are_denied():
    """两个库都进不去时报**第一次**的原因 —— 作者关心的是数据源上配的那个库。"""
    c = _hive(denied=[DB, "default"])
    with pytest.raises(RuntimeError) as ei:
        c.test_connection()
    msg = str(ei.value)
    assert c._hive.attempts == [DB, "default"]
    assert "TExecuteStatementResp" not in msg  # 不泄 Thrift repr
    assert f"[USE] privilege on [{DB}]" in msg
    assert c.bypassed_database is None


def test_transport_failure_does_not_bypass():
    """网络/认证类故障不绕开:换个库重试只会把真故障藏起来,且白付一次握手。"""
    c = _hive(error=OSError("Could not connect to h:10000"))
    with pytest.raises(RuntimeError) as ei:
        c.test_connection()
    assert c._hive.attempts == [DB]  # 只试了一次
    assert "Could not connect" in str(ei.value)


# ---------------------------------------------------------------- MySQL


class _FakeEngine:
    def __init__(self, error=None):
        self.error = error
        self.connects = 0

    def connect(self):
        self.connects += 1
        if self.error is not None:
            raise self.error
        return _FakeSAConn()


class _FakeSAConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def exec_driver_sql(self, sql):
        return None


def _mysql_error(code: int, msg: str):
    """SQLAlchemy 包装后的驱动异常:错误码在 orig.args[0](与 pymysql 一致)。"""
    from sqlalchemy.exc import OperationalError

    return OperationalError("SELECT 1", {}, Exception(code, msg))


def _mysql(monkeypatch, *, primary_error=None, fallback_error=None):
    from app.connectors import mysql as mysql_mod

    engines = {"primary": _FakeEngine(primary_error), "fallback": _FakeEngine(fallback_error)}
    monkeypatch.setattr(
        mysql_mod, "_get_engine",
        lambda url, ct: engines["primary" if url.database else "fallback"],
    )
    conn = mysql_mod.MySQLConnector(ConnectionConfig(
        host="h", port=3306, database=DB, username="team_acct", password="p", extra={},
    ))
    return conn, engines


def test_mysql_bypasses_when_denied_to_database(monkeypatch):
    """1044(进不去这个库)→ 改用不带默认库的连接,连通并留下被绕开的库名。"""
    c, engines = _mysql(monkeypatch, primary_error=_mysql_error(
        1044, "Access denied for user 'team_acct'@'10.0.0.5' to database 'business_analysis'"
    ))
    assert c.test_connection() is None
    assert engines["primary"].connects == 1 and engines["fallback"].connects == 1
    assert c.bypassed_database == DB


@pytest.mark.parametrize(("code", "msg"), [
    (1045, "Access denied for user 'team_acct'@'10.0.0.5' (using password: YES)"),
    (1049, "Unknown database 'business_analysis'"),
])
def test_mysql_does_not_bypass_on_bad_credentials_or_missing_db(monkeypatch, code, msg):
    """密码错(1045)与库不存在(1049)不绕开:那是配置问题,藏起来只会更难查。"""
    c, engines = _mysql(monkeypatch, primary_error=_mysql_error(code, msg))
    with pytest.raises(Exception) as ei:
        c.test_connection()
    assert engines["fallback"].connects == 0
    assert msg in str(ei.value)
    assert c.bypassed_database is None


def test_mysql_nothing_bypassed_when_database_is_reachable(monkeypatch):
    c, engines = _mysql(monkeypatch)
    assert c.test_connection() is None
    assert engines["fallback"].connects == 0
    assert c.bypassed_database is None

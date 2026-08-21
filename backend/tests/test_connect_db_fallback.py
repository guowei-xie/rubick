"""默认库怎么参与建连:取数时是「首选」,测试连接时压根不参与。

线上事故:团队账号没有 `business_analysis` 的 [USE] 权限,而 pyhive 在建连期就执行
`USE <库>`,于是任务 SQL 一行都没跑就失败 —— 哪怕那条 SQL 写了全限定表名、上线后本来跑得动。
这直接反向破坏了 credential_service 立的承诺:「试跑通过即代表上线后能跑」。

两条候选顺序刻意相反:
- **取数**(execute):先配的库,进不去才绕开 —— 不写库名的老 SQL 才能照旧解析;
- **测试连接**:先中立的 default —— 它只该回答「账号能不能登进数仓」,而线上那个应用库
  本来就不是常用库,拿它当门槛只会把可用的账号判成连不上。

绕开只认鉴权拒绝:密码错、库不存在、网络不通都要原样报出来,否则真故障会被藏成一次静默重试。
"""
import pytest

from app.connectors.base import ConnectionConfig
from tests.conftest import HIVE_PERM_DENIED, hive_thrift_error

DB = "business_analysis"
VISIBLE = ["default", "finance_bp", DB]


# ---------------------------------------------------------------- Hive


class _FakeHive:
    """假 pyhive 模块:记录每次尝试的库名,并按库名决定抛什么。

    模拟的是 pyhive 的真实行为 —— `USE <db>` 在 Connection() 构造期执行,所以库权限
    不足时连对象都拿不到(见 pyhive/hive.py 的 Connection.__init__)。
    """

    def __init__(self, *, denied=(), error=None, show_fails=False):
        self.denied = set(denied)  # 这些库会以「鉴权拒绝」失败
        self.error = error  # 非 None 时:任何库都抛这个(模拟传输层/认证故障)
        self.show_fails = show_fails  # SHOW DATABASES 被拒(有些集群不给列库权限)
        self.attempts: list[str] = []

    def Connection(self, **kwargs):  # noqa: N802 -- 对齐 pyhive 的类名
        db = kwargs["database"]
        self.attempts.append(db)
        if self.error is not None:
            raise self.error
        if db in self.denied:
            raise hive_thrift_error(info_messages=[HIVE_PERM_DENIED])
        return _FakeConn(self.show_fails)


class _FakeConn:
    def __init__(self, show_fails=False):
        self.show_fails = show_fails

    def cursor(self):
        return _FakeCursor(self.show_fails)

    def close(self):
        pass


class _FakeCursor:
    description = [("c",)]

    def __init__(self, show_fails=False):
        self._show = False
        self._show_fails = show_fails

    def execute(self, sql, *a, **kw):
        self._show = "SHOW DATABASES" in sql
        if self._show and self._show_fails:
            raise hive_thrift_error(info_messages=[HIVE_PERM_DENIED])

    def fetchall(self):
        return [(d,) for d in VISIBLE] if self._show else [(1,)]

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


def test_test_connection_never_touches_the_configured_database():
    """「测试连接」只连中立库,压根不碰数据源配的那个库,并列出账号可访问的库。"""
    c = _hive(denied=[DB])  # 配的库进不去也无所谓 —— 它不参与
    assert c.test_connection() == VISIBLE
    assert c._hive.attempts == ["default"]


def test_test_connection_falls_back_when_even_default_is_denied():
    """极严策略把 default 也关了才退到配的库 —— 否则可用的账号会被判成连不上。"""
    c = _hive(denied=["default"])
    assert c.test_connection() == VISIBLE
    assert c._hive.attempts == ["default", DB]


def test_listing_failure_does_not_fail_the_connection_test():
    """列不出库不算连不上:建连本身(认证 + USE)已经证明身份可用,列表只是附加信息。"""
    c = _hive(show_fails=True)
    assert c.test_connection() == []
    assert c._hive.attempts == ["default"]


def test_query_prefers_the_configured_database_then_bypasses_it():
    """取数反过来:先配的库(不写库名的老 SQL 靠它解析),被鉴权拒才绕开。"""
    c = _hive(denied=[DB])
    res = c.execute("SELECT 1 FROM business_analysis.t", None, timeout_seconds=5, max_rows=10)
    assert res.columns == ["c"]
    assert c._hive.attempts == [DB, "default"]


def test_query_connects_once_when_the_database_is_reachable():
    """有权限的场景必须完全不变:只连一次。"""
    c = _hive()
    c.execute("SELECT 1", None, timeout_seconds=5, max_rows=10)
    assert c._hive.attempts == [DB]


def test_reports_the_first_database_when_both_are_denied():
    """两个库都进不去时报**第一个候选**的原因 —— 那才是调用方本来想连的库。"""
    c = _hive(denied=[DB, "default"])
    with pytest.raises(RuntimeError) as ei:
        c.execute("SELECT 1", None, timeout_seconds=5, max_rows=10)
    msg = str(ei.value)
    assert c._hive.attempts == [DB, "default"]
    assert "TExecuteStatementResp" not in msg  # 不泄 Thrift repr
    assert f"[USE] privilege on [{DB}]" in msg


def test_transport_failure_does_not_try_another_database():
    """网络/认证类故障不换库重试:那只会把真故障藏起来,且白付一次握手。"""
    c = _hive(error=OSError("Could not connect to h:10000"))
    with pytest.raises(RuntimeError) as ei:
        c.test_connection()
    assert c._hive.attempts == ["default"]  # 只试了一次
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
        return type("R", (), {"fetchall": lambda _s: [(d,) for d in VISIBLE]})()

    def execute(self, stmt, params=None):
        return type("R", (), {
            "keys": lambda _s: ["c"], "fetchmany": lambda _s, n: [(1,)],
        })()


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


def test_mysql_test_connection_never_uses_the_configured_database(monkeypatch):
    """MySQL 侧更直接:测试连接压根不带默认库,身份对了就连得上。"""
    c, engines = _mysql(monkeypatch, primary_error=_mysql_error(
        1044, "Access denied for user 'team_acct'@'10.0.0.5' to database 'business_analysis'"
    ))
    assert c.test_connection() == VISIBLE
    assert engines["primary"].connects == 0  # 带库的那条连接根本没用上
    assert engines["fallback"].connects == 1


def test_mysql_query_bypasses_when_denied_to_database(monkeypatch):
    """取数时 1044(进不去这个库)→ 改用不带默认库的连接继续。"""
    c, engines = _mysql(monkeypatch, primary_error=_mysql_error(
        1044, "Access denied for user 'team_acct'@'10.0.0.5' to database 'business_analysis'"
    ))
    c.execute("SELECT 1", None, timeout_seconds=5, max_rows=10)
    assert engines["primary"].connects == 1 and engines["fallback"].connects == 1


@pytest.mark.parametrize(("code", "msg"), [
    (1045, "Access denied for user 'team_acct'@'10.0.0.5' (using password: YES)"),
    (1049, "Unknown database 'business_analysis'"),
])
def test_mysql_does_not_bypass_on_bad_credentials_or_missing_db(monkeypatch, code, msg):
    """密码错(1045)与库不存在(1049)不绕开:那是配置问题,藏起来只会更难查。"""
    c, engines = _mysql(monkeypatch, primary_error=_mysql_error(code, msg))
    with pytest.raises(Exception) as ei:
        c.execute("SELECT 1", None, timeout_seconds=5, max_rows=10)
    assert engines["fallback"].connects == 0
    assert msg in str(ei.value)

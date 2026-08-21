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
import contextlib

import pytest

from app.connectors.base import ConnectionConfig
from tests.conftest import HIVE_PERM_DENIED, hive_thrift_error

DB = "business_analysis"
VISIBLE = ["default", "finance_bp", DB]


# ---------------------------------------------------------------- Hive


class _FakeHive:
    """假 pyhive 模块。`Connection` 是**类**(真 pyhive 也是),因为连接器会去继承它。"""

    def __init__(self, *, denied=(), error=None, show_fails=False, visible=None):
        self.denied = set(denied)  # 这些库在 `USE` 时被鉴权拒
        self.error = error  # 非 None:OpenSession 阶段就失败(传输层/认证故障)
        self.show_fails = show_fails  # SHOW DATABASES 被拒
        self.visible = VISIBLE if visible is None else visible
        self.use_error = None  # 非 None:`USE` 抛这个(用来造非鉴权类失败)
        self.opened: list[str] = []  # OpenSession 到过的库(建连尝试)
        self.used: list[str] = []  # 真正执行过 `USE` 的库
        rec = self

        class Connection:
            """照搬 pyhive 的构造顺序:OpenSession 成功后再执行 `USE <db>`。

            这个顺序是本轮的关键 —— 「登得进数仓」与「进得去某个库」是两件事,
            测试连接靠跳过后者来验前者(见 hive.py::_open_without_use)。
            """

            def __init__(self, **kwargs):
                self.rec = rec
                db = kwargs["database"]
                rec.opened.append(db)
                if rec.error is not None:
                    raise rec.error
                with contextlib.closing(self.cursor()) as cur:
                    cur.execute(f"USE `{db}`")

            def cursor(self):
                return _FakeCursor(rec)

            def close(self):
                pass

        self.Connection = Connection


class _FakeCursor:
    description = [("c",)]

    def __init__(self, rec):
        self.rec = rec
        self._rows: list[tuple] = [(1,)]

    def execute(self, sql, *a, **kw):
        if sql.startswith("USE `"):
            db = sql[len("USE `"):-1]
            self.rec.used.append(db)
            if self.rec.use_error is not None:
                raise self.rec.use_error
            if db in self.rec.denied:
                raise hive_thrift_error(info_messages=[HIVE_PERM_DENIED])
        elif "SHOW DATABASES" in sql:
            if self.rec.show_fails:
                raise hive_thrift_error(info_messages=[HIVE_PERM_DENIED])
            self._rows = [(d,) for d in self.rec.visible]

    def fetchall(self):
        return self._rows

    def fetchmany(self, n):
        return [(1,)]

    def close(self):
        pass

    def poll(self):
        """异步提交后会轮询状态 —— 直接报「已完成」。"""
        from TCLIService.ttypes import TOperationState

        return type("R", (), {"operationState": TOperationState.FINISHED_STATE})()


def _hive(**kw):
    from app.connectors.hive import HiveConnector

    conn = HiveConnector(ConnectionConfig(
        host="h", port=10000, database=DB, username="team_acct", password=None, extra={},
    ))
    conn._hive = _FakeHive(**kw)
    return conn


def test_test_connection_enters_no_database_at_all():
    """「测试连接」压根不 `USE` 任何库:它只验「登得进数仓」+ 列出能访问的库。"""
    c = _hive()
    assert c.test_connection() == VISIBLE
    assert c._hive.used == []  # 一次 USE 都没做


def test_test_connection_works_when_every_database_is_denied():
    """线上 dongyi7 的处境:default 与数据源配的库都没有 USE 权限。

    它**能**登进数仓(报错是 HiveSQLException,说明 OpenSession 已成功),也确实有别的库的
    权限,却因为 pyhive 建连时那句 USE 被判成「连不上」—— 而且没法通过平台知道自己能进哪个库
    (要连上才知道、要知道才连得上)。跳过 USE 后这个死循环就解开了:测试连接直接告诉他能进哪些库。
    """
    c = _hive(denied=["default", DB], visible=["finance_bp", "dw_finance"])
    assert c.test_connection() == ["finance_bp", "dw_finance"]
    assert c._hive.used == []


def test_listing_failure_does_not_fail_the_connection_test():
    """列不出库不算连不上:OpenSession 已经证明身份可用,列表只是附加信息。"""
    c = _hive(show_fails=True)
    assert c.test_connection() == []


def test_query_survives_a_denied_use_on_the_configured_database():
    """取数:会话开好后**尽力**进一次配的库,被鉴权拒也不算失败 —— 会话本身可用。

    平台不该因为一个「裸表名的解析起点」把整条链路判死:写全限定表名的 SQL 本就不需要它。
    这正是线上那次故障的形状(试跑挂在 USE 上,而那条 SQL 其实跑得动)。
    """
    c = _hive(denied=[DB])
    res = c.execute("SELECT 1 FROM business_analysis.t", None, timeout_seconds=5, max_rows=10)
    assert res.columns == ["c"]
    assert c._hive.opened == [DB]  # 只开一次会话
    assert c._hive.used == [DB]  # 尝试过进那个库(失败了,但不影响取数)


def test_query_raises_when_the_configured_database_does_not_exist():
    """非鉴权的 USE 失败(库名写错等)照旧抛:那是配置错,悄悄放过只会让人对着「找不到表」猜。"""
    c = _hive()
    c._hive.use_error = hive_thrift_error(
        error_message="Error while compiling statement: FAILED: Database 'typo_db' not found"
    )
    with pytest.raises(RuntimeError) as ei:
        c.execute("SELECT 1", None, timeout_seconds=5, max_rows=10)
    assert "not found" in str(ei.value)


def test_query_connects_once_when_the_database_is_reachable():
    """有权限的场景必须完全不变:只连一次。"""
    c = _hive()
    c.execute("SELECT 1", None, timeout_seconds=5, max_rows=10)
    assert c._hive.opened == [DB]


def test_transport_failure_still_fails_the_connection_test():
    """OpenSession 都失败(网络不通 / 认证错)才是真的连不上,必须如实报错。"""
    c = _hive(error=OSError("Could not connect to h:10000"))
    with pytest.raises(RuntimeError) as ei:
        c.test_connection()
    assert len(c._hive.opened) == 1  # 只开一次会话,不换库重试
    assert "Could not connect" in str(ei.value)


def test_query_path_does_not_try_another_database_on_transport_failure():
    """取数路径同理:网络/认证故障不换库重试,那只会把真故障藏起来、白付一次握手。"""
    c = _hive(error=OSError("Could not connect to h:10000"))
    with pytest.raises(RuntimeError) as ei:
        c.execute("SELECT 1", None, timeout_seconds=5, max_rows=10)
    assert c._hive.opened == [DB]
    assert "Could not connect" in str(ei.value)


# ---------------------------------------------------------------- MySQL


class _FakeEngine:
    def __init__(self, error=None):
        self.error = error
        self.connects = 0
        self.disposed = False

    def dispose(self):
        self.disposed = True

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


def _mysql(monkeypatch, *, primary_error=None):
    """假掉两条取连接的路:查询走 _get_engine(带缓存),测试连接走 create_engine(即用即弃)。"""
    from app.connectors import mysql as mysql_mod

    engines = {
        "primary": _FakeEngine(primary_error),  # 带默认库(查询首选)
        "fallback": _FakeEngine(),  # 不带默认库(查询被拒后绕开用)
        "throwaway": _FakeEngine(),  # 测试连接:不进缓存
    }
    monkeypatch.setattr(
        mysql_mod, "_get_engine",
        lambda url, ct: engines["primary" if url.database else "fallback"],
    )
    monkeypatch.setattr(mysql_mod, "create_engine", lambda url, **kw: engines["throwaway"])
    conn = mysql_mod.MySQLConnector(ConnectionConfig(
        host="h", port=3306, database=DB, username="team_acct", password="p", extra={},
    ))
    return conn, engines


def test_mysql_test_connection_never_uses_the_configured_database(monkeypatch):
    """MySQL 侧更直接:测试连接压根不带默认库,身份对了就连得上。

    且它用的是即用即弃的连接,不碰共享的 Engine 缓存 —— 否则每个被测过的身份都会在缓存里
    常驻一条永不复用的 Engine 与空闲连接,把缓存位挤给取数路径的热 Engine。
    """
    c, engines = _mysql(monkeypatch, primary_error=_mysql_error(
        1044, "Access denied for user 'team_acct'@'10.0.0.5' to database 'business_analysis'"
    ))
    assert c.test_connection() == VISIBLE
    assert engines["throwaway"].connects == 1
    assert engines["primary"].connects == 0 and engines["fallback"].connects == 0


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

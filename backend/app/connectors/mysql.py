"""MySQL 连接器。

用 SQLAlchemy Core 执行,命名参数 :name 由驱动做参数化绑定(防注入)。
Engine(连接池)按「连接身份」缓存复用,详见 _get_engine。

默认库:数据源上配的 database 写进 DSN,是**首选**而不是**前提** —— 账号进不去它(MySQL 1044)
时绕开它、不带默认库再连一次(见 _connect),成败交还给任务 SQL。
「测试连接」压根不带默认库(见 test_connection),故与那个库无关 —— 契约见 connectors/base.py。
"""
from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

from app.connectors.base import ConnectionConfig, DataSourceConnector, abandonable

# 按连接身份缓存 Engine,复用连接池;否则每次取数都新建池、每查都付一次 TCP+鉴权握手。
#
# 三个刻意的设计(团队取数账号上线后,缓存条目从「数据源数」变成「数据源 × 团队数」):
# 1. key 是连接串的 sha256,不是连接串本身 —— 明文密码不必常驻在字典的键上;
# 2. 有上限的 LRU,淘汰时 dispose() 真正释放连接池,否则换过密码的旧 Engine 会永久占着连接;
# 3. 每个身份的池刻意开得小(见下),几十个身份并存时才不会把目标库的连接数打满。
_ENGINE_CACHE: "OrderedDict[str, Any]" = OrderedDict()
# 身份数 = 团队数 × MySQL 数据源数(比按人隔离时小一个量级),上限要盖得住,
# 否则每次 miss 都要重付一次 TCP + 鉴权握手(5-50ms);与之配套的是把每个身份的池开得很小。
# 注:真被绕开过默认库的身份会额外占一条「不带默认库」的缓存项(见 _connect),但那是少数
# —— 「测试连接」刻意不进这个缓存(见 test_connection),否则每个被测过的身份都要多占一格。
_ENGINE_CACHE_MAX = 64
# 单个身份的池:取数是低频长查询,常驻 1 条热连接足够;overflow 应付并发试跑
_POOL_SIZE = 1
_POOL_MAX_OVERFLOW = 4
_POOL_RECYCLE_SECONDS = 1800  # 早于 MySQL 默认 wait_timeout(8h)回收,避免拿到已被服务端关掉的连接
# 服务端游标一次预读多少行。只影响单次网络往返的批量,不是缓存:内存占用只与这个数有关,
# 与结果总行数无关。调大省往返、调小省内存,几百行是取数这种低频长查询的合适量级。
_STREAM_BATCH_ROWS = 500


def _get_engine(url: URL, connect_timeout: int):
    # connect_timeout 进 key:同一身份改了超时配置要拿到新 Engine,否则改了不生效
    raw = f"{url.render_as_string(hide_password=False)}|ct={connect_timeout}"
    key = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    eng = _ENGINE_CACHE.get(key)
    if eng is not None:
        _ENGINE_CACHE.move_to_end(key)  # 命中即刷新 LRU 位置
        return eng

    eng = create_engine(
        url,
        pool_pre_ping=True,
        pool_size=_POOL_SIZE,
        max_overflow=_POOL_MAX_OVERFLOW,
        pool_recycle=_POOL_RECYCLE_SECONDS,
        connect_args={"connect_timeout": connect_timeout},
    )
    _ENGINE_CACHE[key] = eng
    while len(_ENGINE_CACHE) > _ENGINE_CACHE_MAX:
        _, evicted = _ENGINE_CACHE.popitem(last=False)
        evicted.dispose()  # 关掉池里的连接;仍在用的连接会在归还时被丢弃,不会中断进行中的查询
    return eng


def _url(config: ConnectionConfig, database: str | None) -> URL:
    """身份 + 地址 + 默认库 → DSN。database=None 即「不指定默认库」(绕开时用)。"""
    return URL.create(
        "mysql+pymysql",
        username=config.username,
        password=config.password or "",
        host=config.host,
        port=config.port,
        database=database,
    )


def _is_db_permission_error(exc: Exception) -> bool:
    """是不是「账号本身没问题,但进不去 DSN 里那个默认库」?

    只认 1044(ER_DBACCESS_DENIED_ERROR):1045 是账号密码错、1049 是库不存在,
    那两种绕开都只会把问题藏起来。错误码取自驱动原始异常,不做文案匹配(文案会随版本变)。
    """
    orig = getattr(exc, "orig", None)
    args = getattr(orig, "args", None) or ()
    return bool(args) and args[0] == 1044


class MySQLConnector(DataSourceConnector):
    engine = "mysql"

    def __init__(self, config: ConnectionConfig):
        super().__init__(config)
        self._connect_timeout = int(config.extra.get("connect_timeout", 10))

    def _open(self, database: str | None):
        """按指定默认库取一条连接。database=None 即「不指定默认库」(绕开用)。"""
        return _get_engine(_url(self.config, database), self._connect_timeout).connect()

    def _connect(self):
        """取数用的建连。默认库进不去(MySQL 1044)时绕开它、不带默认库再连一次。

        与 Hive 同一个理由和同一套取舍,完整论证见 connectors/hive.py::_connect。
        MySQL 侧的区别只有两点:绕开的方式是「不带默认库」而不是换 default;
        判定认的是 1044 —— 1045(密码错)、1049(库不存在)原样抛。
        """
        database = self.config.database
        try:
            return self._open(database)
        except Exception as e:  # noqa: BLE001
            if not (database and _is_db_permission_error(e)):
                raise
            try:
                conn = self._open(None)
            except Exception:  # noqa: BLE001 -- 不带默认库也连不上:仍报第一次的原因
                raise e from None
        self.warn_bypassed(database, "不带默认库的连接")
        return conn

    @contextmanager
    def stream(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: int,
    ) -> Iterator[tuple[list[str], Iterator[Sequence]]]:
        """边取边吐(契约见基类)。**服务端游标**,内存占用与结果行数无关。

        stream_results=True 让 pymysql 用 SSCursor:行留在服务端,客户端按需取。
        这不只是「顺手优化」—— 默认的缓冲游标在 execute() 那一刻就把**整个**结果集
        下载进客户端内存,于是从前那个 10 万行上限压根没保护到内存(截断发生在下载之后),
        真正的保护只能是这里。

        没读完就 invalidate():把连接还回池里要先关游标,而关一个未读完的 SSCursor 会把
        剩下的行全从服务端拉过来丢掉 —— 只要 50 行的试跑会因此等一个几百万行的结果集走完。
        invalidate() 直接丢弃这条物理连接,代价只是下次取数多付一次握手。
        """
        with self._connect() as conn:
            # 语句级超时(MySQL 8+):单位毫秒
            conn.exec_driver_sql(f"SET SESSION MAX_EXECUTION_TIME={timeout_seconds * 1000}")
            stmt = text(sql).execution_options(
                stream_results=True, max_row_buffer=_STREAM_BATCH_ROWS
            )
            result = conn.execute(stmt, params or {})
            with abandonable(result, conn.invalidate) as rows:
                yield list(result.keys()), rows

    def test_connection(self) -> list[str]:
        """验身份 + 列出账号能访问的库(契约见基类)。刻意不走 _connect —— 那是取数口径。

        连接即用即弃(NullPool),不进 _ENGINE_CACHE:一次人工点击换来一条**永不复用**的
        常驻 Engine 与空闲连接,会把缓存位挤给取数路径的热 Engine,那才是真代价;
        多付一次握手(5-50ms)在一个按钮上根本看不出来。
        """
        eng = create_engine(
            _url(self.config, None),
            poolclass=NullPool,
            connect_args={"connect_timeout": self._connect_timeout},
        )
        try:
            with eng.connect() as conn:
                try:
                    rows = conn.exec_driver_sql("SHOW DATABASES").fetchall()
                except Exception as e:  # noqa: BLE001 -- 列不出不算连不上
                    self.warn_unlistable(str(e)[:200])
                    return []
                return self.first_column(rows)
        finally:
            eng.dispose()

"""MySQL 连接器。

用 SQLAlchemy Core 执行,命名参数 :name 由驱动做参数化绑定(防注入)。
Engine(连接池)按「连接身份」缓存复用,详见 _get_engine。

默认库:数据源上配的 database 写进 DSN,是**首选**而不是**前提** —— 账号进不去它(MySQL 1044)
时绕开它、不带默认库再连一次(见 _connect),成败交还给任务 SQL。
"""
from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from app.connectors.base import ConnectionConfig, DataSourceConnector, QueryResult

# 按连接身份缓存 Engine,复用连接池;否则每次取数都新建池、每查都付一次 TCP+鉴权握手。
#
# 三个刻意的设计(团队取数账号上线后,缓存条目从「数据源数」变成「数据源 × 团队数」):
# 1. key 是连接串的 sha256,不是连接串本身 —— 明文密码不必常驻在字典的键上;
# 2. 有上限的 LRU,淘汰时 dispose() 真正释放连接池,否则换过密码的旧 Engine 会永久占着连接;
# 3. 每个身份的池刻意开得小(见下),几十个身份并存时才不会把目标库的连接数打满。
_ENGINE_CACHE: "OrderedDict[str, Any]" = OrderedDict()
# 身份数 = 团队数 × MySQL 数据源数(比按人隔离时小一个量级),上限要盖得住,
# 否则每次 miss 都要重付一次 TCP + 鉴权握手(5-50ms);与之配套的是把每个身份的池开得很小。
# 注:进不去默认库的身份会额外占一条「不带默认库」的缓存项(见 _connect 的绕开),但那是少数。
_ENGINE_CACHE_MAX = 64
# 单个身份的池:取数是低频长查询,常驻 1 条热连接足够;overflow 应付并发试跑
_POOL_SIZE = 1
_POOL_MAX_OVERFLOW = 4
_POOL_RECYCLE_SECONDS = 1800  # 早于 MySQL 默认 wait_timeout(8h)回收,避免拿到已被服务端关掉的连接


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
        """取一条连接。默认库进不去(MySQL 1044)时绕开它、不带默认库再连一次。

        与 Hive 同一个理由和同一套取舍,完整论证见 connectors/hive.py::_connect。
        MySQL 侧的区别只有两点:绕开的方式是「不带默认库」而不是换 default;
        判定认的是 1044 —— 1045(密码错)、1049(库不存在)原样抛。
        """
        self.bypassed_database = None
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
        self.bypassed_database = database
        return conn

    def execute(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: int,
        max_rows: int,
    ) -> QueryResult:
        start = time.perf_counter()
        with self._connect() as conn:
            # 语句级超时(MySQL 8+):单位毫秒
            conn.exec_driver_sql(f"SET SESSION MAX_EXECUTION_TIME={timeout_seconds * 1000}")
            result = conn.execute(text(sql), params or {})
            columns = list(result.keys())
            rows = result.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        return QueryResult(
            columns=columns,
            rows=[tuple(r) for r in rows],
            truncated=truncated,
            meta={"duration_ms": int((time.perf_counter() - start) * 1000)},
        )

    def test_connection(self) -> None:
        """验「这个账号能不能登进库」:`SELECT 1`,不碰任何业务表。

        默认库进不去时会被绕开(见 _connect),那仍算连通;事实留在 bypassed_database。
        """
        with self._connect() as conn:
            conn.exec_driver_sql("SELECT 1")

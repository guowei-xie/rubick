"""MySQL 连接器。

用 SQLAlchemy Core 执行,命名参数 :name 由驱动做参数化绑定(防注入)。
Engine(连接池)按「连接身份」缓存复用,详见 _get_engine。
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
# 否则每次 miss 都要重付一次 TCP + 鉴权握手(5-50ms);与之配套的是把每个身份的池开得很小
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


class MySQLConnector(DataSourceConnector):
    engine = "mysql"

    def __init__(self, config: ConnectionConfig):
        super().__init__(config)
        url = URL.create(
            "mysql+pymysql",
            username=config.username,
            password=config.password or "",
            host=config.host,
            port=config.port,
            database=config.database,
        )
        self._engine = _get_engine(url, int(config.extra.get("connect_timeout", 10)))

    def execute(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: int,
        max_rows: int,
    ) -> QueryResult:
        start = time.perf_counter()
        with self._engine.connect() as conn:
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
        with self._engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")

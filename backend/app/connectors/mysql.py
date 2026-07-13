"""MySQL 连接器(Phase 1 打通)。

用 SQLAlchemy Core 执行,命名参数 :name 由驱动做参数化绑定(防注入)。
每次执行创建独立引擎;生产可换成按数据源缓存的连接池。
"""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from app.connectors.base import ConnectionConfig, DataSourceConnector, QueryResult

# 按连接串缓存 Engine,复用连接池;否则每次取数都新建池、每查都付一次 TCP+鉴权握手
_ENGINE_CACHE: dict = {}


def _get_engine(url: URL, connect_timeout: int):
    key = url.render_as_string(hide_password=False)
    eng = _ENGINE_CACHE.get(key)
    if eng is None:
        eng = create_engine(
            url, pool_pre_ping=True, connect_args={"connect_timeout": connect_timeout}
        )
        _ENGINE_CACHE[key] = eng
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

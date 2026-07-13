"""Hive 连接器(接口就绪,连接实现留待接入真实 HiveServer2)。

设计:直连 HiveServer2(Thrift),用 pyhive。参数化用 pyhive 的 paramstyle。
Phase 1 依赖未安装时,实例化即抛清晰错误,不影响 MySQL 主链路。
接入时:pip install "pyhive[hive]" thrift,并按需处理 Kerberos/LDAP 认证
(见 PRD 开放项——Hive 认证方式)。
"""
from __future__ import annotations

import re
import time
from typing import Any

from app.connectors.base import ConnectionConfig, DataSourceConnector, QueryResult


class HiveConnector(DataSourceConnector):
    engine = "hive"

    def __init__(self, config: ConnectionConfig):
        super().__init__(config)
        try:
            from pyhive import hive  # noqa: F401
        except ImportError as e:  # pragma: no cover - 依赖未装
            raise RuntimeError(
                "Hive 连接器需要 pyhive:pip install 'pyhive[hive]' thrift。"
                "Phase 1 骨架默认未安装。"
            ) from e
        self._hive = hive

    def _connect(self):
        auth = self.config.extra.get("auth", "NONE")  # NONE / LDAP / KERBEROS ...
        return self._hive.Connection(
            host=self.config.host,
            port=self.config.port,
            database=self.config.database or "default",
            username=self.config.username,
            password=self.config.password if auth in ("LDAP", "CUSTOM") else None,
            auth=auth,
        )

    def execute(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: int,
        max_rows: int,
    ) -> QueryResult:
        # pyhive 带参数时会对整条 SQL 做 Python % 格式化来注入参数。因此:
        #   - 有参数:先把字面 % 转义为 %%(否则 LIKE '%x%'、date_format('%Y') 会破坏格式化),
        #             再把 :name 占位符转成 pyhive 的 %(name)s,交由驱动参数化绑定;
        #   - 无参数:原样执行(pyhive 不做格式化),字面 % 保持不变。
        params = params or {}
        start = time.perf_counter()
        conn = self._connect()
        try:
            cursor = conn.cursor()
            if params:
                pyformat_sql = sql.replace("%", "%%")
                # 只替换已定义的参数名,避免误伤字符串里的 :xx(如时间格式 '%H:%i:%s')
                for name in params:
                    pyformat_sql = re.sub(rf":{re.escape(name)}\b", f"%({name})s", pyformat_sql)
                cursor.execute(pyformat_sql, params)
            else:
                cursor.execute(sql)
            columns = [d[0] for d in cursor.description]
            rows = cursor.fetchmany(max_rows + 1)
        finally:
            conn.close()
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        return QueryResult(
            columns=columns,
            rows=[tuple(r) for r in rows],
            truncated=truncated,
            meta={"duration_ms": int((time.perf_counter() - start) * 1000)},
        )

    def test_connection(self) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
        finally:
            conn.close()

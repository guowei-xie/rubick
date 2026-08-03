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


def _hive_error_message(exc: Exception) -> str:
    """从 pyhive 的异常里提炼出一句人能看懂的错误(去掉几千字符的 Java 堆栈)。

    pyhive OperationalError 的 args[0] 是 TExecuteStatementResp,带 status.errorMessage;
    通常形如 "Error while compiling statement: FAILED: ParseException line ..."。
    """
    resp = exc.args[0] if getattr(exc, "args", None) else None
    msg = getattr(getattr(resp, "status", None), "errorMessage", None)
    text = msg or str(exc)
    # 只保留第一段(冒号+Java 堆栈之前),并压到一行
    text = text.split("org.apache.")[0].strip().splitlines()[0] if text else "未知错误"
    return text[:500] or "未知错误"


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
            try:
                if params:
                    pyformat_sql = sql.replace("%", "%%")
                    # 只替换已定义的参数名,避免误伤字符串里的 :xx(如时间格式 '%H:%i:%s')
                    for name in params:
                        pyformat_sql = re.sub(rf":{re.escape(name)}\b", f"%({name})s", pyformat_sql)
                    cursor.execute(pyformat_sql, params, async_=True)
                else:
                    cursor.execute(sql, async_=True)
                # 异步提交后轮询状态,超过 timeout_seconds 就取消——Hive 无语句级 MAX_EXECUTION_TIME,
                # 只能在客户端设截止时间并主动 cancel,避免长批处理拖垮数仓(见 QUERY/HIVE 超时治理)。
                self._await_completion(cursor, timeout_seconds)
            except Exception as e:  # noqa: BLE001 -- 把 Hive 的原始错误提炼成一句可读信息
                raise RuntimeError(f"Hive 执行失败:{_hive_error_message(e)}") from e
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

    @staticmethod
    def _await_completion(cursor, timeout_seconds: int) -> None:
        """轮询异步查询直到结束;超过 timeout_seconds 主动 cancel 并报错。

        Hive 无 MySQL 的语句级 MAX_EXECUTION_TIME,只能在客户端设截止时间轮询状态,
        超时即 cursor.cancel(),防止长批处理无限占用数仓资源。
        """
        from TCLIService.ttypes import TOperationState

        pending = (
            TOperationState.INITIALIZED_STATE,
            TOperationState.PENDING_STATE,
            TOperationState.RUNNING_STATE,
        )
        deadline = time.perf_counter() + max(1, int(timeout_seconds))
        interval = 1.0
        state = cursor.poll().operationState
        while state in pending:
            if time.perf_counter() >= deadline:
                try:
                    cursor.cancel()
                finally:
                    raise RuntimeError(f"Hive 查询超时(>{timeout_seconds}s)已被终止")
            time.sleep(min(interval, max(0.1, deadline - time.perf_counter())))
            interval = min(interval * 1.5, 5.0)  # 退避,降低轮询开销
            state = cursor.poll().operationState
        if state in (
            TOperationState.CANCELED_STATE,
            TOperationState.CLOSED_STATE,
            TOperationState.ERROR_STATE,
        ):
            raise RuntimeError(f"Hive 查询未成功(operationState={state})")

    def test_connection(self) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
        finally:
            conn.close()

"""Hive 连接器:直连 HiveServer2(Thrift),用 pyhive 提交与参数化绑定。

依赖(pyhive / thrift / thrift_sasl / pure-sasl)已在 requirements.txt 里;
若运行环境缺失,实例化即抛清晰错误,不影响 MySQL 主链路。

认证:`auth` 取自数据源的 extra(数据源页面可选 NONE / LDAP / NOSASL / CUSTOM / KERBEROS)。
LDAP / CUSTOM 会带上密码。**KERBEROS 需要额外的 kerberos/gssapi 依赖,当前未安装、未验证。**

超时:Hive 没有语句级超时,故异步提交后轮询状态,到点主动 cursor.cancel()(见 _await_completion)。

默认库:**建会话从不依赖任何库**。pyhive 在 Connection.__init__ 里无条件执行 `USE <db>`,
而 HiveServer2 的 OpenSession 并不需要它 —— 「能登进数仓」与「能进哪个库」是两件事。
故一律跳过那句 USE(见 _open_session),取数时再**尽力**进一次配的库(见 _connect):
进得去,不写库名的 SQL 照旧解析;进不去,会话依然可用,任务 SQL 写全限定表名即可。
"""
from __future__ import annotations

import contextlib
import re
import time
from contextlib import contextmanager
from typing import Any

from app.connectors.base import ConnectionConfig, DataSourceConnector, QueryResult

# 跳过 USE 后这个值用不上,但 pyhive 的构造函数要一个合法库名;给它 default(恒存在),
# 好让「万一将来 pyhive 改了构造流程、USE 没跳成」时退回从前的行为,而不是报个怪错。
_NEUTRAL_DATABASE = "default"
# 列库的超时预算。它只是元数据查询,又卡在一个有人在等的同步请求上,不该按取数的
# 长超时(Hive 默认 3600s)来等 —— 超了就当「列不出来」,不影响连通性判定。
_LIST_TIMEOUT_SECONDS = 15


def _hive_error_text(exc: Exception) -> str:
    """从 pyhive 的异常里提炼出**一行原文**(去掉几千字符的 Java 堆栈)。

    pyhive 把整个 TExecuteStatementResp 塞进 OperationalError.args[0],而它没有 __str__,
    于是 str(exc) 是一整段 Thrift repr。真正的原因可能落在两个地方,**都得看**:

    - `status.errorMessage`:多数编译期 / 运行期错误走这里;
    - `status.infoMessages`:HiveServer2 开了鉴权(HiveAccessControlException)时 errorMessage
      为空,原因只在 infoMessages[0],格式为 `*<异常类>:<消息>:<行>:<列>`。

    早先只读 errorMessage、拿不到就 `str(exc).split("org.apache.")[0]`,而那一刀正好切在
    `infoMessages=['*` 之后 —— 线上「没有 [USE] 权限」的报错因此被削成一段 Thrift 残骸。
    """
    resp = exc.args[0] if getattr(exc, "args", None) else None
    status = getattr(resp, "status", None)
    text = (getattr(status, "errorMessage", None) or "").strip()
    if not text:
        text = _from_info_messages(getattr(status, "infoMessages", None))
    if not text:
        # 兜底:非 Thrift 异常(网络 / SASL / 认证失败等),此时 str(exc) 本身就是可读的
        text = str(exc).strip()
    # 只保留第一段(Java 堆栈之前)的第一行;整段都是堆栈时保留原文,别切成空
    return (text.split("org.apache.")[0].strip() or text).partition("\n")[0].strip()


def _from_info_messages(info_messages) -> str:
    """从 infoMessages 里挑出首条异常消息:`*org.apache.…HiveSQLException:<消息>:28:27`。"""
    for raw in info_messages or []:
        m = re.match(r"\*?[\w.$]+(?:Exception|Error|Throwable):(.+?)(?::\d+:\d+)?$", raw or "")
        if m:
            return m.group(1).strip()
    return ""


def _is_permission_denied(text: str) -> bool:
    """这段原文是不是鉴权拒绝?**判定只看原文**,不看给用户的成品文案。

    早先反过来:绕不绕开由 _hive_error_message 的输出决定,于是改一句提示文案、
    或动一下 500 字符截断,都可能悄悄改掉建连的控制流。
    """
    return "HiveAccessControlException" in text or "Permission denied" in text


def _user_message(text: str) -> str:
    """一行原文 → 给用户看的那句(截断 + 鉴权类补一句「该找谁」)。

    鉴权报错不给出路,用户只会反复重试 —— 而这是平台改不动的外部授权。
    """
    hint = (
        "(该团队取数账号缺少这个库/表的权限,需数仓管理员为它授权)"
        if _is_permission_denied(text) else ""
    )
    return (text[:500] or "未知错误") + hint


def _hive_error_message(exc: Exception) -> str:
    """异常 → 给用户看的那一句。建连期要先拿原文做判定,故那里分两步走(见 _connect)。"""
    return _user_message(_hive_error_text(exc))


@contextmanager
def _readable_errors(action: str):
    """把这段代码里冒出的原始 Thrift 异常换成一句可读的 `Hive <action>:…`。

    执行、取列、取数都要这一层,否则 TExecuteStatementResp 的 repr 会原样冒到用户面前
    (线上「试跑失败:TExecuteStatementResp(status=…」即出自此)。建连期不用它:那里
    还要拿原文判定该不该绕开默认库,故自己分两步走(见 _connect)。
    """
    try:
        yield
    except Exception as e:  # noqa: BLE001 -- 原始 Thrift 异常转一句可读信息
        raise RuntimeError(f"Hive {action}:{_hive_error_message(e)}") from e


class HiveConnector(DataSourceConnector):
    engine = "hive"

    def __init__(self, config: ConnectionConfig):
        super().__init__(config)
        try:
            from pyhive import hive  # noqa: F401
        except ImportError as e:  # pragma: no cover - 依赖未装
            raise RuntimeError(
                "Hive 连接器需要 pyhive,当前运行环境未装。"
                "请按 backend/requirements.txt 重装依赖(pip install -r requirements.txt)。"
            ) from e
        self._hive = hive

    def _open_session(self):
        """开一个 HiveServer2 会话,**不进入任何库**。

        pyhive 在 Connection.__init__ 里无条件执行 `USE <db>`(pyhive/hive.py:282),于是
        「进不去某个库」被混成了「连不上」。线上就撞上了这个区别:dongyi7 的报错是
        HiveSQLException —— 说明会话**已经建立**、认证通过,只是随后那句 USE 被鉴权拒了;
        而它确实有别的库的权限。把库从建连里摘出去,「登得进数仓」才成为一件能单独验证的事。

        实现:构造期 pyhive 只取一次游标(就为那句 USE),把**那一次**的 execute 变成空操作
        即可,之后取的游标都是正常的。比自己拿 TCLIService 重写开会话流程稳妥得多;
        万一将来 pyhive 改了构造流程,最坏结果是那句 USE 照旧执行 —— 退回从前的行为,不会更糟。
        """

        class _NoUseConnection(self._hive.Connection):
            def cursor(self, *args, **kwargs):
                cur = super().cursor(*args, **kwargs)
                if not getattr(self, "_use_skipped", False):
                    self._use_skipped = True
                    cur.execute = lambda *a, **k: None  # 只吞构造期那一次 USE
                return cur

        auth = self.config.extra.get("auth", "NONE")  # NONE / LDAP / KERBEROS ...
        with _readable_errors("连接失败"):
            return _NoUseConnection(
                host=self.config.host,
                port=self.config.port,
                database=self.config.database or _NEUTRAL_DATABASE,  # 跳过 USE 后用不上
                username=self.config.username,
                password=self.config.password if auth in ("LDAP", "CUSTOM") else None,
                auth=auth,
            )

    def _connect(self):
        """取数用的会话:开好之后**尽力**进一次数据源配的库。

        进得去 → 不写库名的 SQL 照旧按那个库解析(与从前完全一致);
        进不去(鉴权拒绝)→ 不当失败:会话本身可用,任务 SQL 写全限定表名照样取数。
        平台不该因为一个「解析起点」把整条链路判死 —— 那正是线上那次故障的形状。

        鉴权之外的失败(库不存在等)照旧抛:那是配置错,悄悄放过只会让人对着
        「找不到表」猜半天。
        """
        conn = self._open_session()
        database = self.config.database
        if not database:
            return conn
        try:
            with contextlib.closing(conn.cursor()) as cur:
                cur.execute(f"USE `{database}`")
        except Exception as e:  # noqa: BLE001
            text = _hive_error_text(e)
            if not _is_permission_denied(text):
                conn.close()
                raise RuntimeError(f"Hive 连接失败:{_user_message(text)}") from e
            self.warn_bypassed(database, "无默认库的会话")
        return conn

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
            with _readable_errors("执行失败"):
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

    def test_connection(self) -> list[str]:
        """验身份 + 列出账号能访问的库。**压根不进入任何库**(见基类的契约说明)。

        「能登进数仓」= OpenSession 成功;「能取什么数」= SHOW DATABASES 的结果(Ranger 下它
        只返回该账号有权限的库)。两件事都不需要先进入某个具体库,所以这里不做 USE。

        这份清单是团队管理员配完账号最想要的答案:它到底能取什么数。
        """
        conn = self._open_session()
        try:
            cursor = conn.cursor()
            try:
                # 异步提交 + 轮询,与取数同一套超时机制:SHOW DATABASES 是元数据扫描,
                # 库多时可能很慢,而这是个有人在等的同步请求,不能没有上限地占着工作线程。
                cursor.execute("SHOW DATABASES", async_=True)
                self._await_completion(cursor, _LIST_TIMEOUT_SECONDS)
                rows = cursor.fetchall()
            except Exception as e:  # noqa: BLE001 -- 列不出(含超时)不算连不上
                self.warn_unlistable(_hive_error_message(e))
                return []
            return self.first_column(rows)
        finally:
            conn.close()

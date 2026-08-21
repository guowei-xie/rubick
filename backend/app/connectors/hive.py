"""Hive 连接器:直连 HiveServer2(Thrift),用 pyhive 提交与参数化绑定。

依赖(pyhive / thrift / thrift_sasl / pure-sasl)已在 requirements.txt 里;
若运行环境缺失,实例化即抛清晰错误,不影响 MySQL 主链路。

认证:`auth` 取自数据源的 extra(数据源页面可选 NONE / LDAP / NOSASL / CUSTOM / KERBEROS)。
LDAP / CUSTOM 会带上密码。**KERBEROS 需要额外的 kerberos/gssapi 依赖,当前未安装、未验证。**

超时:Hive 没有语句级超时,故异步提交后轮询状态,到点主动 cursor.cancel()(见 _await_completion)。

默认库:数据源上配的 database 是**首选**而不是**前提** —— pyhive 建连时就执行 `USE <db>`,
进不去就绕开它、改连 default(见 _connect),成败交还给任务 SQL。
「测试连接」反过来:先连中立的 default,压根不依赖那个配的库(见 test_connection)。
"""
from __future__ import annotations

import re
import time
from contextlib import contextmanager
from typing import Any

from app.connectors.base import ConnectionConfig, DataSourceConnector, QueryResult
from app.core.logging_setup import get_logger

logger = get_logger(__name__)

# 中立库:Hive 的 default 一定存在,且通常对所有账号开放。取数时它是绕开原库后的落脚点,
# 测试连接时它是首选 —— 两处都靠它把「能不能登进数仓」与「进不进得去某个具体库」解耦。
_NEUTRAL_DATABASE = "default"


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
        "(该团队取数账号缺少这个库/表的权限,需数仓管理员为账号授权,平台侧改配置绕不过去)"
        if _is_permission_denied(text) else ""
    )
    return (text[:500] or "未知错误") + hint


def _hive_error_message(exc: Exception) -> str:
    """异常 → 给用户看的那一句。建连期要先拿原文做判定,故那里分两步走(见 _connect)。"""
    return _user_message(_hive_error_text(exc))


def _candidates(*databases: str | None) -> list[str]:
    """候选库列表:按给定顺序去空、去重(两个候选常常是同一个库,不能连两次)。"""
    out: list[str] = []
    for db in databases:
        if db and db not in out:
            out.append(db)
    return out


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

    def _open(self, database: str):
        auth = self.config.extra.get("auth", "NONE")  # NONE / LDAP / KERBEROS ...
        return self._hive.Connection(
            host=self.config.host,
            port=self.config.port,
            database=database,
            username=self.config.username,
            password=self.config.password if auth in ("LDAP", "CUSTOM") else None,
            auth=auth,
        )

    def _open_first(self, candidates: list[str]) -> tuple[Any, str]:
        """按候选顺序连,返回 (连接, 用上的库)。

        只有**鉴权拒绝**才继续试下一个候选:密码错、库不存在、网络不可达一律当场抛 ——
        那是配置或故障,换个库重试只会把它们藏起来。全都失败则抛**第一个**错误,
        因为第一个候选才是调用方本来想连的库。
        """
        first: Exception | None = None
        for database in candidates:
            try:
                return self._open(database), database
            except Exception as e:  # noqa: BLE001 -- 原始 Thrift 异常转一句可读信息
                text = _hive_error_text(e)
                if not _is_permission_denied(text):
                    raise RuntimeError(f"Hive 连接失败:{_user_message(text)}") from e
                first = first or e
        raise RuntimeError(
            f"Hive 连接失败:{_user_message(_hive_error_text(first))}"
        ) from first

    def _connect(self):
        """取数用的建连。**注意 pyhive 在这一步就执行了 `USE <database>`**,所以库级鉴权失败
        (HiveAccessControlException:没有 [USE] 权限)发生在建连期、不在执行期。这带来两件事:

        1. 这里必须与执行期一样做错误提炼(见 _readable_errors);
        2. 数据源上配的默认库只能是**首选**,不能是**前提**。进不去就绕开它连 default,
           把成败交还给任务 SQL —— 一条写了全限定表名的 SQL 本就不需要那个库的 USE 权限,
           平台不该凭空给它加一道门槛(否则「试跑通过即代表上线能跑」这条承诺是反向破的:
           试跑挂在 USE 上,而上线后那条 SQL 其实跑得动)。
        """
        preferred = self.config.database or _NEUTRAL_DATABASE
        conn, used = self._open_first(_candidates(preferred, _NEUTRAL_DATABASE))
        if used != preferred:
            # 绕开是要能查的事实:它解释了「为什么这个任务里不写库名的表突然找不到了」
            logger.warning(
                "hive: 账号 %s 进不去库 %s,已绕开改连 %s;不写库名的 SQL 会解析不到表",
                self.config.username, preferred, used,
            )
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
        """验身份 + 列出账号能访问的库。**不依赖数据源配的默认库**(见基类的契约说明)。

        候选顺序与 _connect 相反:先连中立的 default;只有极严的授权策略把 default 也关了,
        才退到数据源配的那个库 —— 否则那种集群会让一个完全可用的账号被判成「连不上」。
        """
        conn, _ = self._open_first(
            _candidates(_NEUTRAL_DATABASE, self.config.database)
        )
        try:
            cur = conn.cursor()
            with _readable_errors("执行失败"):
                cur.execute("SHOW DATABASES")
                rows = cur.fetchall()
        finally:
            conn.close()
        return [str(r[0]) for r in rows if r and r[0] is not None]

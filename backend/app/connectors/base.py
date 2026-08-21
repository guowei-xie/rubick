"""数据源连接器抽象。

每种引擎(MySQL / Hive / 未来的 StarRocks 等)实现一个连接器。
执行只读参数化查询,返回列名 + 行数据。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool = False  # 是否因 MAX_RESULT_ROWS 被截断
    meta: dict = field(default_factory=dict)

    @property
    def row_count(self) -> int:
        return len(self.rows)


@dataclass(frozen=True)
class Credential:
    """连接目标库时使用的身份。

    只表达「我是谁」,不含 host/port/database —— 开发者能换身份,换不了连哪个库。

    owner_team_id 是这个身份归属的团队;None 表示数据源自带的**公共账号**
    (仅剩管理员测数据源连通性一个用处)。它同时就是审计要记的那个事实
    (见 QueryJob.run_as_team_id),所以取数链路不必再自己判断「这次算不算团队账号」。
    """

    username: str
    password: str | None = None
    owner_team_id: int | None = None

    @property
    def is_team_account(self) -> bool:
        return self.owner_team_id is not None


@dataclass
class ConnectionConfig:
    host: str
    port: int
    database: str | None
    username: str
    password: str | None
    extra: dict


class DataSourceConnector(ABC):
    """所有连接器的统一接口。"""

    engine: str = "base"

    def __init__(self, config: ConnectionConfig):
        self.config = config
        # 本次建连是否绕开了 config.database(账号进不去它),值为被绕开的库名。
        # 契约:每次建连开头清空,只在**绕开成功后**置上 —— 连接器只报这个事实,
        # 面向用户的说法由服务层给(services/credential_service.db_permission_note)。
        self.bypassed_database: str | None = None

    @abstractmethod
    def execute(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: int,
        max_rows: int,
    ) -> QueryResult:
        """执行只读参数化查询。params 使用 :name 命名占位符。"""

    @abstractmethod
    def test_connection(self) -> None:
        """连通性检查,失败抛异常。

        「连上了、但配的默认库进不去」不算失败 —— 那种账号照样能取数(SQL 写全限定表名即可),
        调用方从 bypassed_database 拿这个事实。
        """

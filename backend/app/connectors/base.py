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
        """连通性检查,失败抛异常。"""

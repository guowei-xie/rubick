"""数据源连接器抽象。

每种引擎(MySQL / Hive / 未来的 StarRocks 等)实现一个连接器。
执行只读参数化查询,返回列名 + 行数据。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.core.logging_setup import get_logger

log = get_logger(__name__)


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
    # 这套账号该**进哪个库**(空 = 用数据源配的 Database)。它属于身份而非地址:
    # 能进哪个库取决于账号的授权范围 —— 见 models/credential.py::entry_database
    entry_database: str | None = None

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
    def test_connection(self) -> list[str]:
        """验「这个账号能不能登进库」,失败抛异常;返回它能访问的库名列表。

        三条契约(**怎么实现是各引擎自己的事**,别把某个引擎的做法写进这里):

        1. **不得依赖 config.database**。那个库进不去不代表账号不可用 —— 任务 SQL 写全限定
           表名照样能跑,拿它当门槛会把可用的账号判成连不上(线上配的就是个不常用的应用库);
        2. 不碰任何业务表;
        3. **列不出库不算连不上**,返回空列表即可(引擎不支持、或账号没有列库权限)。

        库 / 表级权限的完整答案仍在试跑 —— 那才与上线后的取数同一套身份、同一条 SQL。
        """

    # ------------------------------------------------ 各引擎共用的小工具

    @staticmethod
    def first_column(rows) -> list[str]:
        """行集 → 第一列的字符串列表(SHOW DATABASES 之类的单列结果)。"""
        return [str(r[0]) for r in rows if r and r[0] is not None]

    def warn_unlistable(self, detail: str) -> None:
        """列不出库时留一条线索。文案共用一份:两个引擎各写一句迟早漂移。

        detail 由调用方给 —— 把引擎异常变成人话恰恰是各连接器自己的活。
        """
        log.warning("%s: 列出库失败(连接本身是好的):%s", self.engine, detail)

    def warn_bypassed(self, configured: str, used: str) -> None:
        """绕开了 config.database 时留一条线索 —— 它解释了「为什么这个任务里不写库名的表
        突然找不到了」。取数路径唯一的痕迹,故不能省(测试连接压根不碰那个库,不会走到这)。
        """
        log.warning(
            "%s: 账号 %s 进不去库 %s,已绕开改用 %s;不写库名的 SQL 会解析不到表",
            self.engine, self.config.username, configured, used,
        )

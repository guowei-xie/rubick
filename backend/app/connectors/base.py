"""数据源连接器抽象。

每种引擎(MySQL / Hive / 未来的 StarRocks 等)实现一个连接器。
执行只读参数化查询,返回列名 + 行数据。
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from itertools import islice
from typing import Any

from app.core.logging_setup import get_logger

log = get_logger(__name__)


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    # 是否因调用方给的 max_rows 被截断。**max_rows=None(不限)时永远是 False** ——
    # 取数结果的行数上限(MAX_RESULT_ROWS)已可关闭,见 config 里那一项的说明。
    truncated: bool = False
    meta: dict = field(default_factory=dict)

    @property
    def row_count(self) -> int:
        return len(self.rows)


def take_rows(rows: Iterator[Sequence], max_rows: int | None) -> tuple[list[tuple], bool]:
    """行迭代器 → (最多 max_rows 行, 是否还有更多)。max_rows=None 即全要。

    多探一行来判断「有没有被截断」:这是唯一能把「刚好 max_rows 行」与「超了」分开的办法。
    转成 tuple 也只在这里做 —— 只有留在内存里的行才需要脱离驱动的行对象,流式落盘的那条
    路上每行都拷一次纯属白烧(见各引擎 stream 的返回类型)。
    """
    if max_rows is None:
        return [tuple(r) for r in rows], False
    head = [tuple(r) for r in islice(rows, max_rows + 1)]
    return head[:max_rows], len(head) > max_rows


@contextmanager
def abandonable(rows: Iterator[Sequence], on_abandon: Callable[[], None]) -> Iterator[Iterator]:
    """行流 + 「调用方没取完就做这件事」。stream() 契约第 2 条的实现只在这里写一次。

    各引擎只需说清「了断」是什么(丢弃连接 / 取消操作),不必各自再记一遍「取完了没」——
    那个标志漏写不会报错、不会有测试变红,只会让一个 50 行的试跑安静地把几百万行拖完。
    """
    drained = False

    def tracked() -> Iterator:
        nonlocal drained
        yield from rows
        drained = True

    try:
        yield tracked()
    finally:
        if not drained:
            on_abandon()


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

    @abstractmethod
    def stream(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: int,
    ) -> AbstractContextManager[tuple[list[str], Iterator[Sequence]]]:
        """执行只读参数化查询,**边取边吐**:实现为 @contextmanager,yield (列名, 行迭代器)。

        params 使用 :name 命名占位符。这是每个引擎唯一要实现的取数入口 —— execute() 是它
        「全收进内存」的封装,行数上限也只在那一层表达(见 take_rows),各引擎不必各写一遍。
        行按驱动给的原样吐出(不必转 tuple):要留在内存里的那条路自己会转。

        两条契约:

        1. **连接的生命周期是这个上下文**。行迭代器只在 with 块内有效,退出即关连接;
        2. **调用方可以提前不取了**(取样、撞上上限)。此时别让剩下的行继续从服务端流过来
           —— 各引擎按自己的方式了断(取消操作、丢弃连接),这是「只要 50 行的试跑」
           不该把一个几百万行的结果集拖完的唯一保障。**用 abandonable() 包一层即可**,
           别自己记「取完了没」。
        """

    def execute(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: int,
        max_rows: int | None,
    ) -> QueryResult:
        """执行只读参数化查询,结果**全部进内存**。max_rows 必须显式给,None 即不限行数。

        规模已知很小的调用方用它(编辑器试跑取样、枚举值候选)。规模不可控的取数结果
        走 stream() + result_service.write_csv 直接落盘 —— 那条路的内存占用与行数无关。
        max_rows 刻意没有默认值:这个入口的默认值只能是「不限」,而那正是要避免的形状。
        """
        start = time.perf_counter()
        with self.stream(sql, params, timeout_seconds=timeout_seconds) as (columns, rows):
            taken, truncated = take_rows(rows, max_rows)
        return QueryResult(
            columns=columns,
            rows=taken,
            truncated=truncated,
            meta={"duration_ms": int((time.perf_counter() - start) * 1000)},
        )

    @abstractmethod
    def test_connection(self) -> list[str]:
        """验「这个账号能不能登进库」,失败抛异常;返回它能访问的库名列表。

        三条契约(**怎么实现是各引擎自己的事**,别把某个引擎的做法写进这里):

        1. **不得依赖任何具体库**。「能登进来」与「能进哪个库」是两件事:线上有账号进不去
           数据源配的库、连 default 也进不去,却确实有别的库的权限。拿某个库当门槛,既会把
           可用账号判成连不上,又让人无从知道自己能进哪个库(要连上才知道、要知道才连得上);
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

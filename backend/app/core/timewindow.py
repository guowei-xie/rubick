"""时间窗口:运营分析与审计检索共用的「朴素本地时间」口径。

库里所有 `created_at` 都是**朴素本地时间**(`TimestampMixin.server_default=func.now()`,
MySQL 与 SQLite 都落本地时区且不带 tzinfo)。带时区的入参若直接拿去和这些列比较,
SQLAlchemy 不会报错,只会静默比错 —— 所以 `naive()` 是所有时间入参的唯一入口。

窗口一律**半开区间** `[start, end)`。注意 `routes/audit.py` 的检索用的是闭区间
(`created_at <= end`),两边口径不同是既成事实:审计的导出结果已经按闭区间发出去过,
改它会让存量 CSV 对不上。本模块的窗口只服务于聚合,统一用半开 —— 半开区间下
「近 30 天」与「再往前 30 天」首尾相接、不重不漏,而闭区间会把边界那一秒算两次。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

# 默认回看天数。与前端的默认预设 `30d` 同一个数,改这里要同步改前端的 DEFAULT_PRESET。
DEFAULT_DAYS = 30
# 单次查询最多回看多久。挡的是 `?days=100000` 这种把全表拖进内存的请求。
MAX_DAYS = 730


def naive(dt: datetime | None) -> datetime | None:
    """带时区的入参统一转本地再去掉 tzinfo;朴素时间与 None 原样返回。"""
    return dt if dt is None or dt.tzinfo is None else dt.astimezone().replace(tzinfo=None)


@dataclass(frozen=True)
class Window:
    """一个半开区间 `[start, end)`。两端都是朴素本地时间。"""

    start: datetime
    end: datetime

    @property
    def days(self) -> int:
        """区间长度(天,向上取整)。只用于展示与 `previous()`,不参与过滤。"""
        secs = (self.end - self.start).total_seconds()
        return max(1, int((secs + 86399) // 86400))


def start_of_today() -> datetime:
    """今天 00:00(朴素本地时间),给「当天」这类自然日边界用。"""
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)


def resolve_window(
    start: datetime | None = None,
    end: datetime | None = None,
    days: int | None = None,
) -> Window:
    """把三个可选入参解算成一个确定的半开区间。**优先级只在这里判一次。**

    - 同时给了 `start`/`end` 与 `days` 时**忽略 days** —— 显式区间比相对天数更具体。
    - 只给 `start`:end 取 now()。只给 `end`:start 取 end - days。
    - 都不给:最近 `days`(默认 30)天。

    两个入参都给、但 start >= end 时按「空窗口」处理(start == end),而不是抛错:
    这类请求来自前端日期控件的中间态,一个空结果比一个 400 更容易解释。
    """
    start, end = naive(start), naive(end)
    # `days if is None` 而不是 `days or`:0 是假值,`days=0` 会被 `or` 悄悄换成默认的 30,
    # 而调用方的本意是「最短的窗口」。钳在 [1, MAX_DAYS]。
    span = timedelta(days=min(max(DEFAULT_DAYS if days is None else days, 1), MAX_DAYS))
    if start is not None and end is not None:
        return Window(start, max(start, end))
    if start is not None:
        return Window(start, max(start, datetime.now()))
    if end is not None:
        return Window(end - span, end)
    now = datetime.now()
    return Window(now - span, now)


def previous(window: Window) -> Window:
    """紧邻在前的等长窗口,用于环比与留存。

    半开区间下它与 `window` 首尾相接(prev.end == window.start),同一条记录不会被两个
    窗口同时算到 —— 这正是本模块不用闭区间的原因。
    """
    span = window.end - window.start
    return Window(window.start - span, window.start)

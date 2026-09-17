"""时间窗口的解算口径:三个可选入参怎么变成一个确定的半开区间。

它是运营分析全部聚合的入口,也是审计检索的时区转换来源,所以这几条边界值得逐条钉住:
  · 显式区间比相对天数更具体 —— 同时给了就忽略 days;
  · 半开区间下相邻两窗首尾相接、不重不漏,这正是环比与留存能算对的前提;
  · 带时区的入参必须转成朴素本地时间,否则与朴素列的比较会静默比错;
  · start > end 这种前端中间态按空窗口处理,不抛错。
"""
from datetime import datetime, timedelta, timezone

from app.core.timewindow import (
    DEFAULT_DAYS,
    MAX_DAYS,
    Window,
    naive,
    previous,
    resolve_window,
)


def test_naive_strips_tzinfo_after_converting_to_local():
    aware = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    got = naive(aware)
    assert got.tzinfo is None
    # 转的是本地时间而不是粗暴地 replace(tzinfo=None):UTC 12:00 在东八区是 20:00
    assert got == aware.astimezone().replace(tzinfo=None)


def test_naive_passes_through_naive_and_none():
    plain = datetime(2026, 1, 1, 12, 0)
    assert naive(plain) is plain
    assert naive(None) is None


def test_explicit_range_wins_over_days():
    """同时给了 start/end 与 days —— days 被忽略,不是取交集也不是取并集。"""
    w = resolve_window(datetime(2026, 1, 1), datetime(2026, 1, 8), days=90)
    assert w.start == datetime(2026, 1, 1)
    assert w.end == datetime(2026, 1, 8)
    assert w.days == 7


def test_only_end_given_backfills_start_by_days():
    w = resolve_window(None, datetime(2026, 3, 1), days=10)
    assert w.start == datetime(2026, 2, 19)
    assert w.end == datetime(2026, 3, 1)


def test_only_start_given_runs_to_now():
    start = datetime.now() - timedelta(days=3)
    w = resolve_window(start)
    assert w.start == start
    assert (datetime.now() - w.end).total_seconds() < 5


def test_default_is_last_30_days():
    w = resolve_window()
    assert w.days == DEFAULT_DAYS
    assert (datetime.now() - w.end).total_seconds() < 5


def test_days_is_clamped():
    """`?days=100000` 不该把全表拖进内存;`days=0` 不该退化成空窗口。"""
    assert resolve_window(days=100000).days == MAX_DAYS
    assert resolve_window(days=0).days == 1
    assert resolve_window(days=-5).days == 1


def test_reversed_range_collapses_to_empty_window():
    """前端日期控件的中间态:一个空结果比一个 400 更容易解释。"""
    w = resolve_window(datetime(2026, 1, 10), datetime(2026, 1, 1))
    assert w.start == w.end == datetime(2026, 1, 10)


def test_previous_window_is_contiguous_and_equal_length():
    """半开区间的全部意义:prev.end == cur.start,同一条记录不会被两个窗口同时算到。"""
    w = Window(datetime(2026, 2, 1), datetime(2026, 3, 1))
    p = previous(w)
    assert p.end == w.start
    assert p.start == datetime(2026, 1, 4)
    assert p.end - p.start == w.end - w.start


def test_aware_inputs_are_normalized_before_comparison():
    """带时区入参进来,出去的区间两端都是朴素的 —— 否则和 created_at 比会静默出错。"""
    w = resolve_window(
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    assert w.start.tzinfo is None and w.end.tzinfo is None

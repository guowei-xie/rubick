"""运营分析纯函数层的口径:失败归因、分位数、日期归一。

这一层不碰 db,所以每条规则都测得起。值得逐条钉住的是那些「改了也不报错、只是数字悄悄变了」
的地方:分桶的优先级顺序、空样本返回 None 还是 0、跨方言的日期形状。
"""
from datetime import date, datetime

import pytest

from app.services.analytics_metrics import (
    BUCKET_OTHER,
    FAILURE_LABELS,
    FAILURE_RULES,
    classify_error,
    hourly_peak_concurrency,
    percentiles,
    to_date,
)
from app.services.query_service import _RECLAIM_ERROR


# ---------------------------------------------------------------- 失败归因


@pytest.mark.parametrize(
    "text,expected",
    [
        # interrupted:用常量本身,而不是抄一遍文案 —— 文案改了这条会失败,正是我们要的
        (_RECLAIM_ERROR, "interrupted"),
        # credential:平台在入队/执行前抛的,下一步是去催团队配账号
        ("团队《增长组》尚未配置数据源《数仓》的取数账号,请联系团队管理员", "credential"),
        ("任务上线被拦下:团队《甲队》尚未配置…", "credential"),
        # param:平台的参数校验与只读网关,还没到数据库
        ("缺少必填参数:统计日期", "param"),
        ("参数「门店」需为数值,收到:abc", "param"),
        ("只允许 SELECT / WITH 查询", "param"),
        ("检测到危险关键字:DROP", "param"),
        # timeout
        ("Hive 查询超时(>3600s)已被终止", "timeout"),
        ("Query execution was interrupted, maximum statement execution time exceeded", "timeout"),
        ("java.sql.SQLTimeoutException: query timed out", "timeout"),
        # permission
        ("(1045, \"Access denied for user 'x'@'10.0.0.5'\")", "permission"),
        ("ERROR 1142 (42000): SELECT command denied to user", "permission"),
        ("org.apache.hadoop.hive.ql.metadata.AuthorizationException", "permission"),
        # syntax
        ("(1064, \"You have an error in your SQL syntax\")", "syntax"),
        ("(1054, \"Unknown column 'foo' in 'field list'\")", "syntax"),
        ("FAILED: SemanticException [Error 10001]: Table not found dw.orders", "syntax"),
        # connection
        ("(2003, \"Can't connect to MySQL server on '10.0.0.5'\")", "connection"),
        ("thrift.transport.TTransport.TTransportException: Could not connect", "connection"),
        ("(2006, 'MySQL server has gone away')", "connection"),
        # other
        ("某种谁也没见过的错误", BUCKET_OTHER),
        ("", BUCKET_OTHER),
        (None, BUCKET_OTHER),
    ],
)
def test_classify_error_buckets(text, expected):
    assert classify_error(text) == expected


def test_timeout_wins_over_connection():
    """Hive 超时后被 cancel,驱动常常再抛一句连接层的话 —— 根因是超时,不是连不上。

    这条顺序若被「顺手整理」掉,超时会被大面积记成连接故障,而两者的处理完全不同
    (前者改 SQL 或调超时,后者找 DBA)。
    """
    assert classify_error(
        "Hive 查询超时(>3600s)已被终止; thrift transport: Broken pipe"
    ) == "timeout"


def test_platform_errors_win_over_engine_keywords():
    """平台自己的文案优先于引擎关键词 —— 它更靠近根因,且文案完全可控不会误伤。"""
    assert classify_error("缺少必填参数:开始日期(connection refused 只是巧合)") == "param"


def test_matching_is_case_insensitive():
    assert classify_error("ACCESS DENIED FOR USER") == "permission"
    assert classify_error("access denied for user") == "permission"


def test_every_bucket_has_a_label():
    """前端只消费返回的中文标签;漏一个桶就会在页面上露出英文 code。"""
    for code, _label, _kw in FAILURE_RULES:
        assert FAILURE_LABELS[code]
    assert FAILURE_LABELS[BUCKET_OTHER]


def test_bucket_codes_are_unique():
    codes = [c for c, _, _ in FAILURE_RULES]
    assert len(codes) == len(set(codes))


# ---------------------------------------------------------------- 分位数


def test_percentiles_on_empty_returns_none_not_zero():
    """没有样本时给 0,会让「P95 耗时 0 毫秒」看起来像平台快得惊人。"""
    assert percentiles([]) == {50: None, 90: None, 95: None}
    assert percentiles([None, None]) == {50: None, 90: None, 95: None}


def test_percentiles_single_value():
    assert percentiles([42]) == {50: 42, 90: 42, 95: 42}


def test_percentiles_use_nearest_rank_not_interpolation():
    """结果必须是样本里**真实存在**的值 —— 看板上的 P95 会被读作「真有一次跑了这么久」。"""
    xs = [10, 20, 30, 40]
    got = percentiles(xs, ps=(50, 100))
    assert got[50] in xs and got[100] == 40
    # 线性插值会在这里给出 25(样本里没有这个数)
    assert got[50] == 20


def test_percentiles_ignore_none_values():
    assert percentiles([1, None, 3], ps=(100,))[100] == 3


# ---------------------------------------------------------------- 日期归一


def test_to_date_accepts_both_dialect_shapes():
    """SQLite 的 DATE() 给字符串,MySQL 给 date —— 两种都得认。"""
    assert to_date("2026-01-01") == date(2026, 1, 1)
    assert to_date(date(2026, 1, 1)) == date(2026, 1, 1)
    assert to_date(datetime(2026, 1, 1, 13, 30)) == date(2026, 1, 1)


# ---------------------------------------------------------------- 峰值并发

D0 = datetime(2026, 9, 24, 0, 0)


def _at(h, m=0):
    return D0.replace(hour=h, minute=m)


def test_peak_concurrency_counts_overlap():
    got = hourly_peak_concurrency(
        [(_at(9, 0), _at(9, 30)), (_at(9, 10), _at(9, 20)), (_at(9, 40), _at(9, 50))], D0, 11
    )
    assert got[9] == 2
    assert got[8] == 0 and got[10] == 0


def test_peak_concurrency_back_to_back_is_not_overlap():
    """一个槽位接力跑两条(前一条 10:00 结束、后一条 10:00 开始)不能读成并发 2。"""
    got = hourly_peak_concurrency([(_at(9, 30), _at(10, 0)), (_at(10, 0), _at(10, 30))], D0, 11)
    assert got[9] == 1 and got[10] == 1


def test_peak_concurrency_long_job_spans_every_hour_it_crosses():
    got = hourly_peak_concurrency([(_at(8, 50), _at(11, 10))], D0, 12)
    assert got[8:12] == [1, 1, 1, 1]
    assert got[7] == 0


def test_peak_concurrency_ignores_bad_intervals():
    got = hourly_peak_concurrency([(None, _at(9)), (_at(9, 30), None), (_at(9, 30), _at(9, 0))], D0, 10)
    assert got == [0] * 10

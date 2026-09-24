"""运营分析的**纯函数**层:失败归因分桶、分位数、日期归一。

刻意与 analytics_service 分开:这一层不碰 db、不碰 scope,输入输出都是普通 Python 值。
失败归因的规则表注定要长期迭代(错误文案来自三种引擎 + 平台自己),而只有纯函数才测得动
—— 把规则写进 SQL 的下场是每改一次口径就要改一条查询,且 MySQL 与 SQLite 的 LIKE
大小写敏感性不同,两边还会得出不同的分类。
"""
from __future__ import annotations

import math
from datetime import date, datetime

# ---------------------------------------------------------------- 失败归因

# 未命中任何规则时的兜底桶。它的样例会被一并返回给前端(见 analytics_service),
# 这是规则表能演进的唯一机制 —— 否则 other 会永远是最大的桶,且没人知道该往里加什么。
BUCKET_OTHER = "other"

# (桶名, 中文标签, 关键词) —— **顺序即优先级,首个命中即停**。
#
# 顺序不是随手排的:一条错误里可以同时出现多个线索(Hive 超时被 cancel 后,驱动往往再抛一句
# 连接相关的话),谁在前谁就赢。排序原则是「**离根因最近的先判**」:
#   · interrupted 最先 —— 它是平台自己写的一句话,不是数据库说的,最确定;
#   · credential / param 紧随 —— 同样是平台自己抛的,文案可控,不会误伤;
#   · timeout 在 connection 之前 —— 超时被取消后常伴随连接层的报错,但根因是超时;
#   · syntax 在 connection 之前 —— 同理,语法错误不会伪装成连接问题,反之则会。
#
# 关键词一律小写,匹配前把错误文本 lower() —— 中文不受影响,英文才对得上
# (MySQL 的 `Access denied` 与 Hive 的 `access denied` 是同一回事)。
FAILURE_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "interrupted",
        "运行被中断",
        # 不抄字符串:直接对齐 query_service._RECLAIM_ERROR 的特征片段。
        # 完整文案见那里,改文案时这里的片段要跟着改 —— test_analytics_metrics 用常量本身断言。
        ("取数进程在本次运行期间被中断",),
    ),
    (
        "credential",
        "缺少取数账号",
        # 平台自己抛的 CredentialRequiredError。单独成桶而不并入 param:它的下一步动作
        # 完全不同(去催团队管理员配账号,而不是让业务方改参数),治理板也在追同一件事。
        ("尚未配置", "取数账号", "任务上线被拦下"),
    ),
    (
        "param",
        "参数或 SQL 被平台拦下",
        # params_service 的校验 + sql_gateway 的只读网关,都是平台在把关,没到数据库
        ("缺少必填参数", "需为数值", "只允许", "不允许", "检测到危险关键字", "sql 不能为空"),
    ),
    (
        "timeout",
        "查询超时",
        (
            "超时", "timeout", "timed out", "cancelled", "canceled",
            # MySQL 的 max_execution_time 到点时说的是这一句,**整句不含 timeout 字样**
            # (「Query execution was interrupted, maximum statement execution time exceeded」)。
            # 少了它,线上最常见的一种超时会整批落进 other。
            "execution time exceeded",
        ),
    ),
    (
        "permission",
        "库账号权限不足",
        (
            "access denied", "permission denied", "1045", "1142", "1044",
            "not allowed", "insufficient privileges", "authorizationexception",
        ),
    ),
    (
        "syntax",
        "SQL 写法或对象不存在",
        (
            "syntax", "1064", "unknown column", "1054", "1146", "1051",
            "doesn't exist", "does not exist", "semanticexception", "parseexception",
            "table not found", "invalid table", "unknown database", "1049",
        ),
    ),
    (
        "connection",
        "连不上数据源",
        (
            "can't connect", "cannot connect", "connection refused", "connection reset",
            "broken pipe", "lost connection", "gone away", "2003", "2006", "2013",
            "thrift", "no route to host", "name or service not known", "unreachable",
        ),
    ),
)

FAILURE_LABELS: dict[str, str] = {code: label for code, label, _ in FAILURE_RULES}
FAILURE_LABELS[BUCKET_OTHER] = "其它"


def classify_error(text: str | None) -> str:
    """把一条错误文本归到一个桶。空文本与未命中都进 `other`。

    只读 `QueryJob.error`(已过 credential_service.redact 脱敏),**不要**拿审计里的原文来分
    —— 那一份带库账号名,团队管理员视角下会漏。
    """
    if not text:
        return BUCKET_OTHER
    low = text.lower()
    for code, _label, keywords in FAILURE_RULES:
        if any(k in low for k in keywords):
            return code
    return BUCKET_OTHER


# ---------------------------------------------------------------- 分位数

# 一次聚合最多把多少行拉进内存(算分位、数排队)。窗口内成功运行上万时只看前这么多条。
PERCENTILE_SAMPLE_CAP = 50_000


def percentiles(values, ps=(50, 90, 95)) -> dict[int, int | None]:
    """最近秩(nearest-rank)分位数。样本为空时每一项都是 None,**不是 0**。

    在 Python 里算而不是交给 SQL:MySQL 5.7 没有窗口函数、SQLite 没有 PERCENTILE_CONT,
    两边不通用。窗口内的样本是几千个 int,拉回内存的代价可以忽略。

    用最近秩而不是线性插值:插值会产出一个样本里**不存在**的耗时值,而运营看板上的
    「P95 = 12.4 秒」被读作「真有一次跑了 12.4 秒」。宁可给一个真实发生过的数。
    """
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return {p: None for p in ps}
    n = len(xs)
    out: dict[int, int | None] = {}
    for p in ps:
        # 最近秩:rank = ceil(p/100 * n),取第 rank 个(1-based)。p=100 时正好取到最大值。
        rank = max(1, min(n, math.ceil(p / 100 * n)))
        out[p] = int(xs[rank - 1])
    return out


# ---------------------------------------------------------------- 日期归一


def to_date(d: date | datetime | str) -> date:
    """把 `func.date()` 的返回值归一成 date。

    **这是一处真实的方言差**:SQLite 的 `DATE()` 返回字符串 `'2026-01-01'`,MySQL 的返回
    `datetime.date`。不归一的话,本机开发一切正常、线上一拼日期键就 AttributeError —— 而这类只在一种库上
    现形的 bug 最费时间。所有日粒度的聚合结果都先过这里。
    """
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return date.fromisoformat(str(d)[:10])

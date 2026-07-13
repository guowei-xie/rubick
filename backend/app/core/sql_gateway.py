"""SQL 安全网关。

执行前对 SQL 做静态校验:仅允许单条 SELECT/WITH 查询,拒绝多语句、
DDL/DML 与危险语句。用 sqlglot 做方言感知的解析,解析失败再退回关键字兜底。

注意:这是**纵深防御的一层**,数据库侧仍必须用只读账号做最终兜底。
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp

from app.core.exceptions import SqlSafetyError

# 本平台方言名与 sqlglot 方言名一致;未知方言回退 None(仅关键字兜底)
_SQLGLOT_DIALECTS = {"hive", "mysql"}

# 允许的顶层语句类型(只读查询)
_ALLOWED_TOP = (exp.Select, exp.Union, exp.With, exp.Subquery)

# 兜底关键字黑名单(解析失败时使用)
_DENY_KEYWORDS = {
    "insert", "update", "delete", "merge", "drop", "truncate", "alter",
    "create", "replace", "grant", "revoke", "call", "load", "export",
    "set", "use", "attach", "copy",
}


def validate_readonly(sql: str, dialect: str) -> None:
    """校验 SQL 是只读单语句;不通过则抛 SqlSafetyError。"""
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise SqlSafetyError("SQL 不能为空")

    glot_dialect = dialect if dialect in _SQLGLOT_DIALECTS else None
    try:
        statements = sqlglot.parse(sql, dialect=glot_dialect)
    except Exception:  # 解析失败 → 关键字兜底
        _keyword_guard(sql)
        return

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SqlSafetyError("只允许执行单条查询语句")

    stmt = statements[0]
    if not isinstance(stmt, _ALLOWED_TOP):
        raise SqlSafetyError(f"只允许 SELECT 查询,检测到:{stmt.key.upper()}")

    # WITH 的主体也必须是 SELECT/UNION
    if isinstance(stmt, exp.With) and not isinstance(stmt.this, (exp.Select, exp.Union)):
        raise SqlSafetyError("CTE 主体必须是 SELECT 查询")

    # 语句内部不允许出现任何写/DDL 表达式
    for node in stmt.walk():
        node = node[0] if isinstance(node, tuple) else node
        if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Drop,
                             exp.Create, exp.Alter, exp.Command)):
            raise SqlSafetyError("语句包含非只读操作")


def _keyword_guard(sql: str) -> None:
    lowered = sql.lower()
    if ";" in sql.rstrip(";"):
        raise SqlSafetyError("不允许多语句执行")
    first = lowered.lstrip().split(None, 1)[0] if lowered.strip() else ""
    if first not in ("select", "with"):
        raise SqlSafetyError("只允许 SELECT / WITH 查询")
    for kw in _DENY_KEYWORDS:
        if f" {kw} " in f" {lowered} ":
            raise SqlSafetyError(f"检测到危险关键字:{kw}")

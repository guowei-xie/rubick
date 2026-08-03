"""参数校验与绑定:把用户填入的值按模板参数定义校验,产出可安全绑定的 dict。

一切参数都是字符串替换,只分两种、一律必填:
  single —— 单值,绑定为 str
  list   —— 值列表,绑定为 list[str],执行前由 expand_list_params 展开成 IN (...)
"""
from __future__ import annotations

import re
from typing import Any

from app.core.exceptions import RubicError
from app.schemas.common import ParamDef


def detect_is_list(sql: str, name: str) -> bool:
    """按 SQL 写法判定变量是否为「值列表」:`字段 IN (:x)` / `NOT IN (:x)` → True,其余 → False。

    只判 list / single,不再区分 in / not_in 方向。前端 isListVar 与本函数保持等价。
    """
    if not name:
        return False
    esc = re.escape(name)
    return bool(re.search(rf"\bIN\s*\(\s*:{esc}\b", sql or "", re.I))


def _is_empty(val: Any) -> bool:
    """未填判定:None / 空串 / 空序列。校验与预览共用同一口径。"""
    return val is None or val == "" or (isinstance(val, (list, tuple)) and len(val) == 0)


def _coerce(pd: ParamDef, val: Any) -> Any:
    """按 kind 归一绑定值:list → [str…](执行前由 expand_list_params 展开),single → str。"""
    if pd.kind == "list":
        items = list(val) if isinstance(val, (list, tuple)) else [val]
        return [str(x) for x in items]
    return str(val)


def validate_and_bind(param_defs: list[dict | ParamDef], values: dict[str, Any]) -> dict[str, Any]:
    bound: dict[str, Any] = {}
    for raw in param_defs:
        pd = raw if isinstance(raw, ParamDef) else ParamDef(**raw)
        val = values.get(pd.name)
        if _is_empty(val):
            raise RubicError(f"缺少必填参数:{pd.label or pd.name}")
        bound[pd.name] = _coerce(pd, val)
    return bound


def expand_list_params(sql: str, bound: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """把值为 list 的绑定参数展开成 :x__0, :x__1, … 逐值绑定。非 list 参数原样透传。

    最小占位符展开:仅把裸 `:x` 替换为逗号连接的 `:x__0, :x__1, …`。作者已在 SQL 写好括号
    (`字段 IN (:x)` / `NOT IN (:x)`),替换后天然得到 `IN (:x__0, :x__1, …)`,方向由作者的
    IN/NOT IN 决定。不再做运算符嗅探、`= → IN` 升级或方向处理。
    """
    new_sql = sql
    out: dict[str, Any] = {}
    for name, val in bound.items():
        if not isinstance(val, (list, tuple)):
            out[name] = val
            continue

        esc = re.escape(name)
        keys = [f"{name}__{i}" for i in range(len(val))]
        inner = ", ".join(f":{k}" for k in keys)
        for k, v in zip(keys, val):
            out[k] = v
        # 用函数替换(而非字符串),避免 inner 里的 : / 数字被当作组引用解释
        new_sql = re.sub(rf":{esc}\b", lambda _m: inner, new_sql)
    return new_sql, out


def render_sql(sql: str, bound: dict[str, Any]) -> str:
    """把绑定参数代入 SQL,生成「最终发给数据库」的可读 SQL(仅供查阅,不用于执行)。

    字符串加单引号(转义内部单引号),数字原样,None → NULL。与执行时参数化绑定的结果一致。
    """
    def _lit(v: Any) -> str:
        if v is None:
            return "NULL"
        if isinstance(v, bool):
            return "TRUE" if v else "FALSE"
        if isinstance(v, (int, float)):
            return str(v)
        return "'" + str(v).replace("'", "''") + "'"

    out = sql
    for name in sorted(bound, key=len, reverse=True):  # 长名先替,避免 x 匹配到 x__0 前缀
        lit = _lit(bound[name])
        out = re.sub(rf":{re.escape(name)}\b", lambda _m, s=lit: s, out)
    return out


def preview_sql(sql: str, param_defs: list[dict | ParamDef], values: dict[str, Any]) -> str:
    """预览「即将执行」的 SQL:代入已填值,未填变量原样保留 :x。

    供作者 review 写法用,不执行、不校验必填。与 validate_and_bind 共用 _is_empty / _coerce,
    区别只在:未填 → 跳过(占位符原样留在 SQL 里)而非报错。已填值经与执行态相同的
    expand_list_params + render_sql 渲染,保证与实际运行一致。
    """
    bound: dict[str, Any] = {}
    for raw in param_defs:
        pd = raw if isinstance(raw, ParamDef) else ParamDef(**raw)
        val = values.get(pd.name)
        if _is_empty(val):
            continue  # 未填 → 跳过,占位符原样留在 SQL 里
        bound[pd.name] = _coerce(pd, val)
    sql2, bound2 = expand_list_params(sql, bound)
    return render_sql(sql2, bound2)

"""参数校验与绑定:把用户填入的值按模板参数定义校验,产出可安全绑定的 dict。

取值方式(type):
  text / number / date  —— 单值
  enum                  —— 从候选值里选一个(单选)
  multi_enum            —— 从候选值里选多个(多选),绑定为 list,执行前展开成 IN (...)
  date_range            —— 区间,展开为 :name_start / :name_end
  number_range          —— 区间,展开为 :name_min / :name_max
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.core.exceptions import RubicError
from app.schemas.common import ParamDef

# 兼容旧类型名
_ALIASES = {"string": "text", "daterange": "date_range"}


class _Unfiltered:
    """哨兵:标记「不选=全部」的空参数,执行前把其筛选谓词失效掉。"""


UNFILTERED = _Unfiltered()


def _kind(t: str | None) -> str:
    return _ALIASES.get(t or "text", t or "text")


def _num(v: Any) -> Any:
    return float(v) if "." in str(v) else int(v)


def _date(v: Any) -> str:
    return str(date.fromisoformat(str(v)))


def _pair(v: Any, label: str) -> tuple:
    if not (isinstance(v, (list, tuple)) and len(v) == 2):
        raise RubicError(f"范围参数 {label} 需为 [起, 止] 两个值")
    return v[0], v[1]


def validate_and_bind(param_defs: list[dict | ParamDef], values: dict[str, Any]) -> dict[str, Any]:
    bound: dict[str, Any] = {}
    for raw in param_defs:
        pd = raw if isinstance(raw, ParamDef) else ParamDef(**raw)
        kind = _kind(pd.type)
        label = pd.label or pd.name
        val = values.get(pd.name, pd.default)

        empty = val is None or val == "" or (isinstance(val, (list, tuple)) and len(val) == 0)

        if empty:
            # 选填的枚举/列表筛选留空 = 不筛选该字段(谓词中和);兼容旧的 all_when_empty
            if pd.all_when_empty or (kind in ("enum", "multi_enum") and not pd.required):
                bound[pd.name] = UNFILTERED
                continue
            if pd.required or kind in ("date_range", "number_range"):
                raise RubicError(f"缺少必填参数:{label}")
            bound[pd.name] = None
            continue

        try:
            if kind == "date_range":
                a, b = _pair(val, label)
                bound[f"{pd.name}_start"], bound[f"{pd.name}_end"] = _date(a), _date(b)
            elif kind == "number_range":
                a, b = _pair(val, label)
                bound[f"{pd.name}_min"], bound[f"{pd.name}_max"] = _num(a), _num(b)
            elif kind == "number":
                bound[pd.name] = _num(val)
            elif kind == "date":
                bound[pd.name] = _date(val)
            elif kind == "enum":
                chosen = val[0] if isinstance(val, list) else val
                if pd.options and str(chosen) not in set(pd.options):
                    raise RubicError(f"参数 {label} 取值非法:{chosen}")
                bound[pd.name] = chosen
            elif kind == "multi_enum":
                items = list(val) if isinstance(val, (list, tuple)) else [val]
                if pd.options:
                    allowed = set(pd.options)
                    for it in items:
                        if str(it) not in allowed:
                            raise RubicError(f"参数 {label} 取值非法:{it}")
                bound[pd.name] = items  # list → 执行前由 expand_list_params 展开
            else:  # text
                bound[pd.name] = str(val)
        except RubicError:
            raise
        except Exception as e:
            raise RubicError(f"参数 {label} 格式错误:{e}") from e
    return bound


def expand_list_params(sql: str, bound: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """把值为 list 的绑定参数展开成正选 IN,并处理「不筛选」哨兵。

    分析师照常写 `字段 = :var` 或 `字段 IN (:var)`,统一生成:
      字段 IN (:var__0, :var__1, ...)
    UNFILTERED(旧数据的可选留空)→ 该字段谓词中和为 1=1。非 list 参数原样透传。
    """
    new_sql = sql
    out: dict[str, Any] = {}
    for name, val in bound.items():
        esc = re.escape(name)
        if isinstance(val, _Unfiltered):
            new_sql = re.sub(rf"[\w.`]+\s*=\s*:{esc}\b", "1=1", new_sql, flags=re.I)
            # (?:NOT\s+)? 让 `字段 NOT IN (:var)` 也整体中和为 1=1;否则 [\w.`]+ 会把 NOT
            # 误当字段,只替换 "NOT IN (:var)" 而残留真正的列名,生成非法 SQL(如 `user_id 1=1`)
            new_sql = re.sub(rf"[\w.`]+\s+(?:NOT\s+)?IN\s*\(\s*:{esc}\s*\)", "1=1", new_sql, flags=re.I)
            new_sql = re.sub(rf":{esc}\b", "NULL", new_sql)  # 兜底
            continue
        if not isinstance(val, (list, tuple)):
            out[name] = val
            continue

        if len(val) == 0:
            pred = lambda _col: "1=0"  # 空正选=无匹配(必填空已 raise,此为兜底)
            inner = ""
        else:
            keys = [f"{name}__{i}" for i in range(len(val))]
            inner = ", ".join(f":{k}" for k in keys)
            for k, v in zip(keys, val):
                out[k] = v
            pred = lambda col, _i=inner: f"{col} IN ({_i})"
        # 1) 字段 = :var   2) 字段 IN (:var)   —— 捕获字段名生成谓词;3) 裸 :var 兜底
        new_sql = re.sub(rf"([\w.`]+)\s*=\s*:{esc}\b", lambda m: pred(m.group(1)), new_sql, flags=re.I)
        new_sql = re.sub(rf"([\w.`]+)\s+IN\s*\(\s*:{esc}\s*\)", lambda m: pred(m.group(1)), new_sql, flags=re.I)
        new_sql = re.sub(rf":{esc}\b", "(NULL)" if len(val) == 0 else f"({inner})", new_sql)
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

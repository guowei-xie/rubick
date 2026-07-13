"""参数校验与绑定:把用户填入的值按模板参数定义校验,产出可安全绑定的 dict。"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.core.exceptions import RubicError
from app.schemas.common import ParamDef


def validate_and_bind(param_defs: list[dict | ParamDef], values: dict[str, Any]) -> dict[str, Any]:
    bound: dict[str, Any] = {}
    for raw in param_defs:
        pd = raw if isinstance(raw, ParamDef) else ParamDef(**raw)
        val = values.get(pd.name, pd.default)

        if val is None or val == "":
            if pd.required:
                raise RubicError(f"缺少必填参数:{pd.label or pd.name}")
            bound[pd.name] = None
            continue

        bound[pd.name] = _coerce(pd, val)
    return bound


def _coerce(pd: ParamDef, val: Any) -> Any:
    try:
        if pd.type == "number":
            return float(val) if "." in str(val) else int(val)
        if pd.type == "date":
            return str(date.fromisoformat(str(val)))
        if pd.type in ("enum", "multi_enum"):
            allowed = set(pd.options or [])
            items = val if isinstance(val, list) else [val]
            for it in items:
                if allowed and str(it) not in allowed:
                    raise RubicError(f"参数 {pd.name} 取值非法:{it}")
            return items if pd.type == "multi_enum" else items[0]
        if pd.type == "daterange":
            if not (isinstance(val, list) and len(val) == 2):
                raise RubicError(f"参数 {pd.name} 需为 [开始, 结束] 两个日期")
            return [str(date.fromisoformat(str(val[0]))), str(date.fromisoformat(str(val[1])))]
        return str(val)
    except RubicError:
        raise
    except Exception as e:
        raise RubicError(f"参数 {pd.name} 格式错误:{e}") from e

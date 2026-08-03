from __future__ import annotations
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, model_validator


class ParamDef(BaseModel):
    """模板参数定义。前端据此渲染填参表单,后端据此校验/绑定。

    一切参数都是 SQL 字符串替换,只分两种形态,且一律必填:
      single —— 单值,业务填一个文本值
      list   —— 值列表,业务多选/粘贴,执行前展开成 IN (...)
    kind 不由作者手选,而由 SQL 写法判定:`字段 IN (:x)` / `NOT IN (:x)` → list,其余 → single。
    """

    name: str
    kind: Literal["single", "list"] = "single"
    label: str | None = None
    description: str | None = None  # 变量说明,业务填参时作为提示展示
    # ---- 值列表(list)专用 ----
    enum_sql: str | None = None  # 取候选值的独立 SELECT(业务点「获取枚举值」时跑,单列)
    list_mode: Literal["in", "not_in"] | None = None  # 方向提示(由 SQL 写法判定),供填参 UI 展示

    @model_validator(mode="before")
    @classmethod
    def _from_legacy(cls, data):
        """兜底:漏迁移的旧 shape(有 type 无 kind)归一,防序列化打崩。"""
        if isinstance(data, dict) and "kind" not in data and "type" in data:
            data = {**data, "kind": "list" if data.get("type") == "multi_enum" else "single"}
        return data


class UserOut(BaseModel):
    id: int
    name: str
    email: str | None = None
    avatar: str | None = None
    role: str
    department_id: int | None = None
    last_login_at: datetime | None = None

    class Config:
        from_attributes = True

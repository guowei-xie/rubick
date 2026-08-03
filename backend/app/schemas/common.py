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
    label: str | None = None  # 变量说明:给业务看的名字兼填参提示(合并了原 中文名+说明)
    test_value: str | list[str] | None = None  # 测试值:作者试跑用,兼作业务填参示例
    # ---- 值列表(list)专用 ----
    enum_sql: str | None = None  # 取候选值的独立 SELECT(业务点「获取枚举值」时跑,单列)
    allow_bulk_input: bool = False  # 是否允许业务「上传/粘贴」批量输入(编辑者勾选)

    @model_validator(mode="before")
    @classmethod
    def _from_legacy(cls, data):
        """兜底:旧 shape 归一,防序列化打崩(未知字段如 list_mode 由 pydantic 默认忽略)。
        - 有 type 无 kind → 归一 kind;
        - label 为空但有旧 description → 用 description 回填(中文名/说明已合并为 label)。
        """
        if isinstance(data, dict):
            if "kind" not in data and "type" in data:
                data = {**data, "kind": "list" if data.get("type") == "multi_enum" else "single"}
            if not data.get("label") and data.get("description"):
                data = {**data, "label": data["description"]}
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

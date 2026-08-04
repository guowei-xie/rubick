from __future__ import annotations
from datetime import datetime
from typing import ClassVar, Literal

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
    # 值形态:text=文本(默认,SQL 里加单引号);number=数值(绑定为 int/float,不加引号,
    # 用于 age > :x、LIMIT :n、数值主键比较等)。与 kind 正交:单值/值列表都可为数值。
    value_type: Literal["text", "number"] = "text"
    label: str | None = None  # 变量说明:给业务看的名字兼填参提示(合并了原 中文名+说明)
    test_value: str | list[str] | None = None  # 测试值:作者试跑用,兼作业务填参示例
    # ---- 值列表(list)专用 ----
    enum_sql: str | None = None  # 取候选值的独立 SELECT(业务点「获取枚举值」时跑,单列)
    allow_bulk_input: bool = False  # 是否允许业务「上传/粘贴」批量输入(编辑者勾选)
    # 作者在编辑器测试 enum_sql 时捕获的获取耗时(毫秒),持久化后在业务填参侧作参考展示
    enum_sql_duration_ms: int | None = None

    # 仅对 list 有意义的字段;single 落库时统一清回默认值(见 template_service._normalize_params)
    LIST_ONLY_FIELDS: ClassVar[tuple[str, ...]] = ("enum_sql", "allow_bulk_input", "enum_sql_duration_ms")

    @property
    def has_enum_candidates(self) -> bool:
        """该变量是否「有共享候选值可言」—— 判定口径的单一事实来源。

        必须同时看 kind 和 enum_sql:作者把 IN (:x) 改成 = :x 时 _normalize_params 会清掉
        enum_sql,但变量名还在,只看名字会留下一份永远读不到的候选。
        """
        return self.kind == "list" and bool(self.enum_sql)

    @staticmethod
    def dict_has_enum_candidates(p: dict) -> bool:
        """同 has_enum_candidates,但直接判已落库的 params dict(避免为了判一下而重建模型)。"""
        return p.get("kind") == "list" and bool(p.get("enum_sql"))

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
    last_login_at: datetime | None = None

    class Config:
        from_attributes = True

from __future__ import annotations
from typing import Any, Literal

from pydantic import BaseModel

ParamType = Literal["string", "number", "date", "daterange", "enum", "multi_enum"]


class ParamDef(BaseModel):
    """模板参数定义。前端据此渲染填参表单,后端据此校验。"""

    name: str
    type: ParamType = "string"
    label: str | None = None
    required: bool = True
    default: Any = None
    options: list[str] | None = None  # enum / multi_enum 的可选值


class UserOut(BaseModel):
    id: int
    name: str
    email: str | None = None
    avatar: str | None = None
    role: str
    department_id: int | None = None

    class Config:
        from_attributes = True

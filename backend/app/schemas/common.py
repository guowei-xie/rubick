from __future__ import annotations
from datetime import datetime
from typing import Any

from pydantic import BaseModel

# 取值方式:text/number/date=单值;enum/multi_enum=枚举(单选/多选);date_range/number_range=范围
# (兼容旧值 string→text、daterange→date_range)
ParamType = str


class ParamDef(BaseModel):
    """模板参数定义。前端据此渲染填参表单,后端据此校验/绑定。"""

    name: str
    type: str = "text"
    label: str | None = None
    description: str | None = None  # 变量说明,业务填参时作为提示展示
    required: bool = True  # False=选填:业务留空则该字段不参与筛选(谓词中和为 1=1)
    default: Any = None
    options: list[str] | None = None  # enum / multi_enum 的预置候选值
    # ---- 枚举/列表筛选(multi_enum)专用 ----
    enum_sql: str | None = None  # 取候选值的独立 SELECT(业务点「获取枚举值」时跑,单列)
    column: str | None = None  # 表中字段名,展示/备注用(谓词里的列以主 SQL 为准)
    all_when_empty: bool = False  # 兼容旧数据;新逻辑用 required 表达可选


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

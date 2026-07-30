from __future__ import annotations
from typing import Any

from pydantic import BaseModel

from app.schemas.common import ParamDef


class TemplateCreateIn(BaseModel):
    name: str
    domain: str | None = None
    description: str | None = None
    tags: list[str] = []
    datasource_id: int
    dialect: str | None = None  # 忽略,方言由数据源引擎决定(保留以兼容旧前端)
    sql_text: str
    params: list[ParamDef] = []


class TemplateUpdateIn(BaseModel):
    """更新会生成新版本(草稿)。"""

    name: str | None = None
    domain: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    datasource_id: int | None = None
    dialect: str | None = None
    sql_text: str | None = None
    params: list[ParamDef] | None = None


class TestRunIn(BaseModel):
    """商分自检试跑:直接给 SQL + 参数,不落库结果,返回样例行。"""

    datasource_id: int
    dialect: str | None = None  # 忽略,方言由数据源引擎决定
    sql_text: str
    params: list[ParamDef] = []
    values: dict[str, Any] = {}
    limit: int = 100
    # 关联到某个已存在任务时,试跑会落一条 source=test 的运行记录;新建未保存任务时为空,不留痕
    template_id: int | None = None


class EnumValuesIn(BaseModel):
    """自动发现某变量对应字段的候选枚举值:后台跑 SELECT DISTINCT 取全部取值。"""

    datasource_id: int
    sql_text: str
    variable: str  # 参数名(SQL 中 :variable)


class EnumValuesOut(BaseModel):
    column: str  # 识别到的字段表达式,如 city / o.status
    values: list[str]
    truncated: bool = False  # 取值数超过上限被截断(可能不适合做枚举)


class EnumSqlIn(BaseModel):
    """分析师在编辑器里测试「枚举值获取 SQL」。"""

    datasource_id: int
    sql: str


class ValueListOut(BaseModel):
    values: list[str]
    truncated: bool = False


class AcceptIn(BaseModel):
    note: str | None = None


class TemplateVersionOut(BaseModel):
    id: int
    version_no: int
    sql_text: str
    params: list[ParamDef]
    author_id: int
    accepted_by: int | None = None

    class Config:
        from_attributes = True


class TemplateOut(BaseModel):
    id: int
    name: str
    domain: str | None
    description: str | None
    tags: list[str]
    datasource_id: int
    dialect: str
    status: str
    author_id: int
    published_version_id: int | None

    class Config:
        from_attributes = True


class TemplateDetailOut(TemplateOut):
    published_version: TemplateVersionOut | None = None
    latest_version: TemplateVersionOut | None = None

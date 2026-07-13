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
    dialect: str  # hive / mysql
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
    dialect: str
    sql_text: str
    params: list[ParamDef] = []
    values: dict[str, Any] = {}
    limit: int = 100


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

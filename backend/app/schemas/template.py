from __future__ import annotations
from typing import Any

from pydantic import BaseModel

from app.schemas.common import ParamDef


class TemplateCreateIn(BaseModel):
    name: str
    description: str | None = None
    tags: list[str] = []
    datasource_id: int
    sql_text: str
    params: list[ParamDef] = []
    timeout_seconds: int | None = None  # 查询超时(秒);留空=按引擎默认


class TemplateUpdateIn(BaseModel):
    """更新会生成一个新版本。原任务已上线时新版本自动接替上线(见
    template_service.add_version);草稿/已下线则维持原状态。"""

    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    datasource_id: int | None = None
    sql_text: str | None = None
    params: list[ParamDef] | None = None
    timeout_seconds: int | None = None  # 查询超时(秒);留空=按引擎默认


class TestRunIn(BaseModel):
    """作者自检试跑:直接给 SQL + 参数,返回样例行。

    带 template_id(试跑已保存的任务)时会落一条 source=test 的运行记录并存结果文件,
    以便在「运行记录」里预览/导出;不带则只返回样例行、不留痕。两种情况都不发通知。
    """

    datasource_id: int
    sql_text: str
    params: list[ParamDef] = []
    values: dict[str, Any] = {}
    limit: int = 100
    # 关联到某个已存在任务时,试跑会落一条 source=test 的运行记录;新建未保存任务时为空,不留痕
    template_id: int | None = None


class PreviewSqlIn(BaseModel):
    """SQL 预览:只渲染不执行,代入当前填的测试值,未填变量原样保留 :x。"""

    sql_text: str
    params: list[ParamDef] = []
    values: dict[str, Any] = {}


class PreviewSqlOut(BaseModel):
    rendered_sql: str


class EnumSqlIn(BaseModel):
    """作者在任务编辑器里测试「枚举值获取 SQL」。"""

    datasource_id: int
    sql: str


class ValueListOut(BaseModel):
    values: list[str]
    truncated: bool = False
    duration_ms: int | None = None  # 获取枚举耗时(毫秒),作者测试时捕获,供前端参考


class PublishIn(BaseModel):
    note: str | None = None


class TemplateVersionOut(BaseModel):
    id: int
    version_no: int
    sql_text: str
    params: list[ParamDef]
    author_id: int

    class Config:
        from_attributes = True


class TemplateOut(BaseModel):
    id: int
    name: str
    description: str | None
    tags: list[str]
    datasource_id: int
    status: str
    author_id: int
    published_version_id: int | None
    timeout_seconds: int | None = None

    class Config:
        from_attributes = True


class TemplateDetailOut(TemplateOut):
    published_version: TemplateVersionOut | None = None
    latest_version: TemplateVersionOut | None = None

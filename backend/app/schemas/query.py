from __future__ import annotations
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class RunIn(BaseModel):
    template_id: int
    values: dict[str, Any] = {}


class JobOut(BaseModel):
    id: int
    template_id: int
    template_name: str | None = None
    user_name: str | None = None
    params: dict[str, Any] = {}
    status: str
    source: str = "run"  # run=正式取数,test=试跑
    row_count: int | None = None
    duration_ms: int | None = None
    error: str | None = None
    result_filename: str | None = None
    result_expired: bool = False
    executed_sql: str | None = None  # 实际发给数据库的最终 SQL
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class TaskOut(BaseModel):
    """统一任务列表的一行(项目/模板 + 展示与能力标记)。"""

    id: int
    name: str
    domain: str | None = None
    description: str | None = None
    status: str
    dialect: str
    datasource_id: int
    datasource_name: str | None = None
    engine: str | None = None
    author_id: int
    author_name: str | None = None
    published_version_id: int | None = None
    created_at: datetime | None = None
    timeout_seconds: int | None = None  # 该任务查询超时(秒);None=按引擎默认
    can_manage: bool = False  # 可编辑/授权/下线(管理员或作者)
    can_run: bool = False      # 可填参取数(已发布且有运行权限)

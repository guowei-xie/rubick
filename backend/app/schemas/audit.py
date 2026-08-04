from __future__ import annotations
from datetime import datetime

from pydantic import BaseModel


class AuditLogOut(BaseModel):
    id: int
    user_id: int | None
    user_name: str | None
    action: str
    resource_type: str | None
    resource_id: str | None
    resource_name: str | None
    detail: dict
    ip: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class AuditLogPage(BaseModel):
    """分页信封:total 供前端 Table 做服务端分页。"""

    total: int
    items: list[AuditLogOut]


class AuditActionOut(BaseModel):
    code: str
    label: str
    group: str


class AuditResourceTypeOut(BaseModel):
    code: str
    label: str


class AuditMetaOut(BaseModel):
    """动作码/资源类型的中文标签,供管理端筛选下拉与列渲染(单一真源在 models.audit)。"""

    actions: list[AuditActionOut]
    resource_types: list[AuditResourceTypeOut]

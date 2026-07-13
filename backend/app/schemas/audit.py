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
    detail: dict
    ip: str | None
    created_at: datetime

    class Config:
        from_attributes = True

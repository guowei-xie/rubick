from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class NotificationOut(BaseModel):
    id: int
    title: str
    body: str | None = None
    job_id: int | None = None
    template_id: int | None = None
    level: str
    is_read: bool
    feishu_sent: bool
    created_at: datetime

    class Config:
        from_attributes = True

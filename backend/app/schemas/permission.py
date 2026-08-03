from __future__ import annotations
from typing import Literal
from pydantic import BaseModel


class GrantIn(BaseModel):
    subject_type: Literal["user"] = "user"  # 仅支持对个人授权
    subject_id: str
    resource_type: str = "template"
    resource_id: str
    actions: list[str] = ["view", "run", "download"]


class PermissionOut(BaseModel):
    id: int
    subject_type: str
    subject_id: str
    subject_name: str | None = None
    resource_type: str
    resource_id: str
    action: str

    class Config:
        from_attributes = True

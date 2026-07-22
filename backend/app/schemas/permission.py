from __future__ import annotations
from pydantic import BaseModel


class GrantIn(BaseModel):
    subject_type: str  # user / department
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

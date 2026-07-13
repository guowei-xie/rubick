from __future__ import annotations
from typing import Any

from pydantic import BaseModel


class RunIn(BaseModel):
    template_id: int
    values: dict[str, Any] = {}


class PreviewOut(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool = False
    row_count: int


class JobOut(BaseModel):
    id: int
    template_id: int
    template_name: str | None = None
    status: str
    row_count: int | None = None
    duration_ms: int | None = None
    error: str | None = None
    result_filename: str | None = None
    result_expired: bool = False

    class Config:
        from_attributes = True

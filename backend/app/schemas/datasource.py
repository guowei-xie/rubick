from __future__ import annotations
from pydantic import BaseModel

from app.schemas.common import ParamType  # noqa: F401  (占位,保持导入风格一致)


class DataSourceIn(BaseModel):
    name: str
    engine: str  # mysql / hive
    host: str
    port: int
    database: str | None = None
    username: str
    password: str | None = None
    extra: dict = {}


class DataSourceOut(BaseModel):
    id: int
    name: str
    engine: str
    host: str
    port: int
    database: str | None = None
    username: str
    is_active: bool

    class Config:
        from_attributes = True

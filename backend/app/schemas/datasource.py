from __future__ import annotations
from pydantic import BaseModel


class DataSourceIn(BaseModel):
    name: str
    engine: str  # mysql / hive
    host: str
    port: int
    database: str | None = None
    username: str
    password: str | None = None
    extra: dict = {}


class DataSourceUpdateIn(BaseModel):
    """编辑数据源:所有字段可选,只更新传来的字段。password 留空表示不修改。"""

    name: str | None = None
    engine: str | None = None
    host: str | None = None
    port: int | None = None
    database: str | None = None
    username: str | None = None
    password: str | None = None
    extra: dict | None = None


class DataSourceOut(BaseModel):
    id: int
    name: str
    engine: str
    host: str
    port: int
    database: str | None = None
    username: str
    extra: dict = {}  # 引擎参数(如 hive auth);不含密码
    is_active: bool

    class Config:
        from_attributes = True

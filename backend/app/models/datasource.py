"""取数目标数据源(Hive / MySQL 等,与平台元数据库不同)。"""
from __future__ import annotations
from typing import Optional
from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin

ENGINE_MYSQL = "mysql"
ENGINE_HIVE = "hive"


class DataSource(Base, TimestampMixin):
    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    engine: Mapped[str] = mapped_column(String(32))  # mysql / hive
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column()
    database: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    username: Mapped[str] = mapped_column(String(128))
    # 演示用明文;生产应改为密钥管理引用(见 PRD 非功能需求)
    password: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 引擎特有参数(如 hive auth 方式、连接超时等)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(default=True)

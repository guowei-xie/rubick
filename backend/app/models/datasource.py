"""取数目标数据源(Hive / MySQL 等,与平台元数据库不同)。"""
from __future__ import annotations
from typing import Optional
from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, tbl
from app.core.db_types import EncryptedText
from app.models.mixins import TimestampMixin

ENGINE_MYSQL = "mysql"
ENGINE_HIVE = "hive"


class DataSource(Base, TimestampMixin):
    __tablename__ = tbl("data_sources")

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    engine: Mapped[str] = mapped_column(String(32))  # mysql / hive
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column()
    database: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    username: Mapped[str] = mapped_column(String(128))
    # 落库前透明加密(EncryptedText),读取时自动解密;历史明文可平滑读出并在下次写入时升级为密文
    password: Mapped[Optional[str]] = mapped_column(EncryptedText, nullable=True)
    # 引擎特有参数(如 hive auth 方式、连接超时等)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(default=True)

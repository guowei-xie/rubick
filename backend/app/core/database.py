"""平台元数据库的 SQLAlchemy 引擎与会话。"""
from __future__ import annotations
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

# 平台元数据表统一前缀:业务库为线上共享 MySQL,加前缀避免与其它系统表名冲突。
# 所有 __tablename__ 与 ForeignKey 目标均基于此常量拼接。
TABLE_PREFIX = "rubick_"


def tbl(name: str) -> str:
    """拼出带项目前缀的表名,如 tbl("users") -> "rubick_users"。"""
    return f"{TABLE_PREFIX}{name}"


engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

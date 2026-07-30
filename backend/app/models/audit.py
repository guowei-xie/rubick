"""审计日志(append-only)与下载事件。核心合规能力。"""
from __future__ import annotations
from typing import Optional
from sqlalchemy import BigInteger, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, tbl
from app.models.mixins import TimestampMixin


class AuditLog(Base, TimestampMixin):
    """一次可审计动作:谁、何时、干了什么、命中哪个资源。仅追加,不更新不删除。"""

    __tablename__ = tbl("audit_logs")

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True, nullable=True)
    user_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)  # login/run_query/download/...
    resource_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    resource_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # 详情:模板版本、参数、行数、数据源等
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class DownloadEvent(Base, TimestampMixin):
    """结果下载事件,单列以便高频检索与配额统计。"""

    __tablename__ = tbl("download_events")

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    job_id: Mapped[int] = mapped_column(BigInteger, index=True)
    filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    row_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

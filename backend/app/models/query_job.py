"""取数任务。Phase 1 同步执行,状态仍完整记录以便前端展示与审计。"""
from __future__ import annotations
from typing import Optional
from sqlalchemy import BigInteger, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_SUCCESS = "success"
JOB_FAILED = "failed"


class QueryJob(Base, TimestampMixin):
    __tablename__ = "query_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("sql_templates.id"), index=True)
    template_version_id: Mapped[int] = mapped_column(ForeignKey("template_versions.id"))
    datasource_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"))

    params: Mapped[dict] = mapped_column(JSON, default=dict)  # 用户填入的参数值
    status: Mapped[str] = mapped_column(String(16), default=JOB_QUEUED, index=True)

    row_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 结果文件在对象存储中的 key(下载时换签名 URL)
    result_object_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    result_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # 关联模板,便于前端显示模板名而非 id
    template = relationship("SqlTemplate", lazy="joined")

    @property
    def template_name(self) -> Optional[str]:
        return self.template.name if self.template else None

    @property
    def result_expired(self) -> bool:
        """结果文件是否已过保留期(到期后由 MinIO 生命周期规则删除)。"""
        from datetime import datetime, timedelta

        from app.core.config import settings

        if self.status != JOB_SUCCESS or not self.result_object_key or not self.created_at:
            return False
        return datetime.now() > self.created_at + timedelta(days=settings.RESULT_RETENTION_DAYS)

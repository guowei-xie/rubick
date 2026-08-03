"""取数任务(一次运行的记录)。默认由独立 DB 轮询 worker 异步执行
(RUN_INLINE=true 时在请求内同步执行);状态全程记录以便前端展示与审计。"""
from __future__ import annotations
from typing import Optional
from sqlalchemy import BigInteger, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, tbl
from app.models.mixins import TimestampMixin

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_SUCCESS = "success"
JOB_FAILED = "failed"

# 运行来源:业务正式取数 vs 作者在编辑器里的试跑
SOURCE_RUN = "run"
SOURCE_TEST = "test"


class QueryJob(Base, TimestampMixin):
    __tablename__ = tbl("query_jobs")

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(tbl("users.id")), index=True)
    template_id: Mapped[int] = mapped_column(ForeignKey(tbl("sql_templates.id")), index=True)
    # 试跑可能发生在模板尚无已发布版本时,故可空
    template_version_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(tbl("template_versions.id")), nullable=True
    )
    datasource_id: Mapped[int] = mapped_column(ForeignKey(tbl("data_sources.id")))

    params: Mapped[dict] = mapped_column(JSON, default=dict)  # 用户填入的参数值
    status: Mapped[str] = mapped_column(String(16), default=JOB_QUEUED, index=True)
    # 运行来源:run=业务正式取数,test=作者在编辑器里的试跑(运行记录里据此区分)
    source: Mapped[str] = mapped_column(String(16), default=SOURCE_RUN, nullable=False, index=True)

    row_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 实际发给数据库的最终 SQL(参数已代入,供运行记录查阅)
    executed_sql: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 结果文件在本地结果目录(RESULT_DIR)下的相对路径 key;下载时换带签名 token 的 URL
    result_object_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    result_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # 关联模板/发起人,便于运行记录展示名称
    template = relationship("SqlTemplate", lazy="joined")
    user = relationship("User", lazy="joined")

    @property
    def template_name(self) -> Optional[str]:
        return self.template.name if self.template else None

    @property
    def user_name(self) -> Optional[str]:
        return self.user.name if self.user else None

    @property
    def result_expired(self) -> bool:
        """结果文件是否已过保留期(到期后由 worker 定期清理本地文件,见 result_service.cleanup_expired)。"""
        from datetime import datetime, timedelta

        from app.core.config import settings

        if self.status != JOB_SUCCESS or not self.result_object_key or not self.created_at:
            return False
        return datetime.now() > self.created_at + timedelta(days=settings.RESULT_RETENTION_DAYS)

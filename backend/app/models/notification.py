"""站内通知。

飞书通知的本地可见镜像:每次任务完成都会写一条站内通知,
无论是否配置了真实飞书,都能在前端"通知铃铛"里看到,便于演示与兜底。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import BigInteger, Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    job_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    level: Mapped[str] = mapped_column(String(16), default="info")  # info / success / error
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # 飞书推送是否成功(mock 或失败时为 False)
    feishu_sent: Mapped[bool] = mapped_column(Boolean, default=False)

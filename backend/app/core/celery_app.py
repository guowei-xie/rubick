"""Celery 应用。broker 与结果后端都用 Redis。

ASYNC_QUERY=false 时开启 eager 模式(任务在调用处同步执行,无需 worker),
便于本地测试与无 Redis 环境回退。
"""
from __future__ import annotations

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "rubic",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_always_eager=not settings.ASYNC_QUERY,  # eager: 同步执行,不入队
    task_eager_propagates=True,
    timezone="Asia/Shanghai",
)

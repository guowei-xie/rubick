"""所有 ORM 模型。集中 import,方便 Alembic autogenerate 发现。"""
from __future__ import annotations
from app.models.audit import AuditLog, DownloadEvent
from app.models.datasource import DataSource
from app.models.notification import Notification
from app.models.permission import Permission
from app.models.query_job import QueryJob
from app.models.template import SqlTemplate, TemplateEnumValues, TemplateVersion
from app.models.user import User

__all__ = [
    "User",
    "DataSource",
    "Notification",
    "SqlTemplate",
    "TemplateVersion",
    "Permission",
    "QueryJob",
    "AuditLog",
    "DownloadEvent",
]

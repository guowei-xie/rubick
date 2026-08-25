"""所有 ORM 模型。集中 import,让 Base.metadata.create_all 能发现全部表。"""
from __future__ import annotations
from app.models.audit import AuditLog, DownloadEvent
from app.models.credential import TeamDataSourceCredential
from app.models.datasource import DataSource
from app.models.notification import Notification
from app.models.permission import Permission
from app.models.query_job import QueryJob
from app.models.subscription import TaskSchedule, TaskSubscription, TaskSubscriptionEvent
from app.models.team import Team, TeamMember
from app.models.template import SqlTemplate, TemplateEnumValues, TemplateVersion
from app.models.user import User

__all__ = [
    "User",
    "Team",
    "TeamMember",
    "DataSource",
    "TeamDataSourceCredential",
    "Notification",
    "SqlTemplate",
    "TemplateVersion",
    "Permission",
    "QueryJob",
    "AuditLog",
    "DownloadEvent",
    "TaskSchedule",
    "TaskSubscription",
    "TaskSubscriptionEvent",
]

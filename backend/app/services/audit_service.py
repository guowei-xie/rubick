"""审计写入。所有可审计动作统一走这里,保证 append-only。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.audit import AuditLog, DownloadEvent
from app.models.user import User


def log(
    db: Session,
    *,
    user: User | None,
    action: str,
    resource_type: str | None = None,
    resource_id: str | int | None = None,
    detail: dict | None = None,
    ip: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        user_id=user.id if user else None,
        user_name=user.name if user else None,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        detail=detail or {},
        ip=ip,
    )
    db.add(entry)
    db.commit()
    return entry


def log_download(
    db: Session, *, user: User, job_id: int, filename: str | None, row_count: int | None, ip: str | None
) -> None:
    db.add(
        DownloadEvent(
            user_id=user.id, job_id=job_id, filename=filename, row_count=row_count, ip=ip
        )
    )
    log(
        db,
        user=user,
        action="download",
        resource_type="job",
        resource_id=job_id,
        detail={"filename": filename, "row_count": row_count},
        ip=ip,
    )

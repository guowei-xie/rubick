from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.notification import Notification
from app.models.user import User
from app.schemas.notification import NotificationOut

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=list[NotificationOut])
def list_notifications(
    unread_only: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    stmt = stmt.order_by(Notification.id.desc()).limit(50)
    return list(db.scalars(stmt))


@router.get("/unread-count")
def unread_count(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    count = db.query(Notification).filter(
        Notification.user_id == user.id, Notification.is_read.is_(False)
    ).count()
    return {"count": count}


@router.post("/read-all")
def read_all(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.is_read.is_(False))
        .values(is_read=True)
    )
    db.commit()
    return {"ok": True}


@router.post("/{note_id}/read")
def read_one(note_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    note = db.get(Notification, note_id)
    if note and note.user_id == user.id:
        note.is_read = True
        db.commit()
    return {"ok": True}

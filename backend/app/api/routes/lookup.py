"""授权时用的用户 / 部门查找。商分和管理员都可用(用于挑授权对象)。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_analyst
from app.core.database import get_db
from app.models.user import Department, User

router = APIRouter(prefix="/lookup", tags=["lookup"])


@router.get("/users")
def lookup_users(q: str | None = None, db: Session = Depends(get_db), _: User = Depends(require_analyst)):
    stmt = select(User).order_by(User.name).limit(50)
    if q:
        stmt = stmt.where(User.name.like(f"%{q}%"))
    return [{"id": u.id, "name": u.name, "email": u.email, "department_id": u.department_id}
            for u in db.scalars(stmt)]


@router.get("/departments")
def lookup_departments(q: str | None = None, db: Session = Depends(get_db), _: User = Depends(require_analyst)):
    stmt = select(Department).order_by(Department.name).limit(100)
    if q:
        stmt = stmt.where(Department.name.like(f"%{q}%"))
    return [{"id": d.id, "name": d.name} for d in db.scalars(stmt)]

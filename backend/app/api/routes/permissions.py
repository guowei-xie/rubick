from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.database import get_db
from app.core.exceptions import PermissionDeniedError
from app.models.permission import Permission
from app.models.user import ROLE_ADMIN, Department, User
from app.schemas.permission import GrantIn, PermissionOut
from app.services import permission_service

router = APIRouter(prefix="/permissions", tags=["permissions"])


def _enrich(db: Session, perms: list[Permission]) -> list[PermissionOut]:
    """给每条授权补上主体名称(用户名/部门名),便于前端展示"谁有什么权限"。"""
    def _ids(t):
        return {int(p.subject_id) for p in perms if p.subject_type == t and p.subject_id.isdigit()}

    unames = dict(db.execute(select(User.id, User.name).where(User.id.in_(_ids("user") or {-1}))).all())
    dnames = dict(
        db.execute(select(Department.id, Department.name).where(Department.id.in_(_ids("department") or {-1}))).all()
    )
    out = []
    for p in perms:
        name = None
        if p.subject_id.isdigit():
            name = unames.get(int(p.subject_id)) if p.subject_type == "user" else dnames.get(int(p.subject_id))
        out.append(
            PermissionOut(
                id=p.id, subject_type=p.subject_type, subject_id=p.subject_id, subject_name=name,
                resource_type=p.resource_type, resource_id=p.resource_id, action=p.action,
            )
        )
    return out


@router.get("", response_model=list[PermissionOut])
def list_permissions(
    resource_type: str | None = None,
    resource_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    stmt = select(Permission).order_by(Permission.id.desc())
    if resource_type:
        stmt = stmt.where(Permission.resource_type == resource_type)
    if resource_id:
        stmt = stmt.where(Permission.resource_id == resource_id)
    # 商分只能看自己模板的授权;管理员看全部
    if user.role != ROLE_ADMIN:
        owned = [str(i) for i in permission_service.owned_template_ids(db, user)]
        if not owned:
            return []
        stmt = stmt.where(
            Permission.resource_type == "template", Permission.resource_id.in_(owned)
        )
    return _enrich(db, list(db.scalars(stmt)))


@router.post("", response_model=list[PermissionOut])
def grant(data: GrantIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    # 商分只能对自己发布/维护的模板授权
    if data.resource_type != "template":
        raise PermissionDeniedError("仅支持对模板授权")
    if not permission_service.owns_template(db, user, data.resource_id):
        raise PermissionDeniedError("只能对自己的模板授权")
    return permission_service.grant(
        db,
        subject_type=data.subject_type,
        subject_id=data.subject_id,
        resource_type=data.resource_type,
        resource_id=data.resource_id,
        actions=data.actions,
        granted_by=user.id,
    )


@router.delete("/{perm_id}")
def revoke(perm_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    p = db.get(Permission, perm_id)
    if p:
        if p.resource_type == "template" and not permission_service.owns_template(db, user, p.resource_id):
            raise PermissionDeniedError("只能撤销自己模板的授权")
        db.delete(p)
        db.commit()
    return {"ok": True}

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, require_manager
from app.core.database import get_db
from app.core.exceptions import PermissionDeniedError
from app.models.audit import ACTION_PERMISSION_GRANT, ACTION_PERMISSION_REVOKE
from app.models.permission import Permission
from app.models.template import SqlTemplate
from app.models.user import User
from app.schemas.permission import GrantIn, PermissionOut
from app.services import audit_service, permission_service

router = APIRouter(prefix="/permissions", tags=["permissions"])


def _name_of(db: Session, model, key: str | int | None) -> str | None:
    """按 id 取名字,写进审计 detail —— 改名或该行被删后日志仍然可读。
    subject_id 是自由字符串列(可能是 open_id 或将来的组 id),非数字直接返回 None。"""
    if not str(key or "").isdigit():
        return None
    obj = db.get(model, int(key))
    return obj.name if obj else None


def _enrich(db: Session, perms: list[Permission]) -> list[PermissionOut]:
    """给每条授权补上主体名称(用户名),便于前端展示"谁有什么权限"。"""
    uids = {int(p.subject_id) for p in perms if p.subject_id.isdigit()} or {-1}
    unames = dict(db.execute(select(User.id, User.name).where(User.id.in_(uids))).all())
    out = []
    for p in perms:
        name = unames.get(int(p.subject_id)) if p.subject_id.isdigit() else None
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
    user: User = Depends(require_manager),
):
    stmt = select(Permission).order_by(Permission.id.desc())
    if resource_type:
        stmt = stmt.where(Permission.resource_type == resource_type)
    if resource_id:
        stmt = stmt.where(Permission.resource_id == resource_id)
    # 普通用户只能看自己作为作者的任务授权;管理者(管理员/开发者)看全部
    if not permission_service.is_manager(user):
        owned = [str(i) for i in permission_service.owned_template_ids(db, user)]
        if not owned:
            return []
        stmt = stmt.where(
            Permission.resource_type == "template", Permission.resource_id.in_(owned)
        )
    return _enrich(db, list(db.scalars(stmt)))


@router.post("", response_model=list[PermissionOut])
def grant(
    data: GrantIn, db: Session = Depends(get_db),
    user: User = Depends(require_manager), ip: str | None = Depends(client_ip),
):
    # 作者只能对自己维护的任务授权;管理者(管理员/开发者)任意
    # (主体仅 user 由 GrantIn.subject_type=Literal 在入参层保证)
    if data.resource_type != "template":
        raise PermissionDeniedError("仅支持对任务授权")
    if not permission_service.owns_template(db, user, data.resource_id):
        raise PermissionDeniedError("只能对自己的任务授权")
    # 主体解析(open_id → 授权时落库 vs 已知 subject_id)交给服务层,路由只做转发。
    created = permission_service.grant(
        db,
        subject_type=data.subject_type,
        subject_id=data.subject_id,
        subject_open_id=data.subject_open_id,
        subject_profile={"name": data.subject_name, "email": data.subject_email, "avatar": data.subject_avatar},
        resource_type=data.resource_type,
        resource_id=data.resource_id,
        actions=data.actions,
        granted_by=user.id,
    )
    # 资源记成「被授权的那个任务」:审计最常被问的是「谁对任务 X 做过什么」,
    # 授权应与该任务的编辑/运行记录一起被筛出来;被授权人放 detail。
    # subject_id 可能由服务层按 open_id 解析而来,故优先取落库后的真实值。
    subject_id = data.subject_id or (created[0].subject_id if created else None)
    audit_service.log(
        db, user=user, action=ACTION_PERMISSION_GRANT,
        resource_type=data.resource_type, resource_id=data.resource_id,
        resource_name=_name_of(db, SqlTemplate, data.resource_id),
        detail={
            "subject_type": data.subject_type,
            "subject_id": subject_id,
            "subject_open_id": data.subject_open_id,
            # 请求里带了姓名就直接用,省一次查库
            "subject_name": data.subject_name or _name_of(db, User, subject_id),
            "actions": data.actions,
            # grant 幂等:已存在的动作会被跳过。两者不等即说明部分/全部是重复授权
            "actions_created": [p.action for p in created],
            "permission_ids": [p.id for p in created],
        },
        ip=ip,
    )
    return created


@router.delete("/{perm_id}")
def revoke(
    perm_id: int, db: Session = Depends(get_db),
    user: User = Depends(require_manager), ip: str | None = Depends(client_ip),
):
    p = db.get(Permission, perm_id)
    if p:
        if p.resource_type == "template" and not permission_service.owns_template(db, user, p.resource_id):
            raise PermissionDeniedError("只能撤销自己任务的授权")
        # delete+commit 之后属性就读不到了,必须先快照
        before = audit_service.snapshot(
            p, ("subject_type", "subject_id", "resource_type", "resource_id", "action")
        )
        subject_name = _name_of(db, User, p.subject_id)
        resource_name = _name_of(db, SqlTemplate, p.resource_id)
        db.delete(p)
        db.commit()
        audit_service.log(
            db, user=user, action=ACTION_PERMISSION_REVOKE,
            resource_type=before["resource_type"], resource_id=before["resource_id"],
            resource_name=resource_name,
            detail={"permission_id": perm_id, "subject_name": subject_name, **before},
            ip=ip,
        )
    # p 不存在说明什么都没发生,不记日志(否则留下一条误导性的「撤销」)
    return {"ok": True}

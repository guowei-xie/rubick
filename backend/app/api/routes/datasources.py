from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, require_admin, require_manager
from app.connectors import get_connector
from app.core.database import get_db
from app.core.exceptions import NotFoundError, RubicError
from app.models.audit import (
    ACTION_DATASOURCE_CREATE,
    ACTION_DATASOURCE_DELETE,
    ACTION_DATASOURCE_UPDATE,
    RESOURCE_DATASOURCE,
)
from app.models.datasource import DataSource
from app.models.template import SqlTemplate
from app.models.user import User
from app.schemas.datasource import DataSourceIn, DataSourceOut, DataSourceUpdateIn
from app.services import audit_service

router = APIRouter(prefix="/datasources", tags=["datasources"])

# 进审计 detail 的数据源字段白名单 —— 刻意不含 password(EncryptedText,读属性即明文)。
# extra 是逐字记录的,新增引擎参数时务必确认其中不含凭证。
_DS_AUDIT_FIELDS = ("name", "engine", "host", "port", "database", "username", "extra", "is_active")


def _audit(
    db: Session, admin: User, ip: str | None, action: str,
    ds_id: int, name: str | None, detail: dict,
) -> None:
    """数据源类审计的固定部分集中一处,免得每个 handler 各写一遍资源身份。"""
    audit_service.log(
        db, user=admin, action=action, resource_type=RESOURCE_DATASOURCE,
        resource_id=ds_id, resource_name=name, detail=detail, ip=ip,
    )


@router.get("", response_model=list[DataSourceOut])
def list_datasources(db: Session = Depends(get_db), _: User = Depends(require_manager)):
    # 开发者建模板需读数据源下拉;DataSourceOut 不含密码。增删改/测连仍限管理员。
    return list(db.scalars(select(DataSource).order_by(DataSource.id)))


@router.post("", response_model=DataSourceOut)
def create_datasource(
    data: DataSourceIn, db: Session = Depends(get_db),
    admin: User = Depends(require_admin), ip: str | None = Depends(client_ip),
):
    ds = DataSource(**data.model_dump())
    db.add(ds)
    db.commit()
    db.refresh(ds)
    # 按白名单取值,绝不用 data.model_dump()(那里面有 password)
    _audit(
        db, admin, ip, ACTION_DATASOURCE_CREATE, ds.id, ds.name,
        {**audit_service.snapshot(ds, _DS_AUDIT_FIELDS), "has_password": bool(data.password)},
    )
    return ds


@router.put("/{ds_id}", response_model=DataSourceOut)
def update_datasource(
    ds_id: int, data: DataSourceUpdateIn, db: Session = Depends(get_db),
    admin: User = Depends(require_admin), ip: str | None = Depends(client_ip),
):
    """编辑数据源(改账号/密码/地址等)。password 留空表示保留原密码。"""
    ds = db.get(DataSource, ds_id)
    if ds is None:
        raise NotFoundError("数据源不存在")
    before = audit_service.snapshot(ds, _DS_AUDIT_FIELDS)  # 必须在 setattr 之前
    patch = data.model_dump(exclude_unset=True)
    if not patch.get("password"):  # 留空/未填 → 不动原密码
        patch.pop("password", None)
    password_changed = "password" in patch  # pop 之后还在,说明确实提交了新密码
    for key, value in patch.items():
        setattr(ds, key, value)
    db.commit()
    db.refresh(ds)
    _audit(
        db, admin, ip, ACTION_DATASOURCE_UPDATE, ds.id, ds.name,
        {
            "changes": audit_service.diff(before, audit_service.snapshot(ds, _DS_AUDIT_FIELDS)),
            # 只记「密码是否被改」这一事实;长度/哈希前缀都算凭证强度泄露,不记
            "password_changed": password_changed,
            # 区分「传了同值」与「压根没传」,changes 单独看不出来
            "fields_submitted": sorted(patch),
        },
    )
    return ds


@router.delete("/{ds_id}")
def delete_datasource(
    ds_id: int, db: Session = Depends(get_db),
    admin: User = Depends(require_admin), ip: str | None = Depends(client_ip),
):
    """删除数据源。仍被任务引用时拒绝删除,避免这些任务失效。"""
    ds = db.get(DataSource, ds_id)
    if ds is None:
        raise NotFoundError("数据源不存在")
    used = db.scalar(
        select(func.count()).select_from(SqlTemplate).where(SqlTemplate.datasource_id == ds_id)
    )
    if used:
        raise RubicError(f"该数据源被 {used} 个任务使用,请先把这些任务改用其它数据源或删除后再试")
    before = audit_service.snapshot(ds, _DS_AUDIT_FIELDS)  # delete 后属性不可再读
    db.delete(ds)
    db.commit()
    _audit(db, admin, ip, ACTION_DATASOURCE_DELETE, ds_id, before.get("name"), before)
    return {"ok": True}


@router.post("/{ds_id}/test")
def test_datasource(ds_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    ds = db.get(DataSource, ds_id)
    if ds is None:
        raise NotFoundError("数据源不存在")
    try:
        get_connector(ds).test_connection()
    except Exception as e:
        raise RubicError(f"连接失败:{e}") from e
    return {"ok": True}

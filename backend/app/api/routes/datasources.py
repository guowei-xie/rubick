from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_admin, require_manager
from app.connectors import get_connector
from app.core.database import get_db
from app.core.exceptions import NotFoundError, RubicError
from app.models.datasource import DataSource
from app.models.template import SqlTemplate
from app.models.user import User
from app.schemas.datasource import DataSourceIn, DataSourceOut, DataSourceUpdateIn

router = APIRouter(prefix="/datasources", tags=["datasources"])


@router.get("", response_model=list[DataSourceOut])
def list_datasources(db: Session = Depends(get_db), _: User = Depends(require_manager)):
    # 开发者建模板需读数据源下拉;DataSourceOut 不含密码。增删改/测连仍限管理员。
    return list(db.scalars(select(DataSource).order_by(DataSource.id)))


@router.post("", response_model=DataSourceOut)
def create_datasource(data: DataSourceIn, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    ds = DataSource(**data.model_dump())
    db.add(ds)
    db.commit()
    db.refresh(ds)
    return ds


@router.put("/{ds_id}", response_model=DataSourceOut)
def update_datasource(
    ds_id: int, data: DataSourceUpdateIn, db: Session = Depends(get_db), _: User = Depends(require_admin)
):
    """编辑数据源(改账号/密码/地址等)。password 留空表示保留原密码。"""
    ds = db.get(DataSource, ds_id)
    if ds is None:
        raise NotFoundError("数据源不存在")
    patch = data.model_dump(exclude_unset=True)
    if not patch.get("password"):  # 留空/未填 → 不动原密码
        patch.pop("password", None)
    for key, value in patch.items():
        setattr(ds, key, value)
    db.commit()
    db.refresh(ds)
    return ds


@router.delete("/{ds_id}")
def delete_datasource(ds_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """删除数据源。仍被任务引用时拒绝删除,避免这些任务失效。"""
    ds = db.get(DataSource, ds_id)
    if ds is None:
        raise NotFoundError("数据源不存在")
    used = db.scalar(
        select(func.count()).select_from(SqlTemplate).where(SqlTemplate.datasource_id == ds_id)
    )
    if used:
        raise RubicError(f"该数据源被 {used} 个任务使用,请先把这些任务改用其它数据源或删除后再试")
    db.delete(ds)
    db.commit()
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

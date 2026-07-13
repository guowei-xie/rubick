from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin, require_analyst
from app.connectors import get_connector
from app.core.database import get_db
from app.core.exceptions import NotFoundError, RubicError
from app.models.datasource import DataSource
from app.models.user import User
from app.schemas.datasource import DataSourceIn, DataSourceOut

router = APIRouter(prefix="/datasources", tags=["datasources"])


@router.get("", response_model=list[DataSourceOut])
def list_datasources(db: Session = Depends(get_db), _: User = Depends(require_analyst)):
    return list(db.scalars(select(DataSource).order_by(DataSource.id)))


@router.post("", response_model=DataSourceOut)
def create_datasource(data: DataSourceIn, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    ds = DataSource(**data.model_dump())
    db.add(ds)
    db.commit()
    db.refresh(ds)
    return ds


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

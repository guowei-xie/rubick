"""SQL 模板的编写、版本、发布、验收、试跑。"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.connectors import get_connector
from app.core.config import settings
from app.core.exceptions import NotFoundError, RubicError
from app.core.sql_gateway import validate_readonly
from app.models.datasource import DataSource
from app.models.template import (
    STATUS_ARCHIVED,
    STATUS_DRAFT,
    STATUS_PENDING_ACCEPT,
    STATUS_PUBLISHED,
    SqlTemplate,
    TemplateVersion,
)
from app.models.user import User
from app.schemas.common import ParamDef
from app.services import params_service


def _next_version_no(db: Session, template_id: int) -> int:
    current = db.scalar(
        select(func.max(TemplateVersion.version_no)).where(
            TemplateVersion.template_id == template_id
        )
    )
    return (current or 0) + 1


def _param_dicts(params: list[ParamDef] | list[dict]) -> list[dict]:
    out = []
    for p in params:
        out.append(p.model_dump() if isinstance(p, ParamDef) else dict(p))
    return out


def create_template(db: Session, author: User, data) -> SqlTemplate:
    ds = db.get(DataSource, data.datasource_id)
    if ds is None:
        raise NotFoundError("数据源不存在")
    validate_readonly(data.sql_text, data.dialect)

    tmpl = SqlTemplate(
        name=data.name,
        domain=data.domain,
        description=data.description,
        tags=data.tags,
        datasource_id=data.datasource_id,
        dialect=data.dialect,
        status=STATUS_DRAFT,
        author_id=author.id,
    )
    db.add(tmpl)
    db.flush()

    version = TemplateVersion(
        template_id=tmpl.id,
        version_no=1,
        sql_text=data.sql_text,
        params=_param_dicts(data.params),
        author_id=author.id,
    )
    db.add(version)
    db.commit()
    db.refresh(tmpl)
    return tmpl


def add_version(db: Session, author: User, tmpl: SqlTemplate, data) -> TemplateVersion:
    """更新模板 = 生成新草稿版本;模板回到 draft 状态。"""
    latest = latest_version(db, tmpl.id)
    sql_text = data.sql_text if data.sql_text is not None else (latest.sql_text if latest else "")
    dialect = data.dialect or tmpl.dialect
    validate_readonly(sql_text, dialect)

    if data.name is not None:
        tmpl.name = data.name
    if data.domain is not None:
        tmpl.domain = data.domain
    if data.description is not None:
        tmpl.description = data.description
    if data.tags is not None:
        tmpl.tags = data.tags
    if data.datasource_id is not None:
        tmpl.datasource_id = data.datasource_id
    if data.dialect is not None:
        tmpl.dialect = data.dialect
    tmpl.status = STATUS_DRAFT

    params = data.params if data.params is not None else (latest.params if latest else [])
    version = TemplateVersion(
        template_id=tmpl.id,
        version_no=_next_version_no(db, tmpl.id),
        sql_text=sql_text,
        params=_param_dicts(params),
        author_id=author.id,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def latest_version(db: Session, template_id: int) -> TemplateVersion | None:
    return db.scalar(
        select(TemplateVersion)
        .where(TemplateVersion.template_id == template_id)
        .order_by(TemplateVersion.version_no.desc())
        .limit(1)
    )


def submit_for_accept(db: Session, tmpl: SqlTemplate) -> None:
    tmpl.status = STATUS_PENDING_ACCEPT
    db.commit()


def accept_and_publish(db: Session, tmpl: SqlTemplate, accepter: User, note: str | None) -> None:
    """验收通过并发布最新版本。"""
    version = latest_version(db, tmpl.id)
    if version is None:
        raise RubicError("模板没有可发布的版本")
    version.accepted_by = accepter.id
    version.accepted_note = note
    tmpl.published_version_id = version.id
    tmpl.status = STATUS_PUBLISHED
    db.commit()


def archive(db: Session, tmpl: SqlTemplate) -> None:
    tmpl.status = STATUS_ARCHIVED
    tmpl.published_version_id = None
    db.commit()


def test_run(db: Session, data) -> dict:
    """商分自检试跑:不落库,返回样例行。"""
    ds = db.get(DataSource, data.datasource_id)
    if ds is None:
        raise NotFoundError("数据源不存在")
    validate_readonly(data.sql_text, data.dialect)
    bound = params_service.validate_and_bind(data.params, data.values)

    connector = get_connector(ds)
    limit = min(data.limit, settings.MAX_RESULT_ROWS)
    result = connector.execute(
        data.sql_text,
        bound,
        timeout_seconds=settings.QUERY_TIMEOUT_SECONDS,
        max_rows=limit,
    )
    return {
        "columns": result.columns,
        "rows": [list(r) for r in result.rows],
        "truncated": result.truncated,
        "row_count": result.row_count,
    }

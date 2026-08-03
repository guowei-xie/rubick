from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.core.database import get_db
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.models.template import STATUS_PUBLISHED, SqlTemplate, TemplateVersion
from app.models.user import User
from app.schemas.template import (
    EnumSqlIn,
    PreviewSqlIn,
    PreviewSqlOut,
    PublishIn,
    TemplateCreateIn,
    TemplateDetailOut,
    TemplateOut,
    TemplateUpdateIn,
    TemplateVersionOut,
    TestRunIn,
    ValueListOut,
)
from app.services import params_service, permission_service, template_service

router = APIRouter(prefix="/templates", tags=["templates"])


def _load(db: Session, template_id: int) -> SqlTemplate:
    tmpl = db.get(SqlTemplate, template_id)
    if tmpl is None:
        raise NotFoundError("模板不存在")
    return tmpl


def _require_author_or_admin(tmpl: SqlTemplate, user: User) -> None:
    if not permission_service.is_template_owner(user, tmpl):
        raise PermissionDeniedError("只有作者或管理员可操作该模板")


@router.get("", response_model=list[TemplateOut])
def list_templates(
    mine: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """默认:业务用户看到有权限的已发布模板。mine=true:商分看自己维护的全部模板。"""
    stmt = select(SqlTemplate).order_by(SqlTemplate.id.desc())
    if mine:
        stmt = stmt.where(SqlTemplate.author_id == user.id)
        return list(db.scalars(stmt))

    stmt = stmt.where(SqlTemplate.status == STATUS_PUBLISHED)
    visible = permission_service.visible_template_ids(db, user)
    if visible is None:  # admin:全部
        return list(db.scalars(stmt))
    if not visible:
        return []
    return list(db.scalars(stmt.where(SqlTemplate.id.in_(visible))))


@router.post("", response_model=TemplateOut)
def create_template(
    data: TemplateCreateIn, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    return template_service.create_template(db, user, data)


@router.get("/{template_id}", response_model=TemplateDetailOut)
def get_template(template_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    tmpl = _load(db, template_id)
    is_owner = permission_service.is_template_owner(user, tmpl)
    if not is_owner:
        # 业务用户:必须有 view 权限且已发布
        if tmpl.status != STATUS_PUBLISHED or not permission_service.can(
            db, user, "view", "template", template_id
        ):
            raise PermissionDeniedError("无权查看该模板")

    detail = TemplateDetailOut.model_validate(tmpl)
    if tmpl.published_version_id:
        pv = db.get(TemplateVersion, tmpl.published_version_id)
        detail.published_version = TemplateVersionOut.model_validate(pv) if pv else None
    if is_owner:
        latest = template_service.latest_version(db, tmpl.id)
        detail.latest_version = TemplateVersionOut.model_validate(latest) if latest else None
    return detail


@router.put("/{template_id}", response_model=TemplateVersionOut)
def update_template(
    template_id: int,
    data: TemplateUpdateIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    tmpl = _load(db, template_id)
    _require_author_or_admin(tmpl, user)
    return template_service.add_version(db, user, tmpl, data)


@router.post("/{template_id}/publish")
def publish(
    template_id: int,
    data: PublishIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """发布最新版本。"""
    tmpl = _load(db, template_id)
    _require_author_or_admin(tmpl, user)
    template_service.publish(db, tmpl, user, data.note)
    return {"status": tmpl.status, "published_version_id": tmpl.published_version_id}


@router.post("/{template_id}/archive")
def archive(template_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    tmpl = _load(db, template_id)
    _require_author_or_admin(tmpl, user)
    template_service.archive(db, tmpl)
    return {"status": tmpl.status}


@router.post("/test-run")
def test_run(data: TestRunIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """作者自检试跑:返回样例行;关联到已存在任务时同时落一条 source=test 的运行记录。"""
    return template_service.test_run(db, data, user)


@router.post("/preview-sql", response_model=PreviewSqlOut)
def preview_sql(data: PreviewSqlIn, _: User = Depends(require_admin)):
    """SQL 预览:代入当前测试值渲染即将执行的 SQL(不连库执行),未填变量原样保留 :变量。"""
    return {"rendered_sql": params_service.preview_sql(data.sql_text, data.params, data.values)}


@router.post("/enum-sql", response_model=ValueListOut)
def enum_sql(data: EnumSqlIn, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """分析师测试「枚举值获取 SQL」,返回候选值(结果第一列去重)。"""
    return template_service.run_value_query(db, data.datasource_id, data.sql)

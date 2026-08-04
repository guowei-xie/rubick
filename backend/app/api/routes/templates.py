from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, require_manager
from app.core.database import get_db
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.models.audit import (
    ACTION_TASK_ARCHIVE,
    ACTION_TASK_CREATE,
    ACTION_TASK_PUBLISH,
    ACTION_TASK_RESTORE,
    ACTION_TASK_UPDATE,
)
from app.models.permission import RESOURCE_TEMPLATE
from app.models.template import STATUS_ARCHIVED, STATUS_PUBLISHED, SqlTemplate, TemplateVersion
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
from app.services import (
    audit_service,
    enum_cache_service,
    params_service,
    permission_service,
    template_service,
)

router = APIRouter(prefix="/templates", tags=["templates"])

# 进审计 detail 的任务元信息字段(SQL 单独记,不混在 diff 里)
_TMPL_AUDIT_FIELDS = ("name", "description", "tags", "datasource_id", "timeout_seconds", "status")

# 审计里 SQL 原文的截断长度,与 query_service 记录 executed_sql 的口径一致
_SQL_CAP = 20000


def _audit(
    db: Session, user: User, ip: str | None, tmpl: SqlTemplate, action: str, detail: dict
) -> None:
    """任务类审计的固定部分集中一处:资源恒为该任务(id + 名称快照)。"""
    audit_service.log(
        db, user=user, action=action, resource_type=RESOURCE_TEMPLATE,
        resource_id=tmpl.id, resource_name=tmpl.name, detail=detail, ip=ip,
    )


def _load(db: Session, template_id: int) -> SqlTemplate:
    tmpl = db.get(SqlTemplate, template_id)
    if tmpl is None:
        raise NotFoundError("任务不存在")
    return tmpl


def _require_author_or_manager(tmpl: SqlTemplate, user: User) -> None:
    if not permission_service.is_template_owner(user, tmpl):
        raise PermissionDeniedError("只有作者或管理者可操作该任务")


@router.get("", response_model=list[TemplateOut])
def list_templates(
    mine: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """默认:业务用户看到有权限的已上线任务。mine=true:作者看自己维护的全部任务(含草稿/已下线)。

    注:前端任务列表走 /tasks(带能力标记);本端点保留给按作者维度取原始模板行的用法。
    """
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
    data: TemplateCreateIn, db: Session = Depends(get_db),
    user: User = Depends(require_manager), ip: str | None = Depends(client_ip),
):
    # 作者测出来的候选值随版本一并落库(见 template_service._sync_enum_cache)
    tmpl = template_service.create_template(db, user, data)
    _audit(
        db, user, ip, tmpl, ACTION_TASK_CREATE,
        {
            **audit_service.snapshot(tmpl, _TMPL_AUDIT_FIELDS),
            "datasource_name": tmpl.datasource_name,
            "version_no": 1,
            "sql_text": (data.sql_text or "")[:_SQL_CAP],
            "param_names": [p.name for p in (data.params or [])],
        },
    )
    return tmpl


@router.get("/{template_id}", response_model=TemplateDetailOut)
def get_template(template_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    tmpl = _load(db, template_id)
    is_owner = permission_service.is_template_owner(user, tmpl)
    if not is_owner:
        # 业务用户:必须有 view 权限且任务已上线
        if tmpl.status != STATUS_PUBLISHED or not permission_service.can(
            db, user, "view", "template", template_id
        ):
            raise PermissionDeniedError("无权查看该任务")

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
    user: User = Depends(require_manager),
    ip: str | None = Depends(client_ip),
):
    tmpl = _load(db, template_id)
    _require_author_or_manager(tmpl, user)
    # 快照必须早于 add_version:SQLAlchemy 就地改对象,提交后再读拿到的是新值
    before = audit_service.snapshot(tmpl, _TMPL_AUDIT_FIELDS)
    prev = template_service.latest_version(db, tmpl.id)
    before_sql = prev.sql_text if prev else ""

    # 共享枚举候选值的播种与剪枝跟着版本写入走(见 template_service._sync_enum_cache)
    version = template_service.add_version(db, user, tmpl, data)

    _audit(
        db, user, ip, tmpl, ACTION_TASK_UPDATE,
        {
            "version_no": version.version_no,
            "changes": audit_service.diff(before, audit_service.snapshot(tmpl, _TMPL_AUDIT_FIELDS)),
            "sql_changed": version.sql_text != before_sql,
            "sql_text": (version.sql_text or "")[:_SQL_CAP],
            # 编辑已上线任务时,新版本会自动接替上线(见 template_service.add_version)
            "auto_republished": before["status"] == STATUS_PUBLISHED,
        },
    )
    return version


@router.post("/{template_id}/publish")
def publish(
    template_id: int,
    data: PublishIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_manager),
    ip: str | None = Depends(client_ip),
):
    """上线最新版本(UI 里的「上线 / 重新上线」)。"""
    tmpl = _load(db, template_id)
    _require_author_or_manager(tmpl, user)
    before_status = tmpl.status  # 必须在 publish 之前取
    version = template_service.publish(db, tmpl, user, data.note)
    # 从回收站(已下线)重新上线单独记一个动作码:这在业务上是「恢复」,
    # 与首次上线不是同一件事,前端回收站也是独立入口
    _audit(
        db, user, ip, tmpl,
        ACTION_TASK_RESTORE if before_status == STATUS_ARCHIVED else ACTION_TASK_PUBLISH,
        {
            "from_status": before_status,
            "to_status": tmpl.status,
            "published_version_id": tmpl.published_version_id,
            "version_no": version.version_no,
            "note": data.note,
        },
    )
    return {"status": tmpl.status, "published_version_id": tmpl.published_version_id}


@router.post("/{template_id}/archive")
def archive(
    template_id: int, db: Session = Depends(get_db),
    user: User = Depends(require_manager), ip: str | None = Depends(client_ip),
):
    tmpl = _load(db, template_id)
    _require_author_or_manager(tmpl, user)
    before_status = tmpl.status
    before_version_id = tmpl.published_version_id  # archive 会清空,先存
    template_service.archive(db, tmpl)
    # name 不重复进 detail:resource_name 列已经记了,且那一列才是列表页读的
    _audit(
        db, user, ip, tmpl, ACTION_TASK_ARCHIVE,
        {"from_status": before_status, "unpublished_version_id": before_version_id},
    )
    return {"status": tmpl.status}


@router.post("/test-run")
def test_run(data: TestRunIn, db: Session = Depends(get_db), user: User = Depends(require_manager)):
    """作者自检试跑:返回样例行;关联到已存在任务时同时落一条 source=test 的运行记录。"""
    return template_service.test_run(db, data, user)


@router.post("/preview-sql", response_model=PreviewSqlOut)
def preview_sql(data: PreviewSqlIn, _: User = Depends(require_manager)):
    """SQL 预览:代入当前测试值渲染即将执行的 SQL(不连库执行),未填变量原样保留 :变量。"""
    return {"rendered_sql": params_service.preview_sql(data.sql_text, data.params, data.values)}


@router.post("/enum-sql", response_model=ValueListOut)
def enum_sql(data: EnumSqlIn, db: Session = Depends(get_db), _: User = Depends(require_manager)):
    """作者测试「枚举值获取 SQL」,返回候选值(结果第一列去重)。"""
    return enum_cache_service.run_value_query(db, data.datasource_id, data.sql)

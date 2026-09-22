from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, require_task_author
from app.core.database import get_db
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.models.audit import (
    ACTION_TASK_ARCHIVE,
    ACTION_TASK_CREATE,
    ACTION_TASK_PUBLISH,
    ACTION_TASK_RESTORE,
    ACTION_TASK_UNARCHIVE,
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
    SubscriptionScheduleOut,
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
    credential_service,
    enum_cache_service,
    params_service,
    permission_service,
    subscription_service,
    template_service,
)

router = APIRouter(prefix="/templates", tags=["templates"])

# 进审计 detail 的任务元信息字段(SQL 单独记,不混在 diff 里)
_TMPL_AUDIT_FIELDS = (
    "name", "description", "tags", "datasource_id", "team_id", "timeout_seconds", "status",
    "allow_api",
)

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


def _require_can_edit(db: Session, tmpl: SqlTemplate, user: User) -> None:
    """编辑权守卫。四条口径集中在 permission_service.can_edit,此处只复用不重写。"""
    if not permission_service.can_edit(permission_service.team_scope(db, user), tmpl):
        raise PermissionDeniedError(
            "无权编辑该任务:需为任务作者、该团队的团队管理员,或已获得该任务的编辑授权"
        )


@router.get("", response_model=list[TemplateOut])
def list_templates(
    mine: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """默认:按可见性(团队 + 显式授权)收窄。mine=true:**按作者维度**取自己名下的全部
    任务(含草稿/已下线)。

    注:前端任务列表走 /tasks(带能力标记);本端点保留给按作者维度取原始模板行的用法。
    这里的 mine 严格等于「author_id 是我」,与任务列表那个「我开发的」筛选
    (TaskOut.developed_by_me = 作者**或**被授予编辑权)不是一回事,别互相套用。
    """
    stmt = select(SqlTemplate).order_by(SqlTemplate.id.desc())
    if mine:
        stmt = stmt.where(SqlTemplate.author_id == user.id)
        return list(db.scalars(stmt))

    # 与 /tasks 同一条可见性规则(此前这里完全忽略了授权,只按 published 过滤)
    cond = permission_service.visible_condition(permission_service.team_scope(db, user))
    if cond is not None:
        stmt = stmt.where(cond)
    return list(db.scalars(stmt))


@router.post("", response_model=TemplateOut)
def create_template(
    data: TemplateCreateIn, db: Session = Depends(get_db),
    user: User = Depends(require_task_author), ip: str | None = Depends(client_ip),
):
    # 建任务必须选团队:它决定任务的可见范围与取数身份。开发者只能选自己所属的团队。
    permission_service.require_can_create_in_team(db, user, data.team_id)
    # 作者测出来的候选值随版本一并落库(见 template_service._sync_enum_cache)
    tmpl = template_service.create_template(db, user, data)
    _audit(
        db, user, ip, tmpl, ACTION_TASK_CREATE,
        {
            **audit_service.snapshot(tmpl, _TMPL_AUDIT_FIELDS),
            "datasource_name": tmpl.datasource_name,
            "team_name": tmpl.team_name,
            "version_no": 1,
            "sql_text": (data.sql_text or "")[:_SQL_CAP],
            "param_names": [p.name for p in (data.params or [])],
        },
    )
    return tmpl


@router.get("/{template_id}", response_model=TemplateDetailOut)
def get_template(template_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    tmpl = _load(db, template_id)
    scope = permission_service.team_scope(db, user)
    if not permission_service.can_view(scope, tmpl):
        raise PermissionDeniedError("无权查看该任务")

    detail = TemplateDetailOut.model_validate(tmpl)
    if tmpl.published_version_id:
        pv = db.get(TemplateVersion, tmpl.published_version_id)
        detail.published_version = TemplateVersionOut.model_validate(pv) if pv else None
    # 最新版本(含未上线的 SQL 原文)只给**团队内部人**:业务使用者被授权后只该看到已上线的那一面
    if permission_service.is_insider(scope, tmpl):
        latest = template_service.latest_version(db, tmpl.id)
        detail.latest_version = TemplateVersionOut.model_validate(latest) if latest else None
    # 订阅计划回显 + 在册订阅人数(编辑器回填表单,以及「有订阅者」预警)
    sched = subscription_service.get_schedule(db, tmpl.id)
    if sched is not None:
        detail.subscription = SubscriptionScheduleOut.model_validate(sched)
    detail.subscriber_count = subscription_service.subscriber_count(db, tmpl.id)
    return detail


@router.put("/{template_id}", response_model=TemplateVersionOut)
def update_template(
    template_id: int,
    data: TemplateUpdateIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_task_author),
    ip: str | None = Depends(client_ip),
):
    tmpl = _load(db, template_id)
    _require_can_edit(db, tmpl, user)
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
    user: User = Depends(require_task_author),
    ip: str | None = Depends(client_ip),
):
    """上线最新版本(UI 里的「上线 / 重新上线」)。"""
    tmpl = _load(db, template_id)
    _require_can_edit(db, tmpl, user)
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
    user: User = Depends(require_task_author), ip: str | None = Depends(client_ip),
):
    """收进回收站(UI 里已上线任务叫「下线」、草稿叫「移入回收站」)。
    **不限状态**:草稿同样能进 —— 见 template_service.archive。"""
    tmpl = _load(db, template_id)
    _require_can_edit(db, tmpl, user)
    before_status = tmpl.status
    before_version_id = tmpl.published_version_id  # archive 会清空,先存
    template_service.archive(db, tmpl)
    # name 不重复进 detail:resource_name 列已经记了,且那一列才是列表页读的
    _audit(
        db, user, ip, tmpl, ACTION_TASK_ARCHIVE,
        {"from_status": before_status, "unpublished_version_id": before_version_id},
    )
    return {"status": tmpl.status}


@router.post("/{template_id}/unarchive")
def unarchive(
    template_id: int, db: Session = Depends(get_db),
    user: User = Depends(require_task_author), ip: str | None = Depends(client_ip),
):
    """从回收站退回草稿(UI 里的「恢复为草稿」)。

    与 publish 的「重新上线」是回收站的两个出口,动作码也分两个:这条**不让任务对业务
    可运行**,所以也不必过 credential_service 那道取数账号卡点 —— 捡回一个半成品接着改,
    不该被「团队还没登记取数账号」挡住。
    """
    tmpl = _load(db, template_id)
    _require_can_edit(db, tmpl, user)
    template_service.unarchive(db, tmpl)
    _audit(db, user, ip, tmpl, ACTION_TASK_UNARCHIVE, {"from_status": STATUS_ARCHIVED})
    return {"status": tmpl.status}


@router.post("/test-run")
def test_run(data: TestRunIn, db: Session = Depends(get_db), user: User = Depends(require_task_author)):
    """作者自检试跑:返回样例行;关联到已存在任务时同时落一条 source=test 的运行记录。"""
    return template_service.test_run(db, data, user)


@router.post("/preview-sql", response_model=PreviewSqlOut)
def preview_sql(data: PreviewSqlIn, _: User = Depends(require_task_author)):
    """SQL 预览:代入当前测试值渲染即将执行的 SQL(不连库执行),未填变量原样保留 :变量。"""
    return {"rendered_sql": params_service.preview_sql(data.sql_text, data.params, data.values)}


@router.post("/enum-sql", response_model=ValueListOut)
def enum_sql(
    data: EnumSqlIn, db: Session = Depends(get_db), user: User = Depends(require_task_author)
):
    """作者测试「枚举值获取 SQL」,返回候选值(结果第一列去重)。

    用**任务所属团队**的取数账号 —— 与试跑同理:编辑器里的验证必须与上线后的取数身份一致,
    否则「这里测通了、业务却跑不动」。for_team 内部校验操作者是该团队成员。
    """
    credential = credential_service.for_team(
        db, team_id=data.team_id, datasource_id=data.datasource_id, actor=user
    )
    return enum_cache_service.run_value_query(db, data.datasource_id, data.sql, credential)

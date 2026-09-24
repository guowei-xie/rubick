"""取数任务(SQL 模板)的编写、版本、上线/下线、试跑,以及「还有没有人在用」的闲置判定。

术语:产品 UI 里的「任务」= 这里的 SqlTemplate;「上线 / 下线」= publish / archive。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.connectors import get_connector
from app.core.config import settings
from app.core.exceptions import NotFoundError, PermissionDeniedError, RubicError
from app.core.logging_setup import get_logger
from app.core.sql_gateway import validate_readonly
from app.models.datasource import DataSource
from app.models.query_job import (
    JOB_FAILED,
    JOB_RUNNING,
    JOB_SUCCESS,
    SOURCE_TEST,
    QueryJob,
    clip_executed_sql,
)
from app.models.template import (
    STATUS_ARCHIVED,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    SqlTemplate,
    TemplateVersion,
)
from app.models.user import User
from app.schemas.common import ParamDef
from app.services import (
    credential_service,
    enum_cache_service,
    notify_service,
    params_service,
    permission_service,
    query_service,
    result_service,
    subscription_service,
    team_service,
)

log = get_logger("rubick.template")


# 试跑是**有人在等**的同步请求,不能套用 Hive 的 3600 秒批处理上限占着请求线程一小时。
# 但也不该像从前那样固定 120 秒 —— 那让「试跑通过 = 上线后能跑」这句承诺在超时这一维上失效:
# 作者给任务配了 30 分钟,试跑照样 120 秒被砍,而报错只说「超时」,他会以为配置没生效。
# 现在的口径是:与正式取数同一个 effective_timeout,再夹这一道前台上限;真被它夹到时,
# 报错要把两个数都说出来(见 test_run 的失败分支)。
TEST_RUN_TIMEOUT_CEILING_SECONDS = 180


def _next_version_no(db: Session, template_id: int) -> int:
    current = db.scalar(
        select(func.max(TemplateVersion.version_no)).where(
            TemplateVersion.template_id == template_id
        )
    )
    return (current or 0) + 1


def _normalize_params(sql: str, params: list[ParamDef] | list[dict]) -> list[dict]:
    """落库前按 SQL 写法定死 kind,让「kind 由 SQL 判定」的约束在持久化边界生效
    (不依赖前端如实传值)。字段 IN/NOT IN (:x) → list,其余 → single。
    single 变量不落 list 专用字段(见 ParamDef.LIST_ONLY_FIELDS),统一清回其声明默认值。
    """
    out = []
    for p in params:
        d = p.model_dump() if isinstance(p, ParamDef) else dict(p)
        is_list = params_service.detect_is_list(sql, d.get("name", ""))
        d["kind"] = "list" if is_list else "single"
        if not is_list:
            for name in ParamDef.LIST_ONLY_FIELDS:
                d[name] = ParamDef.model_fields[name].get_default(call_default_factory=True)
        out.append(d)
    return out


def _sync_enum_cache(db: Session, tmpl: SqlTemplate, version, data, author: User) -> None:
    """版本落库后把共享枚举候选值对齐到新 params —— 与 _normalize_params 同一个持久化边界。

    放在这里(而非各个路由)是为了让「params 被重写 ⇒ 候选值跟着对齐」由写入本身保证:
    以后新增任何写版本的入口(克隆任务、回滚版本、批量修数脚本)都不会漏掉这一步。
    """
    enum_cache_service.sync_params(
        db,
        template_id=tmpl.id,
        datasource_id=tmpl.datasource_id,
        params=version.params,
        samples=getattr(data, "enum_samples", None),
        user_id=author.id,
    )


def create_template(db: Session, author: User, data) -> SqlTemplate:
    ds = db.get(DataSource, data.datasource_id)
    if ds is None:
        raise NotFoundError("数据源不存在")
    dialect = ds.engine  # 方言由数据源引擎决定,不再手填
    validate_readonly(data.sql_text, dialect)

    tmpl = SqlTemplate(
        name=data.name,
        description=data.description,
        tags=data.tags,
        datasource_id=data.datasource_id,
        # 所属团队由路由层先过 permission_service.require_can_create_in_team 校验
        team_id=data.team_id,
        dialect=dialect,
        status=STATUS_DRAFT,
        author_id=author.id,
        timeout_seconds=data.timeout_seconds,
        allow_api=data.allow_api,
        allow_result_reuse=data.allow_result_reuse,
    )
    db.add(tmpl)
    db.flush()

    version = TemplateVersion(
        template_id=tmpl.id,
        version_no=1,
        sql_text=data.sql_text,
        params=_normalize_params(data.sql_text, data.params),
        author_id=author.id,
    )
    db.add(version)
    # 订阅计划落库与「开订阅的任务不能有变量」卡点(新任务无订阅者,不会有清退)
    subscription_service.apply_template_save(db, tmpl, data.subscription, version.params, author)
    _sync_enum_cache(db, tmpl, version, data, author)
    db.commit()
    db.refresh(tmpl)
    return tmpl


def add_version(db: Session, author: User, tmpl: SqlTemplate, data) -> TemplateVersion:
    """更新模板 = 生成新版本。编辑不改变上线状态:
    - 原本已上线(published)→ 新版本自动接替上线,保持对业务可运行;
    - 原本草稿(draft)/已下线 → 维持原状态,由列表「上线」操作再晋升。
    """
    was_published = tmpl.status == STATUS_PUBLISHED
    if was_published:
        # 已上线任务保存后新版本会自动接替上线,那也是一次上线 ⇒ 先过卡点再落库。
        # 提前问一次是为了「要么整件事成立、要么一行都不写」:否则作者的编辑会先 flush
        # 出去、再被卡点回滚掉,白丢一次输入。_mark_published 里还有一道兜底。
        credential_service.require_ready(db, tmpl)
    latest = latest_version(db, tmpl.id)
    sql_text = data.sql_text if data.sql_text is not None else (latest.sql_text if latest else "")

    if data.name is not None:
        tmpl.name = data.name
    if data.description is not None:
        tmpl.description = data.description
    if data.tags is not None:
        tmpl.tags = data.tags
    if data.datasource_id is not None:
        tmpl.datasource_id = data.datasource_id
    # 刻意**不动 team_id**:TemplateUpdateIn 里根本没有这个字段。转移团队会同时改变可见范围
    # 与取数身份,是平台管理员的治理动作,走 PUT /tasks/{id}/team。
    # 超时:编辑器每次提交完整表单,直接覆盖(None=恢复引擎默认)
    tmpl.timeout_seconds = data.timeout_seconds
    # 「允许 API 调用」开关:None = 本次未动(与 name 等字段同一约定),显式 true/false 才改
    if data.allow_api is not None:
        tmpl.allow_api = data.allow_api
    if data.allow_result_reuse is not None:
        tmpl.allow_result_reuse = data.allow_result_reuse
    # 方言始终跟随数据源引擎
    ds = db.get(DataSource, tmpl.datasource_id)
    tmpl.dialect = ds.engine if ds else tmpl.dialect
    validate_readonly(sql_text, tmpl.dialect)

    params = data.params if data.params is not None else (latest.params if latest else [])
    version = TemplateVersion(
        template_id=tmpl.id,
        version_no=_next_version_no(db, tmpl.id),
        sql_text=sql_text,
        params=_normalize_params(sql_text, params),
        author_id=author.id,
    )
    db.add(version)
    db.flush()

    # 订阅计划落库与「开订阅的任务不能有变量」卡点(按提交后的净状态判,见
    # subscription_service.apply_template_save)。显式 enabled=False 视为先关闭订阅:
    # 清退订阅者随本事务落库,通知在 commit 之后发(_push 自带 commit,不能夹在事务中间)。
    closed_subscriber_ids = subscription_service.apply_template_save(
        db, tmpl, data.subscription, version.params, author
    )

    # 已上线任务被编辑:新版本自动接替上线,状态与可运行性不变;
    # 草稿(draft)/已下线则维持原状态,由列表「上线」操作再晋升。
    if was_published:
        _mark_published(db, tmpl, version, author, "编辑保存自动上线")

    _sync_enum_cache(db, tmpl, version, data, author)
    db.commit()
    db.refresh(version)
    if closed_subscriber_ids:
        notify_service.notify_subscription_closed(db, tmpl, closed_subscriber_ids)
    return version


def latest_version(db: Session, template_id: int) -> TemplateVersion | None:
    return db.scalar(
        select(TemplateVersion)
        .where(TemplateVersion.template_id == template_id)
        .order_by(TemplateVersion.version_no.desc())
        .limit(1)
    )


def _mark_published(
    db: Session, tmpl: SqlTemplate, version: TemplateVersion, publisher: User, note: str | None
) -> None:
    """把某版本标记为已上线并写发布留痕(不提交,由调用方统一 commit)。

    上线卡点也放在这里:所有上线入口(publish、编辑已上线任务自动接替上线,以及以后新加的)
    都必经此函数,校验写在这一处就不可能被绕过。
    """
    credential_service.require_ready(db, tmpl)
    version.accepted_by = publisher.id
    version.accepted_note = note
    tmpl.published_version_id = version.id
    tmpl.status = STATUS_PUBLISHED


def publish(db: Session, tmpl: SqlTemplate, publisher: User, note: str | None) -> TemplateVersion:
    """上线最新版本(accepted_by/accepted_note 作上线留痕)。返回被上线的版本,
    免得调用方为了拿 version_no 再查一次。"""
    version = latest_version(db, tmpl.id)
    if version is None:
        raise RubicError("该任务还没有可上线的版本")
    _mark_published(db, tmpl, version, publisher, note)
    db.commit()
    return version


def archive(db: Session, tmpl: SqlTemplate) -> None:
    """收进回收站。**草稿也能进** —— 回收站是「不在台面上的任务」的统一去处,不只是
    已上线任务的下线站;否则一个废弃的草稿除了留在列表里碍眼之外没有任何出路(任务不可删)。
    对草稿而言 published_version_id 本来就是 None,这里的清空是幂等的。"""
    tmpl.status = STATUS_ARCHIVED
    tmpl.published_version_id = None
    db.commit()
    # 有订阅者时告知推送暂停。订阅关系与计划都保留:调度扫描只认 published
    # (subscription_service.tick),下线即天然停跑;重新上线后自动恢复并补跑最近一期。
    sched = subscription_service.get_schedule(db, tmpl.id)
    if sched is not None and sched.enabled:
        subs = subscription_service.subscribers_of(db, tmpl.id)
        if subs:
            notify_service.notify_subscription_paused(db, tmpl, [s.user_id for s in subs])


def unarchive(db: Session, tmpl: SqlTemplate) -> None:
    """从回收站退回草稿 —— 回收站的第二个出口(另一个是 publish 的「重新上线」)。

    只改状态:**不发通知**。订阅者关心的是「还推不推」,而退回草稿之后照样不推
    (调度扫描只认 published),对他们来说什么都没发生;真正该通知的那一刻是 archive
    和重新 publish,已经各自发过了。
    """
    if tmpl.status != STATUS_ARCHIVED:
        raise RubicError("只有回收站里的任务才能退回草稿")
    tmpl.status = STATUS_DRAFT
    db.commit()


# ---------------------------------------------------------------- 转移作者(离职交接)


def eligible_receivers(active_members: list[dict], tmpl: SqlTemplate) -> list[dict]:
    """在职同团队成员里排除当前作者 —— 「谁可以成为作者」的**唯一一句话**。**纯内存。**

    单任务的候选人接口直接回它;批量路径要判的是反向问题(「这个人能不能接这一个任务」),
    走 receiver_rejection —— 两者共享同一个判据:**在不在该团队的在职名单里,且不是当前作者**。
    规则要有两份表述(哪怕都在后端),迟早在长出第四条口径时只改到一处,
    而症状就是下拉里选得到、点了报错。
    """
    return [m for m in active_members if m["user_id"] != tmpl.author_id]


def receiver_rejection(
    tmpl: SqlTemplate, *, member_ids: set[int], user_id: int, name: str, is_active: bool
) -> tuple[str, str] | None:
    """这个人不能接手 tmpl 的 (代码, 给人看的整句话);能接手则返回 None。**纯内存。**

    放行判据只有一条 —— 「在该团队的在职名单里,且不是当前作者」(= eligible_receivers
    的逆问题);下面的分支只负责**把拒绝的理由说清楚**,不再各自重述一遍规则。
    代码(code)只给前端分组计数与测试断言用,面向用户的永远是那句中文。

    刻意只收 id / 姓名 / 在职三个标量而不是一个 ORM User:候选人接口手上是
    active_members_by_team 回的名单 dict,为了喂一个 User 再查一次库,查回来的也只有
    这三样(名字叫 AuthorTransferRejection 的那个 dataclass 是结果,不要与本函数混淆)。
    """
    if user_id != tmpl.author_id and user_id in member_ids:
        return None
    if user_id == tmpl.author_id:
        return ("already_author", f"《{tmpl.name}》的作者已经是 {name},无需转移")
    if not is_active:
        return ("inactive", f"{name} 的账号已停用,不能接手任务")
    return (
        "not_in_team",
        f"{name} 不是团队《{tmpl.team_name}》的成员,不能成为该任务的作者",
    )


def _apply_author_transfers(db: Session, tmpls: list[SqlTemplate], target: User) -> set[int]:
    """改 author_id,并清掉新作者那些已成冗余的 edit 授权。返回被清掉的任务 id。
    **不提交、不校验、不通知** —— 校验由调用方在此之前完成。

    「转移到底动了哪些行」只有这一处定义。批量是主入口、单任务传一个元素的列表进来
    (同 team_service.members_by_team / members_of 的分工):逐条调单个版会发 N 条
    DELETE 往返,而这件事本来就有批量原语(permission_service.discard_edit_grants)。

    **跟着 author_id 自动走的**(这里什么都不用做):编辑权第 3 条
    (permission_service.can_edit)、任务列表的「我开发的」、编辑人名单里的 source="author"、
    订阅定时运行失败的收件人(notify_service._team_fixers(include_author=True))、
    以及各处展示的作者名(SqlTemplate.author_name 是 join 出来的 property,不落库)。

    **刻意不动的**:
      - TemplateVersion.author_id 与 accepted_by —— 那是「这一版是谁保存的 / 谁上线的」
        不可变历史快照,不是归属。改了会让审计里的 task_update 与版本表互相打脸,
        「谁写坏了这版 SQL」也就永久查不出。同构先例见 add_version:被授予编辑权的人
        保存新版本时,版本作者记他,任务作者不变。
      - 该任务上**其他人**的 edit 授权 —— 与转移团队的连带撤销适用条件正好相反:
        那边的前提(同团队)已不成立,这边团队没变,前提仍然成立。
      - 业务授权(view/run/download)、订阅关系、运行记录、共享候选值 —— 都不以作者为键。
      - **不给原作者补一条 edit 授权**:本功能的场景是离职交接,「让他不再能改」正是目的。
        况且在这里偷偷造一条 Permission 行,等于一个没有 task_edit_grant 事件的权限 ——
        审计上查无此授权。确有需要时由团队管理员在「任务编辑权」里显式授予,那才留得下痕。

    **唯一主动做的连带清理**是新作者原先那条 edit 授权行:作者身份已经覆盖它,而
    editors_by_template 里 author 会盖住 granted,那行从此在 UI 上看不见、也就撤不掉;
    等哪天作者再被转走,它会静默复活成一条编辑权。
    """
    # 不提交,与下面的 author_id 一起进同一个事务(故不能用 revoke_edit,它自带 commit)
    revoked = permission_service.discard_edit_grants(
        db, template_ids=[t.id for t in tmpls], user_id=target.id
    )
    for tmpl in tmpls:
        tmpl.author_id = target.id
    return revoked


def transfer_author(
    db: Session, tmpl: SqlTemplate, target: User, *, team, operator: User
) -> tuple[int, bool]:
    """把任务的作者转给同团队的另一名成员。返回 (原作者 id, 是否清掉了冗余的 edit 授权)。

    发起人资格由路由层的 permission_service.require_can_transfer_author 判完;这里只负责
    编排 —— 校验(receiver_rejection)与变更(_apply_author_transfers)都与批量路径共用,
    本函数自己只多做两件事:提交,以及发单任务那两条通知。批量版见 transfer_author_batch,
    差别正是这两件(同 permission_service 里 discard_edit_grant 与 revoke_edit 的关系:
    差的只有一个 commit)。
    """
    # 读到最新已提交值再判「已经是作者」——两个管理员同时点同一个人时,
    # 这能把静默双写降级成一条干净的 400(不为此引入乐观锁,transfer_template 也没有)
    db.refresh(tmpl)

    rejection = receiver_rejection(
        tmpl,
        member_ids={m["user_id"] for m in team_service.members_of(db, team.id, active_only=True)},
        user_id=target.id, name=target.name, is_active=target.is_active,
    )
    if rejection is not None:
        raise RubicError(rejection[1])

    old_author_id = tmpl.author_id
    revoked = _apply_author_transfers(db, [tmpl], target)
    db.commit()

    notify_service.notify_author_transferred(
        db, tmpl, old_author_id=old_author_id, operator=operator
    )
    return old_author_id, tmpl.id in revoked


def author_candidates(db: Session, tmpl: SqlTemplate, *, team) -> list[dict]:
    """能接手这个任务的人:所属团队的**在职**成员,排除当前作者。

    这是「谁可以成为作者」的唯一出处 —— transfer_author 的放行判据就是「在不在这份名单里」,
    候选人接口也直接回它。规则本身写在 eligible_receivers 里(批量路径共用同一句)。
    """
    return eligible_receivers(team_service.members_of(db, team.id, active_only=True), tmpl)


# ---------------------------------------------------------------- 批量转移作者(多选交接)
# 「一次把 N 个任务交给同一个接手人」。语义是**全成功才生效**:校验相位与写入相位完全
# 分开 —— author_transfer_plan 只读、不写、不抛,把拒绝当数据回;有任何一条不行,调用方
# 在**一次写入都还没发生**时就返回。这比「写了再回滚」更强,也不依赖 get_db 的清理行为。


@dataclass(frozen=True)
class AuthorTransferItem:
    """一条**已通过全部校验**的待转移项。"""

    tmpl: SqlTemplate
    old_author_id: int
    old_author_name: str | None       # 必须在改 author_id 之前取(同单任务路由的理由)
    initiated_as: str                 # permission_service.transfer_author_role,**逐条算**


@dataclass(frozen=True)
class AuthorTransferRejection:
    """一条不能转的任务,以及为什么。整批拒绝时这些话会原样出现在给用户的回执里。"""

    template_id: int
    template_name: str | None
    code: str       # no_team | no_right | already_author | inactive | not_in_team | not_found
    message: str


def _member_ids(rosters: dict[int, list[dict]]) -> dict[int, set[int]]:
    """名单 → 每队的在职成员 id 集合。**只 build 一次**:候选人接口要拿同一批任务对
    几十个人各判一遍,而每次判定只问一句 `x in ?` —— 每对都重建一次长度 M 的列表,
    就是「人数 × 任务数」次无谓拷贝。"""
    return {tid: {m["user_id"] for m in ms} for tid, ms in rosters.items()}


def _reject(tmpl: SqlTemplate, code: str, message: str) -> AuthorTransferRejection:
    return AuthorTransferRejection(
        template_id=tmpl.id, template_name=tmpl.name, code=code, message=message
    )


def transfer_author_scope(
    scope, tmpls: list[SqlTemplate]
) -> tuple[list[SqlTemplate], list[AuthorTransferRejection]]:
    """把一批任务分成「我有权处分的」与「我无权处分的(附理由)」。**与接手人无关**,
    所以候选人接口只算一次,而不是对每个候选人各算一遍。

    理由一律来自 permission_service.author_transfer_denial —— 无主任务、编辑权不含处分权、
    完全无权三句话都在那里,前端原样显示。
    """
    mine: list[SqlTemplate] = []
    rejections: list[AuthorTransferRejection] = []
    for tmpl in tmpls:
        denial = permission_service.author_transfer_denial(scope, tmpl)
        if denial is None:
            mine.append(tmpl)
        else:
            rejections.append(_reject(tmpl, "no_team" if tmpl.team_id is None else "no_right", denial))
    return mine, rejections


def author_transfer_plan(
    db: Session, tmpls: list[SqlTemplate], target: User, *, scope, member_ids=None
) -> tuple[list[AuthorTransferItem], list[AuthorTransferRejection]]:
    """把「这批任务能不能转给 target」一次算完。**只读、不写、不抛**(拒绝以数据回)。

    两个调用点共用它:批量执行接口(有拒绝就整批拒绝),以及候选人接口(拿通过的 id 当
    eligible_template_ids、拿 rejections 当置灰理由)。这就是「规则只有一份」在本功能里的
    落点 —— **前端置灰时显示的那句话,与点下去会说的那句话,来自同一次调用**。

    查询次数与任务数无关:团队与作者随任务 join 回来(lazy="joined"),成员名单一次
    查完,发起人资格全在 scope 上做纯内存运算(故 scope 由调用方建一次传进来 ——
    它是固定 2 次查询,在循环里建就是 2N 次)。member_ids 同理可由调用方传入。
    """
    if member_ids is None:
        member_ids = _member_ids(
            team_service.active_members_by_team(
                db, {t.team_id for t in tmpls if t.team_id is not None}
            )
        )
    mine, rejections = transfer_author_scope(scope, tmpls)
    items: list[AuthorTransferItem] = []
    for tmpl in mine:
        rejection = receiver_rejection(
            tmpl,
            member_ids=member_ids.get(tmpl.team_id, set()),
            user_id=target.id, name=target.name, is_active=target.is_active,
        )
        if rejection is not None:
            rejections.append(_reject(tmpl, *rejection))
            continue
        items.append(
            AuthorTransferItem(
                tmpl=tmpl,
                old_author_id=tmpl.author_id,
                old_author_name=tmpl.author_name,
                initiated_as=permission_service.transfer_author_role(scope, tmpl),
            )
        )
    return items, rejections


@dataclass(frozen=True)
class _Receiver:
    """候选人接口喂给 plan 的「接手人」。名单来自 active_members_by_team,在职恒真 ——
    为了满足一个 `target.is_active` 再查一遍 users 表,查回来的也只有这三样。"""

    id: int
    name: str
    is_active: bool = True


def author_transfer_candidates(db: Session, scope) -> tuple[list[AuthorTransferRejection], list[dict]]:
    """批量交接的第一步:**(与接手人无关的拒绝, 每个候选人各自能接哪些)**。

    返回的两截恰好把「我看得见的全部任务」划分完:第一截是我无权处分的(附理由),
    第二截里每个候选人的 eligible + blocked 划分掉其余的。前端据此三态渲染,
    每一句话都来自服务端 —— 它自己不许再推一遍「同团队 ∧ 在职 ∧ 非当前作者」。

    与接手人无关的那部分(无主任务、我有没有处分权)**只算一次**:它不依赖候选人,
    放进 per-candidate 循环就是把同样的判定与同样的 f-string 重算「候选人数」遍。
    """
    tmpls = permission_service.visible_templates(db, scope)
    mine, base_rejections = transfer_author_scope(scope, tmpls)
    if not mine:
        return base_rejections, []

    rosters = team_service.active_members_by_team(db, {t.team_id for t in mine})
    member_ids = _member_ids(rosters)
    profiles: dict[int, dict] = {}
    for ms in rosters.values():
        for m in ms:
            profiles.setdefault(m["user_id"], m)

    out: list[dict] = []
    for uid, profile in profiles.items():
        items, rejections = author_transfer_plan(
            db, mine, _Receiver(uid, profile["name"]), scope=scope, member_ids=member_ids
        )
        out.append({**profile, "items": items, "rejections": rejections})
    # 能接得最多的排前面:离职交接时接手人多半是同团队那个,省一次翻找
    out.sort(key=lambda c: (-len(c["items"]), c["user_id"]))
    return base_rejections, out


def transfer_author_batch(
    db: Session, items: list[AuthorTransferItem], target: User, *, operator: User
) -> set[int]:
    """一个事务改完一批,然后发合并通知。返回「清掉了冗余 edit 授权」的任务 id ——
    那一位要逐条进审计 detail,与单任务那条的键一模一样。

    **调用方必须先用 author_transfer_plan 校验过**:本函数不再判断 —— 「全成功才生效」
    要求校验全部发生在任何写入之前,把校验混进循环就等于承认「转到第 7 条才发现不行」。

    通知整段被 try 包住:业务已经 commit 了,此时抛错会让前端收到 500 并重试,而重试
    会撞上「已经是作者」的整批拒绝 —— 用户看到的是「第一次失败、第二次说我早就转过了」,
    比不发通知糟得多。单任务路径刻意维持原样(零行为变化),不对称是有意的。
    """
    revoked = _apply_author_transfers(db, [i.tmpl for i in items], target)
    db.commit()

    try:
        notify_service.notify_author_transferred_batch(
            db, items=[(i.tmpl, i.old_author_id) for i in items], operator=operator
        )
    except Exception:  # noqa: BLE001 通知不能回头连累已经生效的转移
        log.exception("批量转移作者的通知发送失败(转移本身已生效):to_user_id=%s", target.id)
    return revoked


# ---------------------------------------------------------------- 闲置(长期没人运行)
# 「这个任务还有没有人在用」是任务生命周期的判断(下线的依据),与 publish / archive 同属本模块。
# 不放在 routes.tasks.list_tasks 里:那已经是个「把六七种事实拼成一行」的装配函数,再塞一段
# 日期算术,口径就藏进装配里了 —— 而这条口径以后还要被「闲置任务清单」之类的地方复用。


def idle_days(tmpl: SqlTemplate, last_run_at: datetime | None, now: datetime) -> int | None:
    """这个任务有多少天没被运行过;**None = 不参与闲置判定**。

    只判定已上线的任务:草稿还没给人用,已下线的已经下线了 —— 给它们算「闲置」得不出任何
    可执行的下一步,只会多一堆要解释的噪声。于是「idle_days 非空」与「参与判定」是同一件事,
    消费方不必再记一条「草稿要另外排除」的规则。

    从未运行过的从 created_at 起算:「上线至今没人跑过」比「跑过但很久没跑」更该被看见,
    给它空值等于把它藏起来。

    时间一律用**朴素** datetime.now():库里存的就是朴素本地时间(TimestampMixin 的
    server_default=func.now()),与 QueryJob.result_expired 同一把尺 —— 混进 utcnow /
    带时区的值会凭空差出 8 小时,在 90 天的尺度上看不出来,在边界上就是一条错标。

    now 由调用方传,且**没有默认值**:一次请求几百行要用同一把尺,逐行取 now 会让跨秒的
    那一批出现 89 / 90 两种结果 —— 那正是「标题栏说 3 个闲置、列表里只找得到 2 个」的来源。
    留一个 now=None 的兜底等于把这个 bug 的入口一直开着,所以让调用方必须交代清楚。
    """
    if tmpl.status != STATUS_PUBLISHED:
        return None
    since = last_run_at or tmpl.created_at
    if since is None:  # created_at 是 NOT NULL,理论上到不了;真到了按「不判定」,不谎报闲置
        return None
    return max((now - since).days, 0)


def is_idle(days: int | None) -> bool:
    """超过阈值即闲置。**阈值只在这里比这一次** —— 含「TASK_IDLE_DAYS <= 0 即关闭提示」
    这条换算,别在别处再判一遍 <= 0:漏判一处的后果不是报错,而是「阈值 0 天 ⇒ 所有任务
    都闲置」那种静默走样。"""
    limit = settings.TASK_IDLE_DAYS
    return limit > 0 and days is not None and days >= limit


def _ceiling_note(used: int, task_timeout: int, message: str) -> str:
    """超时报错时补一句「这是谁的上限」。只在前台上限真的夹到了任务超时时才加。

    不加的话,一个配了 30 分钟超时的 Hive 任务在试跑里 180 秒被砍,作者看到的只有
    「查询超时」—— 他会去改任务的超时配置,而那个配置本来就是对的。
    """
    if used >= task_timeout:
        return ""
    if "超时" not in message and "timeout" not in message.lower():
        return ""
    # 不加 markdown 记号:这句话经 message.error 渲染成纯文本,`**` 会原样出现在用户眼前
    return (
        f"(这是试跑的 {used} 秒上限;该任务正式运行时的上限是 {task_timeout} 秒,"
        "同一条 SQL 正式跑未必超时)"
    )


def test_run(db: Session, data, user: User | None = None) -> dict:
    """作者自检试跑:返回样例行。若关联到已存在任务(data.template_id),
    则同时落一条 source=test 的运行记录(可预览/导出、在运行记录里与正式取数区分),
    但**不发通知**;新建未保存任务(无 template_id)时不留痕,仅返回样例行。
    """
    ds = db.get(DataSource, data.datasource_id)
    if ds is None:
        raise NotFoundError("数据源不存在")
    validate_readonly(data.sql_text, ds.engine)  # 方言取自数据源
    # 试跑用**任务所属团队**的取数账号 —— 与正式取数完全同一套身份,所以「试跑通过」
    # 就等于「上线后能跑」。for_team 内部会校验操作者是该团队成员(平台管理员除外),
    # 那道校验必须长在服务层:team_id 是客户端传来的。
    credential = credential_service.for_team(
        db, team_id=data.team_id, datasource_id=ds.id, actor=user
    )
    bound = params_service.validate_and_bind(data.params, data.values)
    sql_text, bound = params_service.expand_list_params(data.sql_text, bound)  # 展开正选 IN
    # 与正式取数同一口径:落库前按字节截断,免得试跑也被那个 Text 列的上限炸掉
    executed_sql = clip_executed_sql(params_service.render_sql(sql_text, bound))

    # 关联到已存在任务时,先建一条 running 的试跑记录
    job = None
    tmpl = db.get(SqlTemplate, data.template_id) if getattr(data, "template_id", None) else None
    if tmpl is not None and user is not None:
        # 归属校验也必须长在服务层:template_id 和 team_id 一样是客户端传来的。少了它,
        # 甲队的人就能把一条自己写的 SQL 的试跑记录挂到乙队任务名下,污染那个任务的运行记录。
        # 判据复用 can_edit_template(「能不能动这个任务」的单一入口,授权接口也走它),
        # 而不是「team_id 必须相等」—— 后者会拦住平台管理员在编辑器里换完团队、还没保存
        # 就点测试运行这条正常路径。
        if not permission_service.can_edit_template(db, user, tmpl.id):
            raise PermissionDeniedError(
                f"无权把试跑记录挂到任务《{tmpl.name}》上:需为该任务的作者、"
                "所属团队的团队管理员,或已获得该任务的编辑授权"
            )
        job = QueryJob(
            user_id=user.id,
            template_id=tmpl.id,
            template_version_id=tmpl.published_version_id,  # 试跑可能没有已发布版本,可空
            datasource_id=ds.id,
            params=data.values,
            status=JOB_RUNNING,
            source=SOURCE_TEST,
            executed_sql=executed_sql,
            run_as_team_id=credential.owner_team_id,
            run_as_username=credential.username if credential.is_team_account else None,
        )
        db.add(job)
        db.commit()
        db.refresh(job)

    connector = get_connector(ds, credential)
    # 试跑取样行数:配了行数上限就别越过它,没配(默认不限)就按前端要的取
    # —— 直接 min(data.limit, MAX_RESULT_ROWS) 会在「不限」时算出 0 行。
    cap = settings.result_row_cap
    limit = data.limit if cap is None else min(data.limit, cap)
    # 与正式取数同一个口径,再夹一道前台上限(见 TEST_RUN_TIMEOUT_CEILING_SECONDS)
    task_timeout = query_service.effective_timeout(tmpl, ds)
    timeout = min(task_timeout, TEST_RUN_TIMEOUT_CEILING_SECONDS)
    try:
        result = connector.execute(
            sql_text,
            bound,
            timeout_seconds=timeout,
            max_rows=limit,
        )
    except (RubicError, Exception) as e:  # noqa: BLE001 -- 引擎错误转可读 400,并把试跑记录标记失败
        # 与 query_service 同一口径:面向用户的文案要抹掉团队库账号名。试跑记录也会
        # 出现在该任务的「运行记录」里,而那对被授权的业务用户可见。
        safe = credential_service.redact(str(e), credential)
        safe += _ceiling_note(timeout, task_timeout, safe)
        if job is not None:
            job.status = JOB_FAILED
            job.error = safe[:2000]
            db.commit()
        if isinstance(e, RubicError):
            raise RubicError(safe) from e
        raise RubicError(f"试跑失败:{safe[:500]}") from e

    # 成功:存结果文件(便于运行记录里预览/导出),推进记录状态
    if job is not None:
        filename = f"{tmpl.name}_{job.id}.csv"
        object_key = f"jobs/{job.id}/{filename}"
        # 试跑结果本来就已经在内存里(取样,最多 data.limit 行),照旧一次写完;
        # 唯一的 CSV 写法住在 result_service,这里不自己拼字节。
        result_service.write_csv(object_key, result.columns, result.rows)
        job.status = JOB_SUCCESS
        job.row_count = result.row_count
        job.duration_ms = result.meta.get("duration_ms")
        job.result_object_key = object_key
        job.result_filename = filename
        db.commit()

    return {
        "columns": result.columns,
        "rows": [list(r) for r in result.rows],
        "truncated": result.truncated,
        "row_count": result.row_count,
        "executed_sql": executed_sql,
    }

"""取数任务(SQL 模板)的编写、版本、上线/下线、试跑。

术语:产品 UI 里的「任务」= 这里的 SqlTemplate;「上线 / 下线」= publish / archive。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.connectors import get_connector
from app.core.config import settings
from app.core.exceptions import NotFoundError, PermissionDeniedError, RubicError
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
)


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
    limit = min(data.limit, settings.MAX_RESULT_ROWS)
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
        result_service.upload_csv(object_key, result_service.to_csv_bytes(result))
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

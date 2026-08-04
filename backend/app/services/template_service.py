"""取数任务(SQL 模板)的编写、版本、上线/下线、试跑。

术语:产品 UI 里的「任务」= 这里的 SqlTemplate;「上线 / 下线」= publish / archive。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.connectors import get_connector
from app.core.config import settings
from app.core.exceptions import NotFoundError, RubicError
from app.core.sql_gateway import validate_readonly
from app.models.datasource import DataSource
from app.models.query_job import JOB_FAILED, JOB_RUNNING, JOB_SUCCESS, SOURCE_TEST, QueryJob
from app.models.template import (
    STATUS_ARCHIVED,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    SqlTemplate,
    TemplateVersion,
)
from app.models.user import User
from app.schemas.common import ParamDef
from app.services import params_service, result_service


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
    db.commit()
    db.refresh(tmpl)
    return tmpl


def add_version(db: Session, author: User, tmpl: SqlTemplate, data) -> TemplateVersion:
    """更新模板 = 生成新版本。编辑不改变上线状态:
    - 原本已上线(published)→ 新版本自动接替上线,保持对业务可运行;
    - 原本草稿(draft)/已下线 → 维持原状态,由列表「上线」操作再晋升。
    """
    was_published = tmpl.status == STATUS_PUBLISHED
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

    # 已上线任务被编辑:新版本自动接替上线,状态与可运行性不变;
    # 草稿(draft)/已下线则维持原状态,由列表「上线」操作再晋升。
    if was_published:
        _mark_published(tmpl, version, author, "编辑保存自动上线")

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


def _mark_published(tmpl: SqlTemplate, version: TemplateVersion, publisher: User, note: str | None) -> None:
    """把某版本标记为已上线并写发布留痕(不提交,由调用方统一 commit)。"""
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
    _mark_published(tmpl, version, publisher, note)
    db.commit()
    return version


def archive(db: Session, tmpl: SqlTemplate) -> None:
    tmpl.status = STATUS_ARCHIVED
    tmpl.published_version_id = None
    db.commit()


def test_run(db: Session, data, user: User | None = None) -> dict:
    """作者自检试跑:返回样例行。若关联到已存在任务(data.template_id),
    则同时落一条 source=test 的运行记录(可预览/导出、在运行记录里与正式取数区分),
    但**不发通知**;新建未保存任务(无 template_id)时不留痕,仅返回样例行。
    """
    ds = db.get(DataSource, data.datasource_id)
    if ds is None:
        raise NotFoundError("数据源不存在")
    validate_readonly(data.sql_text, ds.engine)  # 方言取自数据源
    bound = params_service.validate_and_bind(data.params, data.values)
    sql_text, bound = params_service.expand_list_params(data.sql_text, bound)  # 展开正选 IN
    executed_sql = params_service.render_sql(sql_text, bound)

    # 关联到已存在任务时,先建一条 running 的试跑记录
    job = None
    tmpl = db.get(SqlTemplate, data.template_id) if getattr(data, "template_id", None) else None
    if tmpl is not None and user is not None:
        job = QueryJob(
            user_id=user.id,
            template_id=tmpl.id,
            template_version_id=tmpl.published_version_id,  # 试跑可能没有已发布版本,可空
            datasource_id=ds.id,
            params=data.values,
            status=JOB_RUNNING,
            source=SOURCE_TEST,
            executed_sql=executed_sql,
        )
        db.add(job)
        db.commit()
        db.refresh(job)

    connector = get_connector(ds)
    limit = min(data.limit, settings.MAX_RESULT_ROWS)
    try:
        result = connector.execute(
            sql_text,
            bound,
            timeout_seconds=settings.QUERY_TIMEOUT_SECONDS,
            max_rows=limit,
        )
    except (RubicError, Exception) as e:  # noqa: BLE001 -- 引擎错误转可读 400,并把试跑记录标记失败
        if job is not None:
            job.status = JOB_FAILED
            job.error = str(e)[:2000]
            db.commit()
        if isinstance(e, RubicError):
            raise
        raise RubicError(f"试跑失败:{str(e)[:500]}") from e

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


# 「枚举值获取 SQL」一次最多返回的候选数
_ENUM_SQL_CAP = 1000


def run_value_query(db: Session, datasource_id: int, sql: str) -> dict:
    """跑一段作者写的「枚举值获取 SQL」,取结果第一列的去重值,给业务填参做候选。"""
    ds = db.get(DataSource, datasource_id)
    if ds is None:
        raise NotFoundError("数据源不存在")
    if not (sql or "").strip():
        raise RubicError("未配置枚举值获取 SQL")
    validate_readonly(sql, ds.engine)
    connector = get_connector(ds)
    try:
        result = connector.execute(
            sql, {}, timeout_seconds=settings.QUERY_TIMEOUT_SECONDS, max_rows=_ENUM_SQL_CAP
        )
    except RubicError:
        raise
    except Exception as e:  # noqa: BLE001
        raise RubicError(f"获取枚举值失败:{str(e)[:400]}") from e
    values: list[str] = []
    seen: set[str] = set()
    for row in result.rows:
        if not row or row[0] is None:
            continue
        s = str(row[0])
        if s not in seen:
            seen.add(s)
            values.append(s)
    return {
        "values": values,
        "truncated": result.truncated,
        "duration_ms": result.meta.get("duration_ms"),
    }

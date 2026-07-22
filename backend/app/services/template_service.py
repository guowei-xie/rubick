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
    dialect = ds.engine  # 方言由数据源引擎决定,不再手填
    validate_readonly(data.sql_text, dialect)

    tmpl = SqlTemplate(
        name=data.name,
        domain=data.domain,
        description=data.description,
        tags=data.tags,
        datasource_id=data.datasource_id,
        dialect=dialect,
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
    # 方言始终跟随数据源引擎
    ds = db.get(DataSource, tmpl.datasource_id)
    tmpl.dialect = ds.engine if ds else tmpl.dialect
    validate_readonly(sql_text, tmpl.dialect)
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
    validate_readonly(data.sql_text, ds.engine)  # 方言取自数据源
    bound = params_service.validate_and_bind(data.params, data.values)
    sql_text, bound = params_service.expand_list_params(data.sql_text, bound)  # 展开正选 IN

    connector = get_connector(ds)
    limit = min(data.limit, settings.MAX_RESULT_ROWS)
    try:
        result = connector.execute(
            sql_text,
            bound,
            timeout_seconds=settings.QUERY_TIMEOUT_SECONDS,
            max_rows=limit,
        )
    except RubicError:
        raise
    except Exception as e:  # noqa: BLE001 -- 把引擎执行错误变成可读的 400,而不是 500
        raise RubicError(f"试跑失败:{str(e)[:500]}") from e
    return {
        "columns": result.columns,
        "rows": [list(r) for r in result.rows],
        "truncated": result.truncated,
        "row_count": result.row_count,
        "executed_sql": params_service.render_sql(sql_text, bound),
    }


# 候选枚举值发现:一次最多返回这么多个不同取值;超过则大概率不适合做下拉枚举
_ENUM_VALUES_CAP = 500

# 「枚举值获取 SQL」一次最多返回的候选数
_ENUM_SQL_CAP = 1000


def run_value_query(db: Session, datasource_id: int, sql: str) -> dict:
    """跑一段分析师写的「枚举值获取 SQL」,取结果第一列的去重值,给业务填参做候选。"""
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
    return {"values": values, "truncated": result.truncated}


def discover_enum_values(db: Session, data) -> dict:
    """自动发现变量对应字段的候选枚举值。

    做法:用 sqlglot 解析 SQL,定位 `字段 = :变量` / `字段 IN (:变量)` 里的字段,
    重写成 `SELECT DISTINCT 字段 FROM ... WHERE <保留的静态过滤> AND 字段 IS NOT NULL`
    (含变量的谓词全部去掉,静态过滤保留),读只读连接器取回全部取值。
    """
    ds = db.get(DataSource, data.datasource_id)
    if ds is None:
        raise NotFoundError("数据源不存在")
    validate_readonly(data.sql_text, ds.engine)  # 源 SQL 先过安全网关

    column, disco_sql = _build_enum_query(data.sql_text, data.variable, ds.engine)
    validate_readonly(disco_sql, ds.engine)  # 生成的发现查询也过一遍网关(纵深防御)

    connector = get_connector(ds)
    try:
        result = connector.execute(
            disco_sql,
            {},
            timeout_seconds=settings.QUERY_TIMEOUT_SECONDS,
            max_rows=_ENUM_VALUES_CAP,
        )
    except RubicError:
        raise
    except Exception as e:  # noqa: BLE001
        raise RubicError(f"取候选值失败(字段 {column}):{str(e)[:400]}") from e
    values = [str(r[0]) for r in result.rows if r and r[0] is not None]
    return {"column": column, "values": values, "truncated": result.truncated}


def _build_enum_query(sql_text: str, variable: str, engine: str) -> tuple[str, str]:
    """返回 (字段表达式, 发现查询 SQL);无法识别时抛 RubicError。"""
    import sqlglot
    from sqlglot import exp

    dialect = engine if engine in ("hive", "mysql") else None
    try:
        tree = sqlglot.parse_one(sql_text, dialect=dialect)
    except Exception as e:  # noqa: BLE001
        raise RubicError(f"SQL 解析失败,无法自动取候选值:{e}") from e

    if not isinstance(tree, exp.Select) or "from" not in tree.args:
        raise RubicError("仅支持单层 SELECT(含 FROM)的查询自动取候选值,请手动填写枚举值")
    if tree.args.get("with"):
        raise RubicError("SQL 含 CTE(WITH …),结构较复杂,无法安全自动取候选值,请手动填写枚举值")
    from_this = tree.args["from"].this
    if not isinstance(from_this, exp.Table):
        raise RubicError("主查询来源不是单张表(含子查询/派生表),无法自动取候选值,请手动填写枚举值")

    col = None
    for node in tree.walk():
        node = node[0] if isinstance(node, tuple) else node
        if isinstance(node, exp.EQ):
            for side, other in ((node.left, node.right), (node.right, node.left)):
                if (
                    isinstance(side, exp.Placeholder)
                    and side.this == variable
                    and isinstance(other, exp.Column)
                ):
                    col = other
        elif isinstance(node, exp.In):
            phs = node.args.get("expressions") or []
            if isinstance(node.this, exp.Column) and any(
                isinstance(e, exp.Placeholder) and e.this == variable for e in phs
            ):
                col = node.this
    if col is None:
        raise RubicError(
            f"未能自动识别变量 :{variable} 对应的字段"
            "(仅支持「字段 = :变量」或「字段 IN (:变量)」这类等值筛选)"
        )

    preds = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.In, exp.Like, exp.ILike)

    def _has_placeholder(n) -> bool:
        for d in n.walk():
            d = d[0] if isinstance(d, tuple) else d
            if isinstance(d, exp.Placeholder):
                return True
        return False

    # 把所有含变量占位符的谓词替换成 TRUE,保留静态过滤条件
    stripped = tree.transform(
        lambda n: exp.true() if isinstance(n, preds) and _has_placeholder(n) else n
    )

    disco = exp.Select().select(exp.Distinct(expressions=[col.copy()]))
    disco.set("from", stripped.args["from"].copy())
    for j in stripped.args.get("joins", []) or []:
        disco = disco.join(j.copy())
    not_null = exp.Is(this=col.copy(), expression=exp.Not(this=exp.Null()))
    where = stripped.args.get("where")
    disco = disco.where(exp.and_(where.this, not_null) if where else not_null)
    disco = disco.order_by(exp.Literal.number(1)).limit(_ENUM_VALUES_CAP + 1)
    return col.sql(dialect), disco.sql(dialect)

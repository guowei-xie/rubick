"""值列表变量的**共享候选值** —— 让「枚举值获取 SQL」不再是一次性消费。

一句话:作者在编辑器测出来的候选值随任务保存落库,业务用户打开填参抽屉直接勾选(不跑 SQL);
任何有权限的人点一次「更新枚举值」就真跑一次并回写,结果全任务共享。

「取一列候选值」这件事(run_value_query)也归本模块:它不是模板编写逻辑,两个调用方
(作者测试端点、业务侧更新)都只关心枚举。放在这里,依赖方向才是干净的单向
**template_service → enum_cache_service**,于是候选值的维护(sync_params)可以放进
create_template / add_version 里 —— 与 _normalize_params 同一个持久化边界,和版本写入
同一个事务,不必由各个路由自己记得调。

过期判定不看时间,看「产出这批值的前提是否还成立」:enum_sql 指纹或数据源变了即作废。
按产品决策,过期时**不展示旧候选**,只回一个 stale 标记让前端解释为什么列表空了。
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.connectors import Credential, get_connector
from app.core.config import settings
from app.core.exceptions import NotFoundError, RubicError
from app.core.sql_gateway import validate_readonly
from app.models.datasource import DataSource
from app.models.template import SqlTemplate, TemplateEnumValues
from app.models.user import User
from app.schemas.common import ParamDef
from app.schemas.template import EnumSampleIn, SharedEnumValuesOut, ValueListOut
from app.services import credential_service

log = logging.getLogger(__name__)

# 单个变量候选值的字符总量上限:超了只记日志跳过,绝不 500(防手搓 payload 撑爆元数据库)
_MAX_SAMPLE_CHARS = 1_000_000


def run_value_query(
    db: Session, datasource_id: int, sql: str, credential: Credential
) -> ValueListOut:
    """跑一段作者写的「枚举值获取 SQL」,取结果第一列的去重值,给业务填参做候选。

    注:超时用全局 QUERY_TIMEOUT_SECONDS,不套用任务级 timeout_seconds 也不套 Hive 的
    长超时 —— 这是个用户点了按钮就在等的前台请求,不能让它占着请求线程跑一小时。

    credential 由调用方解析后传入且必填(本函数只管跑一列值,不替谁决定用哪个身份,
    也不给一个「忘了传就用公共账号」的缺省值)。**用谁的账号**这条规则住在
    services/credential_service —— 这里不复述,免得两处漂移。
    """
    ds = db.get(DataSource, datasource_id)
    if ds is None:
        raise NotFoundError("数据源不存在")
    if not (sql or "").strip():
        raise RubicError("未配置枚举值获取 SQL")
    validate_readonly(sql, ds.engine)
    connector = get_connector(ds, credential)
    try:
        result = connector.execute(
            sql, {}, timeout_seconds=settings.QUERY_TIMEOUT_SECONDS,
            max_rows=settings.ENUM_VALUE_CAP,
        )
    except RubicError:
        raise
    except Exception as e:  # noqa: BLE001
        # 与 query_service / template_service 同一口径:面向用户的文案要抹掉团队库账号名。
        # 业务用户点「更新枚举值」也会走到这里,而他根本不属于这个团队。
        raise RubicError(
            f"获取枚举值失败:{credential_service.redact(str(e), credential)[:400]}"
        ) from e
    values: list[str] = []
    seen: set[str] = set()
    for row in result.rows:
        if not row or row[0] is None:
            continue
        s = str(row[0])
        if s not in seen:
            seen.add(s)
            values.append(s)
    return ValueListOut(
        values=values, truncated=result.truncated, duration_ms=result.meta.get("duration_ms")
    )


def fingerprint(enum_sql: str | None) -> str:
    """enum_sql 的指纹。改一个空格不算变(strip),改内容才算。"""
    return hashlib.sha256((enum_sql or "").strip().encode("utf-8")).hexdigest()


def _row(db: Session, template_id: int, variable: str) -> TemplateEnumValues | None:
    return db.scalar(
        select(TemplateEnumValues).where(
            TemplateEnumValues.template_id == template_id,
            TemplateEnumValues.variable == variable,
        )
    )


def _is_stale(row: TemplateEnumValues, *, datasource_id: int | None, enum_sql: str | None) -> bool:
    """这批值的产出前提是否已经不成立(SQL 变了 / 换了数据源)。"""
    return row.enum_sql_hash != fingerprint(enum_sql) or row.datasource_id != datasource_id


def _out(db: Session, row: TemplateEnumValues, *, reused: bool = False) -> SharedEnumValuesOut:
    """把缓存行渲染成响应。更新人的名字实时解析,这样改名不会留下过期快照。"""
    u = db.get(User, row.updated_by) if row.updated_by else None
    return SharedEnumValuesOut(
        values=row.enum_values or [],
        truncated=row.truncated,
        duration_ms=row.duration_ms,
        cached=True,
        reused=reused,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
        updated_by_name=u.name if u else None,
    )


def read(db: Session, tmpl: SqlTemplate, pdef: dict) -> SharedEnumValuesOut:
    """读该变量的共享候选值。**纯读,绝不执行任何 SQL** —— 打开填参抽屉靠它秒开。

    未命中 / 已过期都不是错误,返回空候选 + cached=False(过期时另带 stale=True)。
    """
    row = _row(db, tmpl.id, pdef.get("name", ""))
    if row is None:
        return SharedEnumValuesOut(values=[], cached=False)
    if _is_stale(row, datasource_id=tmpl.datasource_id, enum_sql=pdef.get("enum_sql")):
        # 按产品决策:旧 SQL 的结果不能冒充新 SQL 的候选,一律不展示
        return SharedEnumValuesOut(values=[], cached=False, stale=True)
    return _out(db, row)


#: read_bulk 内部的哨兵。不能用 None —— enum_sql 本身就可以是 None
_MISSING = object()


def read_bulk(
    db: Session, items: Sequence[tuple[SqlTemplate, Sequence[ParamDef]]]
) -> dict[tuple[int, str], list[str]]:
    """批量读多任务多变量的共享候选值。**一次查询**,与任务数、变量数都无关。

    给开放 API 的任务列表用:那个端点一次返回全部可见任务,逐个 read() 就是
    N(任务) × M(变量) 次查询,而它是 Agent 每次开场的第一枪。
    刻意不复用 _out():它为了渲染「谁更新的」对每行再 db.get(User) 一次,正是要躲的
    N+1,而对外契约里根本没有更新人这一项。

    与 read() 同一条产品决策:未命中、以及产出前提已不成立的行(_is_stale:作者改了
    enum_sql 或换了数据源)**都不出现在返回里** —— 旧 SQL 的结果不冒充新 SQL 的候选。
    """
    wanted: dict[int, dict[str, str | None]] = {}
    ds_of: dict[int, int | None] = {}
    for tmpl, params in items:
        ds_of[tmpl.id] = tmpl.datasource_id
        for p in params:
            if p.has_enum_candidates:  # 口径唯一来源,见 schemas/common.ParamDef
                wanted.setdefault(tmpl.id, {})[p.name] = p.enum_sql
    if not wanted:
        return {}  # 一个值列表变量都没有 ⇒ 零查询

    out: dict[tuple[int, str], list[str]] = {}
    for row in db.scalars(
        select(TemplateEnumValues).where(TemplateEnumValues.template_id.in_(wanted))
    ):
        enum_sql = wanted.get(row.template_id, {}).get(row.variable, _MISSING)
        if enum_sql is _MISSING:  # 这个变量已经不是「有候选可言」的变量了
            continue
        if _is_stale(row, datasource_id=ds_of.get(row.template_id), enum_sql=enum_sql):
            continue
        out[(row.template_id, row.variable)] = row.enum_values or []
    return out


def refresh(db: Session, tmpl: SqlTemplate, pdef: dict, user: User) -> SharedEnumValuesOut:
    """真跑一次 enum_sql 并回写共享缓存;刚更新过则直接复用。"""
    variable = pdef.get("name", "")
    enum_sql = pdef.get("enum_sql")
    row = _row(db, tmpl.id, variable)
    if (
        row is not None
        and not _is_stale(row, datasource_id=tmpl.datasource_id, enum_sql=enum_sql)
        # updated_at 由本模块用应用时钟写入,与 datetime.now() 同一时钟(见 _apply)
        and datetime.now() - row.updated_at
        < timedelta(seconds=settings.ENUM_REFRESH_MIN_INTERVAL_SECONDS)
    ):
        return _out(db, row, reused=True)

    # 跑的是任务自带的 enum_sql,数据边界应与运行该任务一致 ⇒ 用**任务所属团队**的账号,
    # 而不是点按钮那位业务用户的(他根本不是团队成员,也没有库账号)
    credential = credential_service.for_template(db, tmpl)
    saved = upsert(
        db, template_id=tmpl.id, variable=variable, datasource_id=tmpl.datasource_id,
        enum_sql=enum_sql,
        result=run_value_query(db, tmpl.datasource_id, enum_sql, credential),
        user_id=user.id, row=row,
    )
    db.commit()
    return _out(db, saved)


def _apply(
    row: TemplateEnumValues,
    *,
    datasource_id: int | None,
    enum_sql: str | None,
    result: ValueListOut,
    user_id: int | None,
) -> TemplateEnumValues:
    values = result.values or []
    row.enum_values = values[: settings.ENUM_VALUE_CAP]
    row.truncated = bool(result.truncated) or len(values) > settings.ENUM_VALUE_CAP
    row.duration_ms = result.duration_ms
    row.datasource_id = datasource_id
    row.enum_sql_hash = fingerprint(enum_sql)
    row.updated_by = user_id
    # 显式用应用时钟写 updated_at,不吃 TimestampMixin 的 func.now():复用窗口要拿它和
    # datetime.now() 相减,而 DB 时钟未必与应用时钟同一时区(SQLite 的 CURRENT_TIMESTAMP
    # 是 UTC),靠"刚好一致"会让复用窗口静默失效。
    row.updated_at = datetime.now()
    return row


def upsert(
    db: Session,
    *,
    template_id: int,
    variable: str,
    datasource_id: int | None,
    enum_sql: str | None,
    result: ValueListOut,
    user_id: int | None,
    row: TemplateEnumValues | None = None,
) -> TemplateEnumValues:
    """按 (template_id, variable) 覆盖写。**不提交**,由调用方统一 commit(同 user_service 约定)。

    不用 MySQL 专属的 ON DUPLICATE KEY:测试跑在 SQLite 上。并发靠唯一约束兜底 ——
    两人同时点更新时后提交的赢(候选值是同一份快照,谁赢都对),撞了就重查再改。
    """
    if row is None:
        row = _row(db, template_id, variable)
    if row is not None:
        return _apply(
            row, datasource_id=datasource_id, enum_sql=enum_sql, result=result, user_id=user_id
        )

    row = _apply(
        TemplateEnumValues(template_id=template_id, variable=variable),
        datasource_id=datasource_id, enum_sql=enum_sql, result=result, user_id=user_id,
    )
    db.add(row)
    try:
        db.flush()  # 提前撞唯一约束,别等到调用方 commit 才炸
    except IntegrityError:
        db.rollback()  # 并发插入:重查那行改掉即可(约束保证这次必然查得到)
        existing = _row(db, template_id, variable)
        if existing is None:  # pragma: no cover 约束存在时不可能走到
            raise
        row = _apply(
            existing, datasource_id=datasource_id, enum_sql=enum_sql,
            result=result, user_id=user_id,
        )
    return row


def sync_params(
    db: Session,
    *,
    template_id: int,
    datasource_id: int | None,
    params: list[dict],
    samples: dict[str, EnumSampleIn] | None,
    user_id: int | None,
) -> None:
    """保存任务后把共享候选值对齐到新的 params。**不提交**,跟着版本写入同一个事务。

    两件事:
    1. 播种 —— 把作者刚测出来的候选落库,只在 sample.source_sql 与真正保存的 enum_sql
       一致时才采纳(作者「测完又改了 SQL 再保存」必须在持久化边界拦住,不能指望前端自律);
       samples 为空表示「本次没有新测的候选」,不动已有的。
    2. 剪枝 —— 清掉已经读不到的行(变量被删、改名,或不再是配了枚举 SQL 的值列表)。
    """
    wanted = {
        p["name"]: p.get("enum_sql")
        for p in (params or [])
        if p.get("name") and ParamDef.dict_has_enum_candidates(p)
    }

    for variable, enum_sql in wanted.items():
        sample = (samples or {}).get(variable)
        if sample is None or fingerprint(sample.source_sql) != fingerprint(enum_sql):
            continue  # 没测过,或测的不是最终落库的那段 SQL
        if sum(len(v) for v in sample.values) > _MAX_SAMPLE_CHARS:
            log.warning("枚举候选值过大,跳过落库:template=%s variable=%s", template_id, variable)
            continue
        upsert(
            db, template_id=template_id, variable=variable, datasource_id=datasource_id,
            enum_sql=enum_sql, result=sample, user_id=user_id,
        )

    db.execute(
        delete(TemplateEnumValues).where(
            TemplateEnumValues.template_id == template_id,
            TemplateEnumValues.variable.notin_(set(wanted)),
        )
    )

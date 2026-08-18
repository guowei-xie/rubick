"""共享枚举候选值:作者测试的结果不再一次性丢弃,业务用户可手动更新且全任务共享。

风格同 test_audit_coverage.py:直接调路由函数,不引入 TestClient。
所有用例都 monkeypatch enum_cache_service.run_value_query —— 单测绝不连任何数据源。
"""
import pytest
from sqlalchemy import func, select

from app.api.routes.tasks import refresh_task_enum_values, task_enum_values
from app.api.routes.templates import create_template, publish, update_template
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.exceptions import PermissionDeniedError, RubicError
from app.models.datasource import DataSource
from app.models.template import TemplateEnumValues
from app.models.user import ROLE_ADMIN, ROLE_USER, User
from app.schemas.common import ParamDef
from app.schemas.template import (
    EnumRefreshIn,
    EnumSampleIn,
    PublishIn,
    TemplateCreateIn,
    TemplateUpdateIn,
    ValueListOut,
)
from app.services import enum_cache_service, permission_service

ENUM_SQL = "SELECT DISTINCT c FROM dim_c"
SQL = "SELECT * FROM o WHERE c IN (:cs)"


@pytest.fixture
def admin(db):
    u = db.get(User, 9001)
    if u is None:
        u = User(id=9001, feishu_open_id="ou_enum_admin", name="枚举管理员", role=ROLE_ADMIN)
        db.add(u)
        db.commit()
    return u


def _datasource(db, name: str) -> DataSource:
    d = db.scalar(select(DataSource).where(DataSource.name == name))
    if d is None:
        d = DataSource(
            name=name, engine="mysql", host="localhost", port=3306,
            database="demo", username="u", password="p", extra={},
        )
        db.add(d)
        db.commit()
    return d


@pytest.fixture
def team(db, ds, ds2, team_factory, team_credential):
    """任务必属团队,且上线要求团队账号已测通。两个数据源都备好账号 ——
    有用例会把任务的数据源换成 ds2(验证候选值随数据源作废)。"""
    t = team_factory("enum-team", [])
    team_credential(t, ds, username="enum_team_acct")
    team_credential(t, ds2, username="enum_team_acct2")
    return t


@pytest.fixture
def ds(db):
    return _datasource(db, "enum-mysql")


@pytest.fixture
def ds2(db):
    return _datasource(db, "enum-mysql-2")


def _biz_user(db, uid: int, name: str) -> User:
    u = db.get(User, uid)
    if u is None:
        u = User(id=uid, feishu_open_id=f"ou_enum_{uid}", name=name, role=ROLE_USER)
        db.add(u)
        db.commit()
    return u


def _fake_query(monkeypatch, values, *, truncated=False, duration_ms=42):
    """替掉真实取数:记录调用次数,便于断言「没有跑 SQL」。"""
    calls = {"n": 0}

    def fake(db, datasource_id, sql, credential=None):
        calls["n"] += 1
        calls["credential"] = credential  # 取数身份(个人取数账号);开关关闭时为 None
        return ValueListOut(values=list(values), truncated=truncated, duration_ms=duration_ms)

    monkeypatch.setattr(enum_cache_service, "run_value_query", fake)
    return calls


def _make_published(db, admin, ds, team, *, name, samples=None, enum_sql=ENUM_SQL, sql=SQL):
    tmpl = create_template(
        TemplateCreateIn(
            name=name, team_id=team.id, datasource_id=ds.id, sql_text=sql,
            params=[ParamDef(name="cs", kind="list", enum_sql=enum_sql)],
            enum_samples=samples or {},
        ),
        db, admin, ip=None,
    )
    publish(tmpl.id, PublishIn(note="上线"), db, admin, ip=None)
    return tmpl


def _read(db, user, tmpl, variable="cs"):
    return task_enum_values(tmpl.id, variable, db, user)


def _refresh(db, user, tmpl, variable="cs"):
    return refresh_task_enum_values(tmpl.id, EnumRefreshIn(variable=variable), db, user, ip=None)


def _resave(db, admin, tmpl, ds, *, sql=SQL, enum_sql=ENUM_SQL, params=None, samples=None):
    """重存一版(等价于作者在编辑器里改完点保存)。params=[] 表示把变量全删掉。"""
    if params is None:
        params = [ParamDef(name="cs", kind="list", enum_sql=enum_sql)]
    return update_template(
        tmpl.id,
        TemplateUpdateIn(sql_text=sql, datasource_id=ds.id, params=params, enum_samples=samples),
        db, admin, ip=None,
    )


def _grant(db, admin, tmpl, user, actions=("view", "run")):
    permission_service.grant(
        db, subject_type="user", subject_id=str(user.id), resource_type="template",
        resource_id=str(tmpl.id), actions=list(actions), granted_by=admin.id,
    )


def _count_rows(db, template_id) -> int:
    return db.scalar(
        select(func.count()).select_from(TemplateEnumValues).where(
            TemplateEnumValues.template_id == template_id
        )
    )


# ---------------------------------------------------------------- 读:纯缓存,不打库


def test_read_never_executes_sql(db, admin, ds, team, monkeypatch):
    """核心保证:业务侧读候选值绝不触发取数 —— 这正是「不再一次性消费」的意义。"""
    tmpl = _make_published(
        db, admin, ds, team, name="读缓存不打库",
        samples={"cs": EnumSampleIn(values=["a", "b"], source_sql=ENUM_SQL)},
    )

    def boom(*a, **kw):
        raise AssertionError("读共享候选时不应执行任何 SQL")

    monkeypatch.setattr(enum_cache_service, "run_value_query", boom)
    out = _read(db, admin, tmpl)
    assert out.cached is True and out.values == ["a", "b"]


def test_author_sample_becomes_business_options(db, admin, ds, team):
    """作者测出来的候选,业务用户打开就能直接勾选。"""
    tmpl = _make_published(
        db, admin, ds, team, name="作者测试即共享",
        samples={"cs": EnumSampleIn(values=["x", "y"], source_sql=ENUM_SQL, duration_ms=88)},
    )
    biz = _biz_user(db, 9010, "业务甲")
    _grant(db, admin, tmpl, biz)
    out = _read(db, biz, tmpl)
    assert out.values == ["x", "y"]
    assert out.cached is True and out.stale is False
    assert out.updated_by == admin.id and out.updated_by_name == "枚举管理员"
    assert out.duration_ms == 88 and out.updated_at is not None


def test_sample_dropped_when_source_sql_mismatches(db, admin, ds, team):
    """作者测完又改了 enum_sql 才保存:那批值对不上新 SQL,必须在持久化边界丢掉。"""
    tmpl = _make_published(
        db, admin, ds, team, name="测完改了SQL",
        samples={"cs": EnumSampleIn(values=["旧"], source_sql="SELECT 早先的 SQL")},
    )
    out = _read(db, admin, tmpl)
    assert out.cached is False and out.values == []
    assert _count_rows(db, tmpl.id) == 0


def test_sample_absent_does_not_clear_existing_cache(db, admin, ds, team, monkeypatch):
    """编辑器每次开窗都清测试结果,所以「只改任务名」发来的是空 —— 不能当成清空。"""
    tmpl = _make_published(db, admin, ds, team, name="改名不清缓存")
    _fake_query(monkeypatch, ["p", "q"])
    _refresh(db, admin, tmpl)

    _resave(db, admin, tmpl, ds)
    out = _read(db, admin, tmpl)
    assert out.cached is True and out.values == ["p", "q"]


# ---------------------------------------------------------------- 更新:共享 + 幂等


def test_refresh_is_shared_between_users(db, admin, ds, team, monkeypatch):
    """甲点更新,乙看到的就是甲那次的结果 —— 共享的意义。"""
    tmpl = _make_published(db, admin, ds, team, name="更新即共享")
    a = _biz_user(db, 9020, "业务A")
    b = _biz_user(db, 9021, "业务B")
    for u in (a, b):
        _grant(db, admin, tmpl, u)
    _fake_query(monkeypatch, ["m", "n"])
    _refresh(db, a, tmpl)

    out = _read(db, b, tmpl)
    assert out.values == ["m", "n"]
    assert out.updated_by == a.id and out.updated_by_name == "业务A"


def test_refresh_replaces_and_keeps_single_row(db, admin, ds, team, monkeypatch):
    """反复更新是覆盖写,不是追加 —— 一个变量恒定一行。"""
    tmpl = _make_published(db, admin, ds, team, name="覆盖写")
    _fake_query(monkeypatch, ["第一次"])
    _refresh(db, admin, tmpl)
    # 绕过 30 秒节流,直接验证覆盖语义
    monkeypatch.setattr(settings, "ENUM_REFRESH_MIN_INTERVAL_SECONDS", 0)
    _fake_query(monkeypatch, ["第二次"])
    out = _refresh(db, admin, tmpl)

    assert out.values == ["第二次"]
    assert _count_rows(db, tmpl.id) == 1


def test_upsert_is_idempotent_across_sessions(db, admin, ds, team):
    """并发插入撞唯一约束时要重查改写,不能把 IntegrityError 抛给用户。"""
    tmpl = _make_published(db, admin, ds, team, name="并发写")
    other = SessionLocal()
    try:
        other.add(
            TemplateEnumValues(
                template_id=tmpl.id, variable="cs", enum_values=["别人先写的"],
                truncated=False, datasource_id=ds.id,
                enum_sql_hash=enum_cache_service.fingerprint(ENUM_SQL), updated_by=admin.id,
            )
        )
        other.commit()
    finally:
        other.close()

    enum_cache_service.upsert(
        db, template_id=tmpl.id, variable="cs", datasource_id=ds.id, enum_sql=ENUM_SQL,
        result=ValueListOut(values=["我后写的"], truncated=False, duration_ms=1),
        user_id=admin.id,
    )
    db.commit()
    assert _count_rows(db, tmpl.id) == 1
    assert _read(db, admin, tmpl).values == ["我后写的"]


def test_refresh_throttled_within_window(db, admin, ds, team, monkeypatch):
    """双击 / 多人同点:30 秒内直接复用,不再浪费一次线上查询。"""
    tmpl = _make_published(db, admin, ds, team, name="节流")
    calls = _fake_query(monkeypatch, ["只该跑一次"])
    _refresh(db, admin, tmpl)
    out = _refresh(db, admin, tmpl)

    assert calls["n"] == 1
    assert out.reused is True and out.values == ["只该跑一次"]


# ---------------------------------------------------------------- 过期与剪枝


def test_stale_when_enum_sql_changed(db, admin, ds, team, monkeypatch):
    """作者改了 enum_sql 又没重测:旧候选一律不展示(按产品决策),只给 stale 让前端解释。"""
    tmpl = _make_published(db, admin, ds, team, name="SQL变了")
    _fake_query(monkeypatch, ["旧值"])
    _refresh(db, admin, tmpl)

    _resave(db, admin, tmpl, ds, enum_sql="SELECT 换了一段 SQL")
    out = _read(db, admin, tmpl)
    assert out.stale is True and out.cached is False and out.values == []


def test_stale_when_datasource_changed(db, admin, ds, ds2, team, monkeypatch):
    """同一段 SQL 换个数据源结果就不一样,同样算过期。"""
    tmpl = _make_published(db, admin, ds, team, name="数据源变了")
    _fake_query(monkeypatch, ["库一的值"])
    _refresh(db, admin, tmpl)

    _resave(db, admin, tmpl, ds2)
    out = _read(db, admin, tmpl)
    assert out.stale is True and out.values == []


def test_cache_survives_archive_and_restore(db, admin, ds, team, monkeypatch):
    """下线进回收站再恢复,共享候选要还在 —— 否则每次上下线都白费一次取数。"""
    from app.api.routes.templates import archive

    tmpl = _make_published(db, admin, ds, team, name="上下线")
    _fake_query(monkeypatch, ["还在"])
    _refresh(db, admin, tmpl)

    archive(tmpl.id, db, admin, ip=None)
    publish(tmpl.id, PublishIn(note="恢复"), db, admin, ip=None)
    out = _read(db, admin, tmpl)
    assert out.cached is True and out.stale is False and out.values == ["还在"]


def test_prune_drops_cache_for_removed_and_delisted_variables(db, admin, ds, team, monkeypatch):
    """变量没了、或不再是「配了枚举 SQL 的值列表」,缓存行必须清掉,别留下读不到的业务数据。"""
    tmpl = _make_published(db, admin, ds, team, name="剪枝-删变量")
    _fake_query(monkeypatch, ["v"])
    _refresh(db, admin, tmpl)
    _resave(db, admin, tmpl, ds, sql="SELECT 1", params=[])
    assert _count_rows(db, tmpl.id) == 0

    # IN (:cs) → = :cs:_normalize_params 会把 enum_sql 清掉,名字却还在
    t2 = _make_published(db, admin, ds, team, name="剪枝-降级为单值")
    _fake_query(monkeypatch, ["v"])
    _refresh(db, admin, t2)
    _resave(db, admin, t2, ds, sql="SELECT * FROM o WHERE c = :cs")
    assert _count_rows(db, t2.id) == 0


# ---------------------------------------------------------------- 上限与权限


def test_values_capped_on_seed(db, admin, ds, team):
    """手搓 payload 也不能把元数据库撑爆:落库前再截一次到 1000。"""
    tmpl = _make_published(
        db, admin, ds, team, name="截断",
        samples={
            "cs": EnumSampleIn(
                values=[str(i) for i in range(1500)], source_sql=ENUM_SQL
            )
        },
    )
    out = _read(db, admin, tmpl)
    assert len(out.values) == settings.ENUM_VALUE_CAP
    assert out.truncated is True


def test_refresh_requires_permission(db, admin, ds, team):
    tmpl = _make_published(db, admin, ds, team, name="无权更新")
    outsider = _biz_user(db, 9030, "路人")
    with pytest.raises(PermissionDeniedError):
        _refresh(db, outsider, tmpl)


def test_refresh_rejects_variable_without_enum_sql(db, admin, ds, team):
    tmpl = _make_published(db, admin, ds, team, name="没配枚举SQL", enum_sql=None)
    with pytest.raises(RubicError):
        _refresh(db, admin, tmpl)

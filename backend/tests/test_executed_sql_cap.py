"""护栏:`executed_sql` 落库前必须按**字节**截断。

守的坑:业务方用「上传/粘贴列表」粘一批 ID 时,params_service.render_sql 渲染出来的
最终 SQL 会撑破 query_jobs.executed_sql 那个 Text 列(MySQL 上限 65535 字节)。
严格模式下 MySQL 抛 1406,而那次 commit 发生在**把 SQL 发给目标库之前** —— 于是整次取数
在还没查数时就失败,报错(「服务器内部错误(DataError)」)与 SQL 本身毫无关系。

实测:5000 个 10 位 ID → 70,055 字节,而前端 LIST_CAP 就是 5000。
SQLite 没有列长度限制,所以这个坑**只在线上出现**,必须靠这条用例守住。
"""
import pytest

from app.api.routes.templates import create_template, publish
from app.models.query_job import EXECUTED_SQL_MAX_BYTES, JOB_SUCCESS
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER
from app.schemas.common import ParamDef
from app.schemas.template import PublishIn, TemplateCreateIn, TestRunIn
from app.services import query_service, template_service

pytestmark = pytest.mark.usefixtures("clean_credentials")

# ID 段 9100–9101
ADMIN, AUTHOR = 9100, 9101

SQL = "SELECT c FROM o WHERE uid IN (:uids) AND d = :d"
# 5000 个 10 位 ID:渲染后约 70KB,超过 MySQL Text 的 65535 字节
BIG_IDS = [str(1_000_000_000 + i) for i in range(5000)]


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("cap-mysql")


@pytest.fixture
def admin(user_factory):
    return user_factory(ADMIN, ROLE_ADMIN, "平台管理员", prefix="cap")


@pytest.fixture
def author(user_factory):
    return user_factory(AUTHOR, ROLE_DEVELOPER, "作者", prefix="cap")


@pytest.fixture
def team(team_factory, author):
    return team_factory("cap-team", [(author, True)])


@pytest.fixture
def tmpl(db, ds, team, author, team_credential):
    team_credential(team, ds, username="cap_acct")
    t = create_template(
        TemplateCreateIn(
            name="cap-粘一大批ID的任务", team_id=team.id, datasource_id=ds.id, sql_text=SQL,
            params=[
                ParamDef(name="uids", kind="list", label="用户ID"),
                ParamDef(name="d", kind="single", label="日期"),
            ],
        ),
        db, author, ip=None,
    )
    publish(t.id, PublishIn(note="上线"), db, author, ip=None)
    return t


def test_rendered_sql_really_would_overflow(db):
    """先证明这个坑是真的:不截断的话渲染结果确实超过 MySQL Text 上限。"""
    from app.services import params_service

    sql2, bound = params_service.expand_list_params(SQL, {"uids": BIG_IDS, "d": "2026-08-01"})
    raw = params_service.render_sql(sql2, bound)
    assert len(raw.encode("utf-8")) > 65535, "样本不够大,这条护栏就守不到东西"


def test_run_with_huge_list_stores_clipped_sql(db, tmpl, author, spy_connector, monkeypatch):
    """正式取数:executed_sql 落库前被截断,任务照常跑完。"""
    spy_connector()
    monkeypatch.setattr(query_service.settings, "RUN_INLINE", True)

    job = query_service.enqueue(db, author, tmpl.id, {"uids": BIG_IDS, "d": "2026-08-01"})

    assert job.status == JOB_SUCCESS, f"不该失败:{job.error}"
    assert len(job.executed_sql.encode("utf-8")) <= EXECUTED_SQL_MAX_BYTES
    assert job.executed_sql.endswith(")"), "截断标记应当收尾,便于看的人知道这不是全文"
    assert "已截断" in job.executed_sql


def test_test_run_with_huge_list_stores_clipped_sql(db, tmpl, ds, team, author, spy_connector):
    """编辑器试跑走的是另一条写入路径,同样要截断。"""
    spy_connector()
    template_service.test_run(
        db,
        TestRunIn(
            team_id=team.id, datasource_id=ds.id, sql_text=SQL, template_id=tmpl.id,
            params=[
                ParamDef(name="uids", kind="list", label="用户ID"),
                ParamDef(name="d", kind="single", label="日期"),
            ],
            values={"uids": BIG_IDS, "d": "2026-08-01"},
        ),
        author,
    )
    from sqlalchemy import select

    from app.models.query_job import SOURCE_TEST, QueryJob

    job = db.scalars(
        select(QueryJob)
        .where(QueryJob.template_id == tmpl.id, QueryJob.source == SOURCE_TEST)
        .order_by(QueryJob.id.desc())
    ).first()
    assert job is not None
    assert len(job.executed_sql.encode("utf-8")) <= EXECUTED_SQL_MAX_BYTES


def test_short_sql_is_left_alone(db, tmpl, author, spy_connector, monkeypatch):
    """没超限的不许动:截断只在真的会撑破列时发生。"""
    spy_connector()
    monkeypatch.setattr(query_service.settings, "RUN_INLINE", True)
    job = query_service.enqueue(db, author, tmpl.id, {"uids": ["1", "2"], "d": "2026-08-01"})
    assert job.executed_sql == "SELECT c FROM o WHERE uid IN ('1', '2') AND d = '2026-08-01'"

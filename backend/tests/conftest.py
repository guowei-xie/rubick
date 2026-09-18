"""pytest 全局夹具:用本地临时 SQLite 承载平台元数据库,绝不触碰线上库。

必须在任何 app.* 导入之前设置 CONFIG_FILE(settings 在导入时即缓存)。而「必须」是一句
约定,不是保证 —— 任何一个先于本文件执行的 app.* 导入都会让 settings 缓存成真实
config.ini,于是整套测试(含 create_all 与各种写入)直接落到那个库上。所以下面还钉了一道
硬闸:引擎不是**系统临时目录里的 sqlite**就当场终止整个测试会话。

这道闸尤其挡一种「配置全对」的翻车:在**线上机器**上跑 pytest。那里的 config.ini 指向线上库,
且按部署要求打开了 ALLOW_REMOTE_DB —— 应用层护栏会放行(它本该放行),只有这道闸拦得住。
"""
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="rubick-test-")
_CFG = Path(_TMP) / "config.ini"
_CFG.write_text(
    "[rubick]\n"
    f"DATABASE_URL = sqlite:///{_TMP}/test.db\n"
    "JWT_SECRET = test-secret\n"
    "MOCK_AUTH = false\n"
    # 数据产物也进临时目录:订阅/下载类用例会真的落 CSV,不许写进仓库的 backend/data
    f"DATA_DIR = {_TMP}\n",
    encoding="utf-8",
)
os.environ["CONFIG_FILE"] = str(_CFG)

import pytest  # noqa: E402
from sqlalchemy import func, select, update  # noqa: E402

import app.models  # noqa: F401,E402  注册所有模型
from app.connectors.base import DataSourceConnector  # noqa: E402
from app.core.database import Base, SessionLocal, engine  # noqa: E402


def _refuse_foreign_test_db() -> None:
    """测试库必须是系统临时目录里的 sqlite,否则终止整个会话。

    只报错不够 —— 必须**在任何用例跑起来之前**停下:第一个动作 create_all 就已经在
    往那个库里建表了。pytest.exit 在 conftest 导入期即中止收集,是唯一足够早的出口。

    判据用「系统临时目录」而不是本模块这次的 _TMP:一次会话里 conftest 可能被重复导入
    (有用例会 reload app.* / 清 sys.modules),每次都新建一个 _TMP,而 engine 早已绑定在
    最先那个上 —— 拿 _TMP 比对会把正常运行判成违规。
    """
    url = engine.url
    if url.get_backend_name() != "sqlite" or not str(url.database or "").startswith(
        tempfile.gettempdir()
    ):
        pytest.exit(
            "测试库不是临时 sqlite,已终止:"
            f"engine={url.render_as_string(hide_password=True)}。"
            "测试一律用 sqlite 本地库,绝不连线上 —— 多半是某个 app.* 导入早于 "
            "tests/conftest.py(settings 因此缓存成了真实 config.ini),"
            "或是在线上机器上直接跑了 pytest。",
            returncode=2,
        )


_refuse_foreign_test_db()
from app.models.audit import AuditLog  # noqa: E402
from app.models.credential import TeamDataSourceCredential
from app.models.notification import Notification  # noqa: E402
from app.models.datasource import DataSource  # noqa: E402
from app.models.team import Team, TeamMember  # noqa: E402
from app.models.template import STATUS_PUBLISHED, SqlTemplate  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import (  # noqa: E402
    credential_service,
    enum_cache_service,
    query_service,
    team_service,
    template_service,
)


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


# ---- 团队与团队取数账号(多个测试文件共用)----
#
# 刻意**没有** enforced 之类的开关夹具:团队取数账号是唯一路径,过渡开关
# REQUIRE_OWNER_CREDENTIAL 已删除。留一个「测试里能关掉强制」的夹具就是留一条暗门。


@pytest.fixture
def team_factory(db):
    """get-or-create 团队 + 成员。members=[(user, is_team_admin), ...]

    按团队名 get-or-create(Team.name 唯一,同各文件的 ds 夹具写法);没有按用例清库,
    故必须幂等。直接落 TeamMember 行而不走 team_service.add_member:后者只收开发者角色,
    而测试里也要把管理员/普通用户放进团队来验证边界。
    """

    def make(name: str, members=()):
        team = db.scalar(select(Team).where(Team.name == name))
        if team is None:
            team = Team(name=name, description=f"测试团队 {name}")
            db.add(team)
            db.commit()
            db.refresh(team)
        for user, is_admin in members:
            row = db.scalar(
                select(TeamMember).where(
                    TeamMember.team_id == team.id, TeamMember.user_id == user.id
                )
            )
            if row is None:
                db.add(TeamMember(team_id=team.id, user_id=user.id, is_team_admin=is_admin))
            else:
                row.is_team_admin = is_admin
        db.commit()
        return team

    return make


@pytest.fixture
def team_credential(db):
    """给 (团队 × 数据源) 登记一套取数账号,并顺手标记为已测通。

    上线卡点(credential_service.require_ready)要求团队**登记过**账号,所以任何走到 publish
    的用例都需要它。测通与否不影响任何拦截(测试连接是非必选项),这里仍写上 last_verified_at
    是为了让用例的前置状态与真实环境里「配完顺手验一下」的常态一致 —— 「没验过也能跑」由
    test_unverified_credential_is_usable 专门覆盖。
    """

    def make(team, ds, *, username: str = "team_acct", password: str | None = "team-pw"):
        cred, _ = credential_service.upsert(
            db, team_id=team.id, datasource_id=ds.id,
            username=username, password=password, updated_by=None,
        )
        cred.last_verified_at = datetime.now()
        cred.last_verify_error = None
        db.commit()
        db.refresh(cred)
        return cred

    return make


# ---- 通用夹具:用户 / 数据源 / 审计断言 ----
# 库不按用例清理,故一律 get-or-create 且主键显式(BigInteger 主键在 SQLite 下不自增)。


@pytest.fixture
def user_factory(db):
    """按固定主键 get-or-create 一个用户。

    每个测试文件各占一段 id(见各文件顶部的常量),prefix 只为让 feishu_open_id 不撞车。
    """

    def make(uid: int, role: str, name: str, *, prefix: str = "t", logged_in: bool = True):
        u = db.get(User, uid)
        if u is None:
            u = User(
                id=uid, feishu_open_id=f"ou_{prefix}_{uid}", name=name, role=role,
                last_login_at=datetime.now() if logged_in else None,
            )
            db.add(u)
            db.commit()
        return u

    return make


@pytest.fixture
def system_user(db):
    """订阅定时运行的系统用户。生产由 migrate._ensure_system_scheduler_user 创建,
    测试库不跑 migrate,这里按同一哨兵 open_id get-or-create(id 取 9990,避开各文件 ID 段)。"""
    from app.models.user import ROLE_USER, SYSTEM_SCHEDULER_NAME, SYSTEM_SCHEDULER_OPEN_ID

    u = db.scalar(select(User).where(User.feishu_open_id == SYSTEM_SCHEDULER_OPEN_ID))
    if u is None:
        u = User(
            id=9990, feishu_open_id=SYSTEM_SCHEDULER_OPEN_ID,
            name=SYSTEM_SCHEDULER_NAME, role=ROLE_USER, is_active=False,
        )
        db.add(u)
        db.commit()
    return u


@pytest.fixture
def subscribed_task_factory(db):
    """建一个带订阅计划的无参数任务(默认每天 09:00、已上线),可顺手挂订阅者。

    订阅类测试四个文件都要这套「create_template(subscription=…) → publish → subscribe」
    脚手架,构造方式只写这一份 —— SubscriptionScheduleIn 签名一变只改这里。
    """
    from app.schemas.template import SubscriptionScheduleIn, TemplateCreateIn
    from app.services import subscription_service, template_service

    def make(
        author, ds, team, name, *,
        enabled: bool = True, freq: str = "daily", days=(), at_time: str = "09:00",
        publish: bool = True, subscribers=(),
    ):
        tmpl = template_service.create_template(
            db, author,
            TemplateCreateIn(
                name=name, team_id=team.id, datasource_id=ds.id, sql_text="SELECT 1",
                subscription=SubscriptionScheduleIn(
                    enabled=enabled, freq=freq, days=list(days), at_time=at_time
                ),
            ),
        )
        if publish:
            template_service.publish(db, tmpl, author, None)
        for u in subscribers:
            subscription_service.subscribe(db, tmpl, u)
        return tmpl

    return make


@pytest.fixture
def datasource_factory(db):
    """按名字 get-or-create 一个数据源。

    公共账号默认叫 public_acct,与 assert_never_public 的默认值同源 —— 「取数不许回退到
    公共账号」这条断言才对得上。
    """

    def make(name: str, **overrides):
        d = db.scalar(select(DataSource).where(DataSource.name == name))
        if d is None:
            d = DataSource(
                **{
                    "name": name, "engine": "mysql", "host": "localhost", "port": 3306,
                    "database": "demo", "username": "public_acct", "password": "public_pw",
                    "extra": {},
                    **overrides,
                }
            )
            db.add(d)
            db.commit()
        return d

    return make


def max_audit_id(db) -> int:
    return db.scalar(select(func.max(AuditLog.id))) or 0


def new_audit_rows(db, since_id: int) -> list[AuditLog]:
    db.expire_all()
    return list(db.scalars(select(AuditLog).where(AuditLog.id > since_id).order_by(AuditLog.id)))


def latest_audit(db, action: str) -> AuditLog:
    """某个动作最新的那条审计行。「取数写了什么审计」几个文件都要问,查法只写一遍。"""
    db.expire_all()
    return db.scalars(
        select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id.desc()).limit(1)
    ).first()


def one_audit_row(db, since_id: int) -> AuditLog:
    """恰好一条新审计行。一次写操作发 N 条会让治理阅读变差,故这条断言本身就是规格。"""
    rows = new_audit_rows(db, since_id)
    assert len(rows) == 1, f"期望恰好 1 条审计记录,实际 {len(rows)} 条:{[r.action for r in rows]}"
    return rows[0]


def note_floor(db) -> int:
    """通知水位线。与 max_audit_id 同一套用法:先记水位,做事,再问「新发了哪些」。
    库不按用例清理,所以「本次发了什么」只能这么问 —— 查法在这里写一遍,不在各文件重述。"""
    return db.scalar(select(func.max(Notification.id))) or 0


def new_notifications(db, floor: int) -> list[Notification]:
    db.expire_all()
    return list(db.scalars(select(Notification).where(Notification.id > floor)))


# HiveServer2 开了鉴权时的报错原文:消息只在 infoMessages 里,errorMessage 是空的
HIVE_PERM_DENIED = (
    "*org.apache.hive.service.cli.HiveSQLException:Error while compiling statement: "
    "FAILED: HiveAccessControlException Permission denied: user [team_acct] does not "
    "have [USE] privilege on [business_analysis]:28:27"
)


def hive_thrift_error(*, error_message=None, info_messages=()):
    """造一个与 pyhive 真实抛出等价的异常:args[0] 是带 TStatus 的响应对象。

    放在 conftest 而不是各测试文件里:「pyhive 的异常长什么样」这件事只该写一遍,
    否则驱动升级要改好几处(见 connectors/hive.py::_hive_error_text 的说明)。
    """
    from pyhive.exc import OperationalError
    from TCLIService.ttypes import TExecuteStatementResp, TStatus

    status = TStatus(
        statusCode=3, errorMessage=error_message, infoMessages=list(info_messages)
    )
    return OperationalError(TExecuteStatementResp(status=status))


def assert_never_public(seen, public_username: str = "public_acct") -> None:
    """断言这批取数身份里没有数据源公共账号。

    专门为「开关删除后的回归」而存在:静默回退到能看全库的公共账号是最危险的回归形态,
    而它不会让任何断言变红 —— 除非显式检查。每条取数链路的用例都调一次。
    """
    names = [c.username for c in seen]
    assert public_username not in names, f"取数用到了数据源公共账号:{names}"


@pytest.fixture
def spy_connector(monkeypatch):
    """替掉**所有**取数链路的连接器,记录每次拿到的取数身份(Credential)。

    返回一个安装函数,可在同一个用例里重复调用以切换行为(如先成功再失败)。
    各模块都是 `from app.connectors import get_connector` 的 module-level 名字,
    故逐个 patch。集中一处:`get_connector` 签名再变时只改这里。
    """

    def install(
        *, rows=((1,),), fail: str | None = None, databases: list[str] | None = None,
        calls: list | None = None,
    ) -> list:
        seen: list = []
        dbs = list(databases or [])  # 局部别名:类体里同名赋值会遮住外层参数

        class FakeConnector(DataSourceConnector):
            """只假掉「怎么取到行」这一层。

            **刻意继承真基类**:execute()(试跑 / 枚举值走它)、行数截断、duration
            于是都是应用真正跑的那份代码 —— 自己抄一遍的下场是护栏测的是假的。
            """

            def __init__(self):
                super().__init__(config=None)

            @contextmanager
            def stream(self, sql, params=None, *, timeout_seconds):
                if fail:
                    raise RuntimeError(fail)
                yield ["c"], iter([tuple(r) for r in rows])

            def execute(self, sql, params=None, *, timeout_seconds, max_rows):
                if calls is not None:  # 想断言「传下来的是什么」的用例传个列表进来
                    calls.append({"timeout_seconds": timeout_seconds, "max_rows": max_rows})
                return super().execute(
                    sql, params, timeout_seconds=timeout_seconds, max_rows=max_rows
                )

            def test_connection(self):
                if fail:
                    raise RuntimeError(fail)
                # 真连接器返回「这个账号能访问的库」(见 connectors/base.py 的契约)
                return dbs

        def fake_get_connector(ds, credential):
            seen.append(credential)
            return FakeConnector()

        for mod in (credential_service, query_service, template_service, enum_cache_service):
            monkeypatch.setattr(mod, "get_connector", fake_get_connector)
        return seen

    return install


@pytest.fixture
def clean_credentials(db):
    """每个用例从「无凭证」起步,免得用例之间通过库里的残留互相影响。"""
    db.query(TeamDataSourceCredential).delete()
    db.commit()
    yield


@pytest.fixture
def template_factory(db):
    """get-or-create 一张任务。运营分析的四个板块测试共用。

    **按名字 get-or-create**,同 team_factory:这些用例之间不清 sql_templates
    (clean_jobs 只清运行记录),同名再建会撞唯一约束,而各文件的名字本来就带自己的前缀。

    created_at 建完再 UPDATE 回去:它由 TimestampMixin 的 server_default 生成,
    构造函数里传不进去 —— 而闲置口径要的正是一个「很久以前建的」任务。
    """

    def make(author, ds, team, name, *, status=STATUS_PUBLISHED, created_at=None):
        t = db.scalar(select(SqlTemplate).where(SqlTemplate.name == name))
        if t is None:
            t = SqlTemplate(
                name=name, datasource_id=ds.id, dialect="mysql", status=status,
                author_id=author.id, team_id=team.id,
            )
            db.add(t)
            db.commit()
            if created_at is not None:
                db.execute(
                    update(SqlTemplate).where(SqlTemplate.id == t.id)
                    .values(created_at=created_at)
                )
                db.commit()
            db.refresh(t)
        return t

    return make


@pytest.fixture
def job_factory(db):
    """直接造一条运行记录,**不走 query_service.enqueue**。

    运营分析的用例要的是「三个月前那天跑了 5 次、其中 2 次超时」这类历史分布,而 enqueue
    只会在当下造 queued 行,还要带齐权限/凭证/连接器的一整套前置。这里直接落行,
    把 created_at / started_at 显式写进去(绕开 server_default 与 onupdate)。

    **只在分析类用例里用**:它刻意跳过了所有前置校验,拿它去验取数链路等于把护栏测成假的。
    """
    from app.models.query_job import JOB_SUCCESS, SOURCE_RUN, QueryJob

    def make(
        *, user, template, datasource, status=JOB_SUCCESS, source=SOURCE_RUN,
        created_at=None, started_at=None, duration_ms=None, row_count=None,
        error=None, run_as_team_id=None,
    ):
        job = QueryJob(
            user_id=user.id, template_id=template.id, datasource_id=datasource.id,
            params={}, status=status, source=source, duration_ms=duration_ms,
            row_count=row_count, error=error, run_as_team_id=run_as_team_id,
        )
        db.add(job)
        db.commit()
        # created_at 有 server_default、updated_at 有 onupdate —— 都得显式 UPDATE 才压得住
        values = {}
        if created_at is not None:
            values["created_at"] = created_at
        if started_at is not None:
            values["started_at"] = started_at
        if values:
            from sqlalchemy import update as _update

            db.execute(_update(QueryJob).where(QueryJob.id == job.id).values(**values))
            db.commit()
        db.refresh(job)
        return job

    return make


@pytest.fixture
def clean_jobs(db):
    """每个用例从「零运行记录」起步 —— 运营分析类用例专用。

    其余测试靠各文件独占的 ID 段互不干扰,但运行记录没有稳定主键可占:聚合是按团队/时间窗
    横扫的,上一个用例留下的行会原封不动地落进下一个用例的窗口里,于是断言「跑了 1 次」
    变成「跑了 4 次」。与 clean_credentials 同一取舍 —— 聚合类断言需要一个确定的空盘。
    """
    from app.models.audit import DownloadEvent
    from app.models.query_job import QueryJob

    db.query(DownloadEvent).delete()
    db.query(QueryJob).delete()
    db.commit()
    yield

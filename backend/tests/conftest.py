"""pytest 全局夹具:用本地临时 SQLite 承载平台元数据库,绝不触碰线上库。

必须在任何 app.* 导入之前设置 CONFIG_FILE(settings 在导入时即缓存)。
"""
import os
import tempfile
from datetime import datetime
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="rubick-test-")
_CFG = Path(_TMP) / "config.ini"
_CFG.write_text(
    "[rubick]\n"
    f"DATABASE_URL = sqlite:///{_TMP}/test.db\n"
    "JWT_SECRET = test-secret\n"
    "MOCK_AUTH = false\n",
    encoding="utf-8",
)
os.environ["CONFIG_FILE"] = str(_CFG)

import pytest  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

import app.models  # noqa: F401,E402  注册所有模型
from app.connectors.base import QueryResult  # noqa: E402
from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.models.audit import AuditLog  # noqa: E402
from app.models.credential import TeamDataSourceCredential  # noqa: E402
from app.models.datasource import DataSource  # noqa: E402
from app.models.team import Team, TeamMember  # noqa: E402
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


def one_audit_row(db, since_id: int) -> AuditLog:
    """恰好一条新审计行。一次写操作发 N 条会让治理阅读变差,故这条断言本身就是规格。"""
    rows = new_audit_rows(db, since_id)
    assert len(rows) == 1, f"期望恰好 1 条审计记录,实际 {len(rows)} 条:{[r.action for r in rows]}"
    return rows[0]


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
        *, rows=((1,),), fail: str | None = None, bypassed_database: str | None = None
    ) -> list:
        seen: list = []
        bypassed = bypassed_database  # 局部别名:类体里同名赋值会遮住外层参数

        class FakeConnector:
            # 真连接器在「配的默认库进不去、已绕开」时置上库名(见 connectors/base.py)
            bypassed_database = bypassed

            def execute(self, sql, params=None, *, timeout_seconds, max_rows):
                if fail:
                    raise RuntimeError(fail)
                return QueryResult(
                    columns=["c"], rows=[tuple(r) for r in rows], meta={"duration_ms": 1}
                )

            def test_connection(self):
                if fail:
                    raise RuntimeError(fail)

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

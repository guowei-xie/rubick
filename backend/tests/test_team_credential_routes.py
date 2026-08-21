"""团队取数账号的接口层:权限边界、库用户名的可见性分级、审计留痕、密码永不外泄。

与仓库既有测试同风格:直接调路由函数,不起 TestClient。
"""
import json

import pytest
from sqlalchemy import func, select

from app.api.routes.credentials import (
    credentials_overview,
    delete_team_credential,
    list_team_credentials,
    my_teams_credentials,
    upsert_team_credential,
    verify_team_credential,
)
from app.api.routes.datasources import delete_datasource
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.models import audit as A
from app.models.datasource import DataSource
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER
from app.schemas.credential import CredentialIn
from app.services import credential_service
from tests.conftest import max_audit_id, new_audit_rows, one_audit_row

pytestmark = pytest.mark.usefixtures("clean_credentials")

SECRET = "s3cret-route-pw"
ACCT = "route_team_acct"

# ID 段 8650–8654
ADMIN, T_ADMIN, MEMBER, OUTSIDER = 8650, 8651, 8652, 8653





@pytest.fixture
def ds(db, datasource_factory):
    return datasource_factory("tcroute-mysql")


@pytest.fixture
def admin(db, user_factory):
    return user_factory(ADMIN, ROLE_ADMIN, "平台管理员", prefix="tcr")


@pytest.fixture
def t_admin(db, user_factory):
    return user_factory(T_ADMIN, ROLE_DEVELOPER, "团队管理员", prefix="tcr")


@pytest.fixture
def member(db, user_factory):
    return user_factory(MEMBER, ROLE_DEVELOPER, "团队成员", prefix="tcr")


@pytest.fixture
def outsider(db, user_factory):
    return user_factory(OUTSIDER, ROLE_DEVELOPER, "外队开发者", prefix="tcr")




@pytest.fixture
def team(db, t_admin, member, team_factory):
    return team_factory("tcroute-team", [(t_admin, True), (member, False)])






def _blob(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _configure(db, team, ds, actor):
    return upsert_team_credential(
        team.id, ds.id, CredentialIn(username=ACCT, password=SECRET), db, actor, ip=None
    )


# ---------------------------------------------------------------- 密码永不外泄


def test_no_endpoint_ever_returns_the_password(
    db, ds, team, t_admin, admin, spy_connector
):
    """五个端点各自序列化后断言密文与 "password" 字段都不出现 —— 铁律的回归网。"""
    spy_connector()
    outs = [
        _configure(db, team, ds, t_admin),
        list_team_credentials(team.id, db, t_admin),
        verify_team_credential(team.id, ds.id, db, t_admin, ip=None),
        my_teams_credentials(db, t_admin),
        credentials_overview(db, admin),
    ]
    for out in outs:
        blob = _blob(out)
        assert SECRET not in blob
        assert "password" not in blob


# ---------------------------------------------------------------- 库用户名的可见性分级


def test_username_is_visible_only_to_team_admin_and_platform_admin(
    db, ds, team, t_admin, member, admin
):
    """库用户名是半机密:Hive auth=NONE 下它本身就是完整凭证,拿到就能绕过平台直连。

    故只对该团队的团队管理员与平台管理员可见;普通成员只看三态。
    """
    _configure(db, team, ds, t_admin)

    as_team_admin = next(
        r for r in list_team_credentials(team.id, db, t_admin) if r["datasource_id"] == ds.id
    )
    assert as_team_admin["username"] == ACCT

    rows_as_member = list_team_credentials(team.id, db, member)
    as_member = next(r for r in rows_as_member if r["datasource_id"] == ds.id)
    assert as_member["username"] is None
    assert as_member["configured"] is True and as_member["verified"] is False
    assert ACCT not in _blob(rows_as_member), "整个响应里都不该出现库账号名"

    as_platform_admin = next(
        r for r in list_team_credentials(team.id, db, admin) if r["datasource_id"] == ds.id
    )
    assert as_platform_admin["username"] == ACCT


def test_my_teams_never_reveals_username(db, ds, team, t_admin, member):
    """编辑器走的这个端点面向全体团队成员,恒不含库用户名 —— 它只需要回答「就绪没」。"""
    _configure(db, team, ds, t_admin)
    for actor in (t_admin, member):
        rows = my_teams_credentials(db, actor)
        assert rows and all(r["username"] is None for r in rows)
        assert ACCT not in _blob(rows)


# ---------------------------------------------------------------- 权限边界


def test_only_team_admin_can_write(db, ds, team, member, outsider, t_admin, admin, spy_connector):
    spy_connector()
    for actor, why in ((member, "普通成员"), (outsider, "外队开发者")):
        with pytest.raises(PermissionDeniedError):
            upsert_team_credential(
                team.id, ds.id, CredentialIn(username="x", password="y"), db, actor, ip=None
            )
        with pytest.raises(PermissionDeniedError):
            verify_team_credential(team.id, ds.id, db, actor, ip=None)
        with pytest.raises(PermissionDeniedError):
            delete_team_credential(team.id, ds.id, db, actor, ip=None)

    # 团队管理员与平台管理员都放行(平台管理员不受团队约束,可代为配置/回收)
    _configure(db, team, ds, t_admin)
    verify_team_credential(team.id, ds.id, db, admin, ip=None)


def test_outsider_cannot_even_read(db, ds, team, outsider):
    with pytest.raises(PermissionDeniedError, match="不是团队"):
        list_team_credentials(team.id, db, outsider)


def test_member_can_read_status(db, ds, team, member, t_admin):
    _configure(db, team, ds, t_admin)
    rows = list_team_credentials(team.id, db, member)
    # 未配置的数据源也列出来:「还差哪个源」正是配置页要回答的问题
    assert len(rows) == db.scalar(select(func.count()).select_from(DataSource))


# ---------------------------------------------------------------- 审计


def test_upsert_is_audited_with_team_in_detail(db, ds, team, t_admin):
    since = max_audit_id(db)
    _configure(db, team, ds, t_admin)
    rows = new_audit_rows(db, since)
    assert [r.action for r in rows] == [A.ACTION_CREDENTIAL_UPSERT]
    row = rows[0]
    # 资源记数据源(治理按库筛),团队作为第二个维度放 detail
    assert row.resource_type == A.RESOURCE_CREDENTIAL and row.resource_id == str(ds.id)
    assert row.resource_name == ds.name
    assert row.detail["team_id"] == team.id and row.detail["team_name"] == team.name
    assert row.detail["db_username"] == ACCT
    assert row.detail["password_changed"] is True
    assert row.detail["by_platform_admin"] is False
    assert SECRET not in _blob(row.detail)


def test_platform_admin_intervention_is_marked(db, ds, team, admin):
    """平台管理员代为配置时要标出来:治理上要答得出「这是团队自己改的还是平台干预的」。"""
    since = max_audit_id(db)
    _configure(db, team, ds, admin)
    row = new_audit_rows(db, since)[0]
    assert row.detail["by_platform_admin"] is True


def test_verify_failure_is_also_audited(db, ds, team, t_admin, spy_connector):
    """测试失败也留痕:「一直测不通」本身就是要能查的事实,而且它是上线卡点的凭据。

    注意 verify 在**抛出之前**就把状态落库并 commit,路由的 finally 才读得到 cred.verified
    —— 这条行为必须被测住,否则审计会记成 ok=True。
    """
    spy_connector(fail="Access denied for user 'route_team_acct'")
    _configure(db, team, ds, t_admin)
    since = max_audit_id(db)
    from app.core.exceptions import RubicError

    with pytest.raises(RubicError):
        verify_team_credential(team.id, ds.id, db, t_admin, ip=None)
    row = new_audit_rows(db, since)[0]
    assert row.action == A.ACTION_CREDENTIAL_VERIFY
    assert row.detail["ok"] is False
    assert "Access denied" in row.detail["error"]


def test_verify_requires_existing_credential(db, ds, team, t_admin):
    with pytest.raises(NotFoundError, match="尚未配置"):
        verify_team_credential(team.id, ds.id, db, t_admin, ip=None)


def test_delete_missing_credential_leaves_no_misleading_audit(db, ds, team, t_admin):
    since = max_audit_id(db)
    out = delete_team_credential(team.id, ds.id, db, t_admin, ip=None)
    assert out == {"ok": True, "deleted": False}
    assert new_audit_rows(db, since) == [], "什么都没发生就不该留下一条「删除」"


def test_delete_is_audited(db, ds, team, t_admin):
    _configure(db, team, ds, t_admin)
    since = max_audit_id(db)
    out = delete_team_credential(team.id, ds.id, db, t_admin, ip=None)
    assert out["deleted"] is True
    row = new_audit_rows(db, since)[0]
    assert row.action == A.ACTION_CREDENTIAL_DELETE
    assert row.detail["db_username"] == ACCT


# ---------------------------------------------------------------- 总览与级联


def test_overview_has_no_enforced_and_lists_teams(db, ds, team, t_admin, admin):
    _configure(db, team, ds, t_admin)
    out = credentials_overview(db, admin)
    assert "enforced" not in out, "过渡开关已删除,这个字段不该再出现"
    hit = next(t for t in out["teams"] if t["team_id"] == team.id)
    # 平台管理员的治理矩阵要答「哪个库用的哪个账号」,故这里可见用户名
    cell = next(c for c in hit["credentials"] if c["datasource_id"] == ds.id)
    assert cell["username"] == ACCT
    assert [a["user_id"] for a in hit["admins"]] == [T_ADMIN]


def test_deleting_datasource_cascades_team_credentials(db, team, t_admin, admin, team_factory):
    """数据源被删时清掉其下所有团队凭证 —— 留着就是一堆指向不存在库的死行。"""
    from app.schemas.datasource import DataSourceIn
    from app.api.routes.datasources import create_datasource

    doomed = create_datasource(
        DataSourceIn(
            name="tcroute-待删数据源", engine="mysql", host="h", port=3306,
            database="d", username="u", password="p", extra={},
        ),
        db, admin, ip=None,
    )
    _configure(db, team, doomed, t_admin)
    since = max_audit_id(db)
    delete_datasource(doomed.id, db, admin, ip=None)
    row = new_audit_rows(db, since)[0]
    assert row.action == A.ACTION_DATASOURCE_DELETE
    assert row.detail["revoked_credentials"] == 1


# ---------------------------------------------------------------- 「这个账号能取哪些库」


def test_verify_reports_the_accessible_databases(db, ds, team, t_admin, spy_connector):
    """「测试连接」除了报连通,还要告诉团队管理员这个账号能访问哪些库。

    这是配完账号最该确认的事,而它**与数据源上配的默认库无关**(那只是不写库名时的解析
    起点,线上那个 business_analysis 本就不是常用库)。库列表刻意不落库,审计只记个数。
    """
    spy_connector(databases=["default", "finance_bp"])
    _configure(db, team, ds, t_admin)
    since = max_audit_id(db)
    out = verify_team_credential(team.id, ds.id, db, t_admin, ip=None)
    assert out["databases"] == ["default", "finance_bp"]
    assert out["verified"] is True
    assert one_audit_row(db, since).detail["visible_databases"] == 2


def test_verify_tolerates_an_engine_that_cannot_list_databases(
    db, ds, team, t_admin, spy_connector
):
    """列不出库不算失败:连得上就是连得上,列表为空只是少一条信息。"""
    spy_connector()
    _configure(db, team, ds, t_admin)
    since = max_audit_id(db)
    out = verify_team_credential(team.id, ds.id, db, t_admin, ip=None)
    assert out["databases"] == [] and out["verified"] is True
    assert "visible_databases" not in one_audit_row(db, since).detail

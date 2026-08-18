"""团队与成员的接口层:权限边界、成员资格约束、审计留痕、删团队卡点。

与仓库既有测试同风格:直接调路由函数,不起 TestClient。
"""
import pytest
from sqlalchemy import func, select

from app.api.routes.tasks import transfer_task_team
from app.api.routes.teams import (
    add_team_member,
    create_team,
    delete_team,
    get_team,
    grant_team_admin,
    list_candidates,
    list_teams,
    remove_team_member,
    revoke_team_admin,
    update_team,
)
from app.core.exceptions import NotFoundError, PermissionDeniedError, RubicError
from app.models import audit as A
from app.models.team import Team, TeamMember
from app.models.template import STATUS_ARCHIVED, SqlTemplate
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER
from app.schemas.team import MemberIn, TaskTeamIn, TeamIn, TeamUpdateIn
from app.services import permission_service
from tests.conftest import max_audit_id, one_audit_row

# ID 段 8610–8617
ADMIN, T_ADMIN, DEV_A, DEV_B, PLAIN, OUTSIDER = 8610, 8611, 8612, 8613, 8614, 8615





@pytest.fixture
def ds(db, datasource_factory):
    return datasource_factory("troute-mysql")


@pytest.fixture
def admin(db, user_factory):
    return user_factory(ADMIN, ROLE_ADMIN, "平台管理员", prefix="troute")


@pytest.fixture
def t_admin(db, user_factory):
    return user_factory(T_ADMIN, ROLE_DEVELOPER, "团队管理员", prefix="troute")


@pytest.fixture
def dev_a(db, user_factory):
    return user_factory(DEV_A, ROLE_DEVELOPER, "开发者甲", prefix="troute")


@pytest.fixture
def dev_b(db, user_factory):
    return user_factory(DEV_B, ROLE_DEVELOPER, "开发者乙", prefix="troute")


@pytest.fixture
def plain(db, user_factory):
    return user_factory(PLAIN, ROLE_USER, "普通用户", prefix="troute")


@pytest.fixture
def outsider(db, user_factory):
    return user_factory(OUTSIDER, ROLE_DEVELOPER, "外队开发者", prefix="troute")




@pytest.fixture
def team(db, t_admin, dev_a, team_factory):
    return team_factory("troute-team", [(t_admin, True), (dev_a, False)])


@pytest.fixture
def other_team(db, outsider, team_factory):
    return team_factory("troute-other", [(outsider, True)])








# ---------------------------------------------------------------- 团队 CRUD


def test_create_team_is_admin_only_and_audited(db, admin):
    since = max_audit_id(db)
    out = create_team(TeamIn(name="新建的团队", description="说明"), db, admin, ip=None)
    assert out["name"] == "新建的团队"
    row = one_audit_row(db, since)
    assert row.action == A.ACTION_TEAM_CREATE
    assert row.resource_type == A.RESOURCE_TEAM and row.resource_id == str(out["id"])
    assert row.resource_name == "新建的团队"


def test_create_team_with_initial_team_admins(db, admin, dev_b):
    out = create_team(
        TeamIn(name="带管理员的团队", admin_user_ids=[dev_b.id]), db, admin, ip=None
    )
    assert [a["user_id"] for a in out["admins"]] == [dev_b.id]
    assert permission_service.team_scope(db, dev_b).admin_team_ids == frozenset({out["id"]})


def test_duplicate_team_name_rejected(db, admin, team):
    with pytest.raises(RubicError, match="已存在"):
        create_team(TeamIn(name=team.name), db, admin, ip=None)


def test_update_team_records_diff(db, admin, team):
    since = max_audit_id(db)
    update_team(team.id, TeamUpdateIn(description="改过的说明"), db, admin, ip=None)
    row = one_audit_row(db, since)
    assert row.action == A.ACTION_TEAM_UPDATE
    assert row.detail["changes"]["description"]["to"] == "改过的说明"


def test_list_teams_scopes_to_membership(db, admin, dev_a, outsider, team, other_team):
    """平台管理员看全部;开发者只看自己所属的 —— 返回空即意味着「还不能建任务」。"""
    admin_names = {t["name"] for t in list_teams(db, admin)}
    assert {team.name, other_team.name} <= admin_names
    assert [t["name"] for t in list_teams(db, dev_a)] == [team.name]
    assert [t["name"] for t in list_teams(db, outsider)] == [other_team.name]


def test_get_team_requires_membership(db, dev_a, outsider, team):
    assert get_team(team.id, db, dev_a)["name"] == team.name
    with pytest.raises(PermissionDeniedError, match="不是团队"):
        get_team(team.id, db, outsider)


# ---------------------------------------------------------------- 成员


def test_add_member_only_accepts_developers(db, t_admin, plain, team):
    """需求 2:团队成员仅可添加开发者。业务使用者拿的是任务级授权,不该进团队 ——
    进了团队就等于拿到该团队取数账号的全部数据权限。"""
    with pytest.raises(RubicError, match="只能添加"):
        add_team_member(team.id, MemberIn(user_id=plain.id), db, t_admin, ip=None)


def test_add_member_is_team_admin_only_and_audited(db, t_admin, dev_b, outsider, team):
    since = max_audit_id(db)
    out = add_team_member(team.id, MemberIn(user_id=dev_b.id), db, t_admin, ip=None)
    assert out["user_id"] == dev_b.id and out["is_team_admin"] is False
    row = one_audit_row(db, since)
    assert row.action == A.ACTION_TEAM_MEMBER_ADD
    assert row.detail["target_user_id"] == dev_b.id

    # 别的团队的团队管理员管不到这里
    with pytest.raises(PermissionDeniedError):
        add_team_member(team.id, MemberIn(user_id=OUTSIDER), db, outsider, ip=None)


def test_add_duplicate_member_rejected(db, t_admin, dev_a, team):
    with pytest.raises(RubicError, match="已经是"):
        add_team_member(team.id, MemberIn(user_id=dev_a.id), db, t_admin, ip=None)


def test_plain_developer_cannot_add_members(db, dev_a, dev_b, team):
    """普通成员没有治理能力 —— 只有团队管理员/平台管理员能加人。"""
    with pytest.raises(PermissionDeniedError, match="团队管理员"):
        add_team_member(team.id, MemberIn(user_id=dev_b.id), db, dev_a, ip=None)


def test_remove_member_cascades_edit_grants_in_one_audit_row(db, ds, t_admin, dev_a, team):
    """离队要连带撤销他在本团队任务上的编辑权,且**只发一条**审计行
    (级联清单进 detail;一次操作发 N 条会让治理阅读变差)。"""
    tmpl = SqlTemplate(
        name="troute-待撤销编辑权的任务", datasource_id=ds.id, dialect="mysql",
        author_id=T_ADMIN, team_id=team.id,
    )
    db.add(tmpl)
    db.commit()
    permission_service.grant_edit(
        db, template_id=tmpl.id, user_id=dev_a.id, granted_by=t_admin.id
    )
    assert permission_service.can_edit_template(db, dev_a, tmpl.id) is True

    since = max_audit_id(db)
    out = remove_team_member(team.id, dev_a.id, db, t_admin, ip=None)
    assert out["revoked_edit_template_ids"] == [tmpl.id]
    row = one_audit_row(db, since)
    assert row.action == A.ACTION_TEAM_MEMBER_REMOVE
    assert row.detail["revoked_edit_template_ids"] == [tmpl.id]
    # 授权行真的被删掉了(而不是靠 can_edit 的惰性兜底)
    assert permission_service.team_scope(db, dev_a).edit_ids == frozenset()


def test_remove_non_member_raises(db, t_admin, plain, team):
    # 用 plain(永远不会被加进任何团队,因为 add_member 只收开发者)避免依赖用例执行顺序
    with pytest.raises(NotFoundError, match="不是本团队成员"):
        remove_team_member(team.id, plain.id, db, t_admin, ip=None)


def test_grant_and_revoke_team_admin_are_audited(db, admin, dev_a, team):
    """指定/取消团队管理员由平台管理员操作(需求 1)。

    注:`require_admin` 是 FastAPI 依赖,本仓库的测试直接调路由函数、不经依赖注入,
    所以「非管理员被拒」这件事不在这里断言 —— 它由 deps.require_admin 本身保证,
    且 test_audit_coverage 的清单门禁会确保这些端点被登记。这里只验行为与留痕。
    """
    since = max_audit_id(db)
    out = grant_team_admin(team.id, dev_a.id, db, admin, ip=None)
    assert out["is_team_admin"] is True
    assert one_audit_row(db, since).action == A.ACTION_TEAM_ADMIN_GRANT

    since = max_audit_id(db)
    out = revoke_team_admin(team.id, dev_a.id, db, admin, ip=None)
    assert out["is_team_admin"] is False
    row = one_audit_row(db, since)
    assert row.action == A.ACTION_TEAM_ADMIN_REVOKE
    assert row.detail["remaining_team_admins"] == 1


def test_team_admin_can_be_emptied(db, admin, t_admin, team):
    """刻意允许清零:平台管理员本就不受团队约束、能兜底,造一个「不能移除最后一名
    团队管理员」的不变量只会在组织调整时挡路。前端会标「无团队管理员」告警。"""
    revoke_team_admin(team.id, t_admin.id, db, admin, ip=None)
    from app.services import team_service

    assert team_service.team_admin_ids(db, team.id) == []


def test_candidates_are_logged_in_developers_not_in_team(
    db, t_admin, dev_a, plain, team, user_factory
):
    # 专用的「从未入队」开发者:dev_b 会被别的用例加进团队,共用会让本用例依赖执行顺序
    fresh = user_factory(8616, ROLE_DEVELOPER, "候选开发者", prefix="troute")
    never_logged_in = user_factory(
        8617, ROLE_DEVELOPER, "没登录过的开发者", prefix="troute", logged_in=False
    )

    ids = {c["user_id"] for c in list_candidates(team.id, None, db, t_admin)}
    assert fresh.id in ids                 # 开发者、已登录、不在本团队
    assert dev_a.id not in ids             # 已在本团队
    assert plain.id not in ids             # 不是开发者
    assert never_logged_in.id not in ids   # 没登录过 —— 还不是平台上的真实用户
    assert T_ADMIN not in ids


# ---------------------------------------------------------------- 删团队


# 删团队的用例各自建一个专用团队:库不按用例清理,共用 team 夹具会被别的用例留下的
# 任务/成员干扰,变成「时而通过」。


def test_delete_team_blocked_while_tasks_exist_including_archived(
    db, ds, admin, dev_b, team_factory
):
    """含**回收站里已下线**的任务也算 —— 与「数据源被任务引用时不可删」同一口径。
    错误文案必须点明这一点,否则 UI 上看不见的归档任务会让人莫名其妙。"""
    doomed = team_factory("troute-待删团队", [(dev_b, True)])
    tmpl = SqlTemplate(
        name="troute-已下线任务", datasource_id=ds.id, dialect="mysql",
        status=STATUS_ARCHIVED, author_id=DEV_B, team_id=doomed.id,
    )
    db.add(tmpl)
    db.commit()

    with pytest.raises(RubicError, match="回收站"):
        delete_team(doomed.id, db, admin, ip=None)

    # 转移走之后才能删
    other = create_team(TeamIn(name="troute-接收团队"), db, admin, ip=None)
    transfer_task_team(tmpl.id, TaskTeamIn(team_id=other["id"]), db, admin, ip=None)
    since = max_audit_id(db)
    delete_team(doomed.id, db, admin, ip=None)
    row = one_audit_row(db, since)
    assert row.action == A.ACTION_TEAM_DELETE
    assert db.get(Team, doomed.id) is None
    # 成员行随之清零
    assert db.scalar(
        select(func.count()).select_from(TeamMember).where(TeamMember.team_id == doomed.id)
    ) == 0


def test_delete_team_reports_revoked_credentials(db, ds, admin, team_factory, team_credential):
    doomed = team_factory("troute-待删团队2", [])
    team_credential(doomed, ds, username="to_be_deleted")
    since = max_audit_id(db)
    delete_team(doomed.id, db, admin, ip=None)
    row = one_audit_row(db, since)
    assert row.detail["revoked_credentials"] == 1

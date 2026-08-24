"""护栏:把人降成「普通用户」时,必须同时把他清出全部团队。

守的坑:set_role 原先只改 users.role 一列,而权限判定的核心 permission_service.is_insider
**只看团队成员关系、完全不看角色**。于是一个被降级的开发者仍然:看得到该团队全部任务
(含草稿与 SQL 原文)、跑得动全部已上线任务、下得了别人跑出来的结果 —— 而管理员点那个
按钮时的心智是「我把权限收回来了」。

team_service.add_member 明确写着「只能添加开发者」:入口守了、出口没守,这条用例守出口。
"""
import pytest
from sqlalchemy import select

from app.api.routes.admin import RoleIn, set_role
from app.models.audit import ACTION_USER_ROLE_CHANGE
from app.models.team import TeamMember
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER
from app.services import permission_service
from tests.conftest import one_audit_row, max_audit_id

# ID 段 9120–9122
ADMIN, DEV, KEEPER = 9120, 9121, 9122


@pytest.fixture
def admin(user_factory):
    return user_factory(ADMIN, ROLE_ADMIN, "平台管理员", prefix="dg")


@pytest.fixture
def dev(user_factory):
    return user_factory(DEV, ROLE_DEVELOPER, "要被降级的开发者", prefix="dg")


@pytest.fixture
def keeper(user_factory):
    return user_factory(KEEPER, ROLE_DEVELOPER, "留在队里的开发者", prefix="dg")


@pytest.fixture
def team(team_factory, dev, keeper):
    return team_factory("dg-team", [(dev, True), (keeper, False)])


def _member_rows(db, user_id):
    return list(db.scalars(select(TeamMember).where(TeamMember.user_id == user_id)))


def test_downgrade_to_user_clears_team_membership(db, admin, dev, team):
    """降成普通用户 → 成员行被清掉,团队内视角随之消失。"""
    assert _member_rows(db, dev.id), "前置:他本来在团队里"
    since = max_audit_id(db)

    set_role(dev.id, RoleIn(role=ROLE_USER), db, admin, ip=None)

    assert _member_rows(db, dev.id) == [], "降级后不该还留着成员行"
    scope = permission_service.team_scope(db, dev)
    assert scope.team_ids == frozenset(), "团队内视角必须一起消失"
    row = one_audit_row(db, since)
    assert row.action == ACTION_USER_ROLE_CHANGE
    assert row.detail["removed_from_teams"] == [{"team_id": team.id, "team_name": team.name}], (
        "被清出哪几个团队必须进审计 —— 这是一次实质的权限回收,不能只记角色变了"
    )


def test_downgrade_revokes_task_edit_grants(
    db, admin, dev, keeper, team, datasource_factory, team_credential
):
    """连带撤销他在本团队任务上的「指定任务编辑权」,否则重新入队时会静默复活。"""
    from app.api.routes.templates import create_template
    from app.schemas.template import TemplateCreateIn

    ds = datasource_factory("dg-mysql")
    team_credential(team, ds, username="dg_acct")
    tmpl = create_template(
        TemplateCreateIn(
            name="dg-任务", team_id=team.id, datasource_id=ds.id,
            sql_text="SELECT 1", params=[],
        ),
        db, keeper, ip=None,
    )
    permission_service.grant_edit(db, template_id=tmpl.id, user_id=dev.id, granted_by=admin.id)
    assert permission_service.team_scope(db, dev).edit_ids == frozenset({tmpl.id})

    set_role(dev.id, RoleIn(role=ROLE_USER), db, admin, ip=None)

    assert permission_service.team_scope(db, dev).edit_ids == frozenset()


def test_promotion_and_sideways_moves_keep_membership(db, admin, dev, keeper, team):
    """只有「降成普通用户」才清队:提成管理员、或角色没变,成员关系原样保留。"""
    set_role(dev.id, RoleIn(role=ROLE_ADMIN), db, admin, ip=None)
    assert _member_rows(db, dev.id), "提成管理员不该把人踢出团队"

    set_role(keeper.id, RoleIn(role=ROLE_DEVELOPER), db, admin, ip=None)
    assert _member_rows(db, keeper.id), "角色没变更不该有任何副作用"


def test_audit_tells_apart_empty_and_not_applicable(db, admin, dev, team):
    """护栏(语义):`removed_from_teams` 的空列表**只能**表示「清了队,但他不在任何团队」。

    守的坑:提权 / 平调根本不执行清队,若也写 `[]`,事后查审计的人会把它读成「此人当时不在
    任何团队」—— 而他可能正在三个团队里、只是这个动作不碰成员关系。两种情况必须分得开:
    不适用写 null,真的清了才写列表。键仍然恒有(缺键会被读成「这个版本还没这功能」)。
    """
    since = max_audit_id(db)
    set_role(dev.id, RoleIn(role=ROLE_ADMIN), db, admin, ip=None)  # 提权:不清队
    assert _member_rows(db, dev.id), "前置:提权不该动成员关系"
    detail = one_audit_row(db, since).detail
    assert "removed_from_teams" in detail, "键要恒有"
    assert detail["removed_from_teams"] is None, (
        f"本次动作不涉及清队,应写 null 而不是空列表,实际:{detail['removed_from_teams']!r}"
    )


def test_downgrade_of_someone_with_no_team_is_a_noop(db, admin, user_factory):
    """不在任何团队的人被降级:审计里那一项是空列表,不是缺键 —— 同一个动作只该有一种形状。"""
    loner = user_factory(9123, ROLE_DEVELOPER, "无队开发者", prefix="dg")
    since = max_audit_id(db)
    set_role(loner.id, RoleIn(role=ROLE_USER), db, admin, ip=None)
    assert one_audit_row(db, since).detail["removed_from_teams"] == []

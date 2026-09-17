"""运营分析的可见范围:谁能进、进来看到哪一片、以及那一片绝不会超出他本来就能看的东西。

这是整个模块的权限闸门。指标算错了页面上看得出来,范围算错了**看不出来** —— 一个团队
管理员看到别队的数字,页面上和看自己的数字长得一模一样。所以这一层单独一个文件,
先于任何指标落地。

最后一条 test_scope_never_exceeds_viewer_visibility 是不变量测试:analytics 自己造收窄
条件而不复用 permission_service.visible_condition(两者口径本就不同,见 analytics_service
的模块 docstring),那就必须有东西保证它永远不会变成一条绕过 permission_service 的侧门。
"""
import pytest

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER
from app.services import analytics_service, permission_service

# ID 段 9310–9315
PLAT, A_ADMIN, B_ADMIN, MULTI, PLAIN_DEV, BIZ = 9310, 9311, 9312, 9313, 9314, 9315


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("asc-mysql")


@pytest.fixture
def plat(user_factory):
    return user_factory(PLAT, ROLE_ADMIN, "平台管理员", prefix="asc")


@pytest.fixture
def a_admin(user_factory):
    return user_factory(A_ADMIN, ROLE_DEVELOPER, "甲队团队管理员", prefix="asc")


@pytest.fixture
def b_admin(user_factory):
    return user_factory(B_ADMIN, ROLE_DEVELOPER, "乙队团队管理员", prefix="asc")


@pytest.fixture
def multi(user_factory):
    """同时管两个队的人 —— 默认落在哪个队必须是确定的。"""
    return user_factory(MULTI, ROLE_DEVELOPER, "管两个队的人", prefix="asc")


@pytest.fixture
def plain_dev(user_factory):
    """在团队里但**不是**团队管理员的开发者 —— 他不该进得来。"""
    return user_factory(PLAIN_DEV, ROLE_DEVELOPER, "普通开发者", prefix="asc")


@pytest.fixture
def biz(user_factory):
    return user_factory(BIZ, ROLE_USER, "业务使用者", prefix="asc")


@pytest.fixture
def team_a(db, a_admin, multi, plain_dev, team_factory):
    return team_factory("asc-team-A", [(a_admin, True), (multi, True), (plain_dev, False)])


@pytest.fixture
def team_b(db, b_admin, multi, team_factory):
    return team_factory("asc-team-B", [(b_admin, True), (multi, True)])


# ---------------------------------------------------------------- 谁能进来


def test_platform_admin_defaults_to_platform_scope(db, plat, team_a):
    s = analytics_service.resolve_scope(db, plat)
    assert s.is_platform and s.team_id is None and s.team_name is None


def test_platform_admin_can_drill_into_any_team(db, plat, team_a, team_b):
    """平台管理员不在任何团队里,照样能下钻 —— 豁免由 require_team_admin 提供。"""
    s = analytics_service.resolve_scope(db, plat, team_b.id)
    assert not s.is_platform
    assert s.team_id == team_b.id and s.team_name == team_b.name


def test_team_admin_defaults_to_own_team(db, a_admin, team_a):
    s = analytics_service.resolve_scope(db, a_admin)
    assert not s.is_platform and s.team_id == team_a.id


def test_team_admin_cannot_drill_into_another_team(db, a_admin, team_a, team_b):
    """越权传别队的 id —— 直接 403,**不是**静默收窄回他自己的队。

    静默收窄意味着界面上选了乙队却显示甲队的数,没人会发现;403 当场就知道。
    """
    with pytest.raises(PermissionDeniedError):
        analytics_service.resolve_scope(db, a_admin, team_b.id)


def test_team_admin_can_pass_own_team_explicitly(db, a_admin, team_a):
    s = analytics_service.resolve_scope(db, a_admin, team_a.id)
    assert s.team_id == team_a.id


def test_multi_team_admin_default_is_deterministic(db, multi, team_a, team_b):
    """管多个队的人每次进来必须落在同一个队,否则他会以为数字在自己跳。"""
    first = analytics_service.resolve_scope(db, multi)
    again = analytics_service.resolve_scope(db, multi)
    assert first.team_id == again.team_id == min(team_a.id, team_b.id)


def test_plain_developer_is_refused(db, plain_dev, team_a):
    """在团队里但不是团队管理员 —— 运营分析不对他开放。"""
    with pytest.raises(PermissionDeniedError):
        analytics_service.resolve_scope(db, plain_dev)


def test_business_user_is_refused(db, biz):
    with pytest.raises(PermissionDeniedError):
        analytics_service.resolve_scope(db, biz)


def test_unknown_team_id_is_not_found(db, plat):
    with pytest.raises(NotFoundError):
        analytics_service.resolve_scope(db, plat, 99_999_999)


# ---------------------------------------------------------------- 选项列表


def test_scope_options_for_platform_admin_lead_with_whole_platform(db, plat, team_a, team_b):
    opts = analytics_service.scope_options(db, plat)
    assert opts[0] == {"team_id": None, "name": "全平台"}
    assert {o["team_id"] for o in opts} >= {None, team_a.id, team_b.id}


def test_scope_options_for_team_admin_exclude_whole_platform(db, a_admin, team_a, team_b):
    """选项本身就是权限的一部分:不给一个点了会 403 的选项。"""
    opts = analytics_service.scope_options(db, a_admin)
    assert [o["team_id"] for o in opts] == [team_a.id]
    assert all(o["team_id"] is not None for o in opts)


# ---------------------------------------------------------------- 收窄谓词




def test_template_ids_are_scoped_to_the_team(db, a_admin, b_admin, ds, team_a, team_b, template_factory):
    ta = template_factory(a_admin, ds, team_a, "asc-甲队任务")
    tb = template_factory(b_admin, ds, team_b, "asc-乙队任务")

    ids = analytics_service.scope_template_ids(
        db, analytics_service.resolve_scope(db, a_admin)
    )
    assert ta.id in ids
    assert tb.id not in ids


def test_platform_scope_means_no_limit_not_an_empty_set(db, plat):
    """None = 不设限,与 permission_service.visible_condition 同一约定。

    返回空集合会让调用方写出 `WHERE id IN ()` —— 平台管理员看到的会是一片空白。
    """
    s = analytics_service.resolve_scope(db, plat)
    assert analytics_service.scope_template_ids(db, s) is None
    assert analytics_service.template_conditions(s) == []
    assert analytics_service.job_conditions(s) == []


def test_scope_never_exceeds_viewer_visibility(db, a_admin, b_admin, ds, team_a, team_b, template_factory):
    """**不变量**:analytics 收窄到的任务,一定在这个人本来就看得见的集合里。

    analytics 自己造收窄条件而不复用 permission_service.visible_condition(两者口径本就
    不同),那就必须有东西保证它不会在某次「顺手放宽」后变成一条绕过 permission_service
    的提权侧门。这条断言就是那个东西。
    """
    template_factory(a_admin, ds, team_a, "asc-不变量-甲")
    template_factory(b_admin, ds, team_b, "asc-不变量-乙")

    scope = analytics_service.resolve_scope(db, a_admin)
    analytics_ids = analytics_service.scope_template_ids(db, scope)

    visible = permission_service.visible_templates(
        db, permission_service.team_scope(db, a_admin)
    )
    assert analytics_ids <= {t.id for t in visible}

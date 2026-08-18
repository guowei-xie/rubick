"""授权判定:团队边界 + 显式授权行。

这个文件曾断言「开发者近似管理员、对他人任务全通」—— 那正是团队功能废除的扁平语义,
故整体重写。核心不变量:
  - 跨团队互不可见、互不可编辑;
  - 同团队可见、可运行,但**默认不可编辑**他人任务;
  - 团队管理员可编辑本团队全部任务;
  - 被授予 edit 后可编辑,**离队即失效**(即使授权行还在);
  - 平台管理员全通;普通用户只有显式授权的已上线任务;
  - 无所属团队的「无主任务」fail-closed。
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.exceptions import PermissionDeniedError
from app.models.permission import (
    ACTION_EDIT,
    ACTION_VIEW,
    RESOURCE_TEMPLATE,
    SUBJECT_USER,
    Permission,
)
from app.models.team import TeamMember
from app.models.template import STATUS_DRAFT, STATUS_PUBLISHED, SqlTemplate
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER
from app.services import permission_service as ps

# ID 段 8600–8607(见 conftest 的说明:没有按用例清库,故主键必须显式且互不冲突)
ADMIN, A_ADMIN, A_AUTHOR, A_OTHER, B_DEV, BIZ, NO_TEAM = 8600, 8601, 8602, 8603, 8604, 8605, 8606





@pytest.fixture
def ds(db, datasource_factory):
    return datasource_factory("perm-mysql")


@pytest.fixture
def people(db, user_factory):
    return SimpleNamespace(
        admin=user_factory(ADMIN, ROLE_ADMIN, "平台管理员", prefix="perm"),
        a_admin=user_factory(A_ADMIN, ROLE_DEVELOPER, "甲队团队管理员", prefix="perm"),
        a_author=user_factory(A_AUTHOR, ROLE_DEVELOPER, "甲队作者", prefix="perm"),
        a_other=user_factory(A_OTHER, ROLE_DEVELOPER, "甲队另一成员", prefix="perm"),
        b_dev=user_factory(B_DEV, ROLE_DEVELOPER, "乙队成员", prefix="perm"),
        biz=user_factory(BIZ, ROLE_USER, "业务使用者", prefix="perm"),
        no_team=user_factory(NO_TEAM, ROLE_DEVELOPER, "无团队开发者", prefix="perm"),
    )




@pytest.fixture
def teams(db, people, team_factory):
    a = team_factory(
        "perm-team-A",
        [(people.a_admin, True), (people.a_author, False), (people.a_other, False)],
    )
    b = team_factory("perm-team-B", [(people.b_dev, False)])
    return SimpleNamespace(a=a, b=b)


def _tmpl(db, ds, *, name, author_id, team_id, status=STATUS_PUBLISHED) -> SqlTemplate:
    t = db.scalar(select(SqlTemplate).where(SqlTemplate.name == name))
    if t is None:
        t = SqlTemplate(
            name=name, datasource_id=ds.id, dialect="mysql", status=status,
            author_id=author_id, team_id=team_id,
        )
        db.add(t)
        db.commit()
        db.refresh(t)
    # 已上线才可运行,还需要一个已发布版本 id(can_run 的前提);这里不建真版本,给个占位
    if status == STATUS_PUBLISHED and t.published_version_id is None:
        t.published_version_id = None
    return t


@pytest.fixture
def task_a(db, ds, people, teams):
    """甲队里由 a_author 建的已上线任务。"""
    return _tmpl(db, ds, name="perm-甲队任务", author_id=A_AUTHOR, team_id=teams.a.id)


@pytest.fixture
def task_a2(db, ds, people, teams):
    """甲队里另一个任务 —— 用来验证 edit 授权不外溢。"""
    return _tmpl(db, ds, name="perm-甲队任务2", author_id=A_AUTHOR, team_id=teams.a.id)


@pytest.fixture
def task_b(db, ds, people, teams):
    return _tmpl(db, ds, name="perm-乙队任务", author_id=B_DEV, team_id=teams.b.id)


def _scope(db, user):
    return ps.team_scope(db, user)


# ---------------------------------------------------------------- 平台管理员


def test_platform_admin_sees_and_edits_everything(db, people, task_a, task_b):
    s = _scope(db, people.admin)
    assert s.is_admin is True
    assert ps.visible_condition(s) is None  # None = 不加限制,只对平台管理员成立
    for t in (task_a, task_b):
        assert ps.is_insider(s, t) and ps.can_view(s, t) and ps.can_edit(s, t)


# ---------------------------------------------------------------- 团队边界


def test_cross_team_is_invisible(db, people, teams, task_a, task_b):
    """核心断言:乙队成员看不到、也改不了甲队的任务 —— 反之亦然。"""
    s_b = _scope(db, people.b_dev)
    assert ps.can_view(s_b, task_a) is False
    assert ps.can_edit(s_b, task_a) is False
    assert ps.is_insider(s_b, task_a) is False

    # 真跑一次带谓词的查询:确认收窄发生在 SQL 侧,而不只是内存判定
    visible_ids = set(
        db.scalars(select(SqlTemplate.id).where(ps.visible_condition(s_b)))
    )
    assert task_b.id in visible_ids
    assert task_a.id not in visible_ids


def test_teammate_can_view_but_not_edit(db, people, task_a):
    """同团队互相可见(需求 3),但默认不可编辑他人任务。"""
    s = _scope(db, people.a_other)
    assert ps.can_view(s, task_a) is True
    assert ps.is_insider(s, task_a) is True
    assert ps.can_edit(s, task_a) is False


def test_author_can_edit_own(db, people, task_a):
    assert ps.can_edit(_scope(db, people.a_author), task_a) is True


def test_team_admin_edits_all_team_tasks_without_any_grant(db, people, teams, task_a, task_a2):
    """团队管理员对本团队全部任务有编辑权,且**零授权行** —— 这是身份的推论,不是授权。"""
    s = _scope(db, people.a_admin)
    assert s.edit_ids == frozenset()
    assert ps.can_edit(s, task_a) is True
    assert ps.can_edit(s, task_a2) is True


# ---------------------------------------------------------------- 指定任务的编辑权


def test_edit_grant_is_scoped_to_that_one_task(db, people, teams, task_a, task_a2, task_b):
    """授予后可编辑该任务;**不外溢**到同团队的另一个任务,也不外溢给别的团队成员。"""
    s_before = _scope(db, people.a_other)
    assert ps.can_edit(s_before, task_a) is False

    ps.grant_edit(db, template_id=task_a.id, user_id=people.a_other.id, granted_by=A_ADMIN)

    s = _scope(db, people.a_other)
    assert ps.can_edit(s, task_a) is True
    assert ps.can_edit(s, task_a2) is False          # 同团队的另一个任务:不外溢
    assert ps.can_edit(_scope(db, people.b_dev), task_a) is False  # 别队成员:不外溢


def test_edit_grant_dies_with_membership(db, people, teams, task_a):
    """离队即失去编辑权 —— **即使 Permission(edit) 行还留着**。

    这是「成员离队后任务留在团队、离队者不再可编辑」的落地方式:can_edit 叠加了
    「仍在团队内」,所以残留授权行是惰性的,不会让权限僵在那里。
    """
    ps.grant_edit(db, template_id=task_a.id, user_id=people.a_other.id, granted_by=A_ADMIN)
    assert ps.can_edit(_scope(db, people.a_other), task_a) is True

    # 只删成员行,刻意不删授权行
    db.execute(
        TeamMember.__table__.delete().where(
            (TeamMember.team_id == teams.a.id) & (TeamMember.user_id == people.a_other.id)
        )
    )
    db.commit()
    still_there = db.scalar(
        select(Permission.id).where(
            Permission.subject_id == str(people.a_other.id),
            Permission.action == ACTION_EDIT,
            Permission.resource_id == str(task_a.id),
        )
    )
    assert still_there is not None, "本用例要验证的正是残留授权行不生效"
    assert ps.can_edit(_scope(db, people.a_other), task_a) is False
    assert ps.can_view(_scope(db, people.a_other), task_a) is False


def test_revoke_edit_for_member_clears_only_that_team(db, people, teams, task_a, task_b):
    """离队清理只清该团队的任务,不误伤别处。"""
    ps.grant_edit(db, template_id=task_a.id, user_id=people.a_other.id, granted_by=A_ADMIN)
    ps.grant_edit(db, template_id=task_b.id, user_id=people.a_other.id, granted_by=ADMIN)
    revoked = ps.revoke_edit_for_member(db, team_id=teams.a.id, user_id=people.a_other.id)
    db.commit()
    assert revoked == [task_a.id]
    remaining = set(
        db.scalars(
            select(Permission.resource_id).where(
                Permission.subject_id == str(people.a_other.id),
                Permission.action == ACTION_EDIT,
            )
        )
    )
    assert remaining == {str(task_b.id)}


# ---------------------------------------------------------------- 业务使用者


def test_business_user_needs_explicit_grant(db, people, task_a):
    s = _scope(db, people.biz)
    assert ps.can_view(s, task_a) is False
    ps.grant(
        db, subject_type=SUBJECT_USER, subject_id=str(people.biz.id),
        resource_type=RESOURCE_TEMPLATE, resource_id=str(task_a.id),
        actions=[ACTION_VIEW], granted_by=A_AUTHOR,
    )
    s = _scope(db, people.biz)
    assert ps.can_view(s, task_a) is True
    assert ps.is_insider(s, task_a) is False   # 有 view ≠ 团队内部人
    assert ps.can_edit(s, task_a) is False


def test_business_user_cannot_see_draft_even_with_view_grant(db, ds, people, teams):
    """状态门:被授权 view 的业务使用者只看得到**已上线**的那一面,草稿不算。"""
    draft = _tmpl(
        db, ds, name="perm-甲队草稿", author_id=A_AUTHOR, team_id=teams.a.id,
        status=STATUS_DRAFT,
    )
    ps.grant(
        db, subject_type=SUBJECT_USER, subject_id=str(people.biz.id),
        resource_type=RESOURCE_TEMPLATE, resource_id=str(draft.id),
        actions=[ACTION_VIEW], granted_by=A_AUTHOR,
    )
    assert ps.can_view(_scope(db, people.biz), draft) is False
    # 同团队成员照样看得到草稿(内部人)
    assert ps.can_view(_scope(db, people.a_other), draft) is True


def test_grant_rejects_edit_action(db, people, task_a):
    """业务授权入口不接受 edit —— 否则任何能授权的人都能给自己开编辑权(提权后门)。"""
    with pytest.raises(PermissionDeniedError):
        ps.grant(
            db, subject_type=SUBJECT_USER, subject_id=str(people.biz.id),
            resource_type=RESOURCE_TEMPLATE, resource_id=str(task_a.id),
            actions=[ACTION_EDIT], granted_by=A_AUTHOR,
        )


# ---------------------------------------------------------------- 运行与运行记录


def test_can_run_requires_published_version(db, people, task_a):
    """未上线(或没有已发布版本)时谁都跑不了,含作者与平台管理员。"""
    assert task_a.published_version_id is None
    assert ps.can_run(_scope(db, people.a_author), task_a) is False
    assert ps.can_run(_scope(db, people.admin), task_a) is False

    task_a.published_version_id = 1  # 占位:can_run 只看它是否为空
    db.commit()
    # 内部人天然可运行:同团队跑的是同一个团队账号,拦住「运行」只是形式
    assert ps.can_run(_scope(db, people.a_other), task_a) is True
    assert ps.can_run(_scope(db, people.b_dev), task_a) is False


def test_can_access_job_follows_task_visibility(db, people, task_a):
    """运行记录/结果的可见性:发起人本人 + 对该任务可见的人。

    这一条收口的是本次最宽的口子 —— 此前任何开发者都能预览/下载任何人的取数结果。
    """
    job = SimpleNamespace(user_id=people.biz.id, template_id=task_a.id)
    assert ps.can_access_job(db, people.biz, job) is True        # 发起人本人
    assert ps.can_access_job(db, people.a_other, job) is True    # 同团队
    assert ps.can_access_job(db, people.admin, job) is True      # 平台管理员
    assert ps.can_access_job(db, people.b_dev, job) is False     # 别的团队


# ---------------------------------------------------------------- 边界情形


def test_orphan_task_is_fail_closed(db, ds, people):
    """无所属团队的「无主任务」:除平台管理员外谁都看不到、也改不了。

    存量迁移会把所有任务并入默认团队并硬断言无残留(migrate._assert_every_template_has_team),
    这条是那道断言之外的第二层保险 —— 数据没迁完时宁可少给,也不要静默放行。
    """
    orphan = _tmpl(db, ds, name="perm-无主任务", author_id=A_AUTHOR, team_id=None)
    for u in (people.a_author, people.a_admin, people.a_other, people.b_dev, people.biz):
        s = _scope(db, u)
        assert ps.can_view(s, orphan) is False, f"{u.name} 不该看到无主任务"
        assert ps.can_edit(s, orphan) is False, f"{u.name} 不该能编辑无主任务"
    assert ps.can_edit(_scope(db, people.admin), orphan) is True


def test_developer_without_team_cannot_create(db, people, teams):
    """需求 4:开发者必须先有团队才能建任务。can_author 为真但选不出团队。"""
    assert ps.can_author(people.no_team) is True
    with pytest.raises(PermissionDeniedError, match="还不属于任何团队"):
        ps.require_can_create_in_team(db, people.no_team, teams.a.id)


def test_developer_cannot_create_in_foreign_team(db, people, teams):
    with pytest.raises(PermissionDeniedError, match="不是团队"):
        ps.require_can_create_in_team(db, people.b_dev, teams.a.id)
    # 自己的团队放行;平台管理员任意团队放行
    ps.require_can_create_in_team(db, people.b_dev, teams.b.id)
    ps.require_can_create_in_team(db, people.admin, teams.a.id)


def test_plain_user_cannot_author(db, people):
    assert ps.can_author(people.biz) is False
    assert ps.is_platform_admin(people.biz) is False


def test_manageable_template_ids_scopes_to_editable(db, people, teams, task_a, task_a2):
    """能对之授权的任务 = 我有编辑权的任务。平台管理员返回 None(全部)。"""
    assert ps.manageable_template_ids(db, _scope(db, people.admin)) is None
    # 团队管理员:本团队全部
    a_admin_ids = ps.manageable_template_ids(db, _scope(db, people.a_admin))
    assert {task_a.id, task_a2.id} <= a_admin_ids
    # 普通成员:只有自己作者的 + 被授予 edit 的
    other_ids = ps.manageable_template_ids(db, _scope(db, people.a_other))
    assert task_a.id not in other_ids and task_a2.id not in other_ids
    # 无团队开发者:空集
    assert ps.manageable_template_ids(db, _scope(db, people.no_team)) == set()

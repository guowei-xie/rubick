"""任务作者转移(离职交接):谁能转、能转给谁、转完连带改变了什么。

本功能唯一的设计难点是「发起人集合恰好是 can_edit 减去最后一条」——
被授予该任务编辑权的人**有** can_manage 却**不能**转移作者。
test_edit_grantee_can_edit_but_cannot_transfer 就是钉死这条减法的那颗钉子。
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.api.routes.tasks import (
    grant_task_editor,
    list_tasks,
    task_author_candidates,
    transfer_task_author,
)
from app.api.routes.templates import create_template, publish, update_template
from app.core.exceptions import NotFoundError, PermissionDeniedError, RubicError
from app.models.audit import ACTION_TASK_AUTHOR_TRANSFER
from app.models.notification import Notification
from app.models.permission import Permission
from app.models.team import TeamMember
from app.models.template import TemplateVersion
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER
from app.schemas.common import ParamDef
from app.schemas.team import EditorIn, TaskAuthorIn
from app.schemas.template import PublishIn, TemplateCreateIn, TemplateUpdateIn
from app.services import notify_service, permission_service
from tests.conftest import max_audit_id, one_audit_row

pytestmark = pytest.mark.usefixtures("clean_credentials")

SQL = "SELECT c FROM o WHERE d = :d"

# ID 段 8680–8686
ADMIN, A_ADMIN, A_AUTHOR, A_OTHER, A_THIRD, B_DEV, BIZ = (
    8680, 8681, 8682, 8683, 8684, 8685, 8686,
)


@pytest.fixture
def ds(db, datasource_factory):
    return datasource_factory("xfer-mysql")


@pytest.fixture
def people(db, user_factory):
    return SimpleNamespace(
        admin=user_factory(ADMIN, ROLE_ADMIN, "平台管理员", prefix="xfer"),
        a_admin=user_factory(A_ADMIN, ROLE_DEVELOPER, "甲队团队管理员", prefix="xfer"),
        author=user_factory(A_AUTHOR, ROLE_DEVELOPER, "甲队作者", prefix="xfer"),
        other=user_factory(A_OTHER, ROLE_DEVELOPER, "甲队接手人", prefix="xfer"),
        third=user_factory(A_THIRD, ROLE_DEVELOPER, "甲队第三人", prefix="xfer"),
        b_dev=user_factory(B_DEV, ROLE_DEVELOPER, "乙队成员", prefix="xfer"),
        biz=user_factory(BIZ, ROLE_USER, "业务使用者", prefix="xfer"),
    )


@pytest.fixture
def teams(db, ds, people, team_factory, team_credential):
    a = team_factory(
        "xfer-team-A",
        [
            (people.a_admin, True),
            (people.author, False),
            (people.other, False),
            (people.third, False),
        ],
    )
    b = team_factory("xfer-team-B", [(people.b_dev, True)])
    team_credential(a, ds, username="xferA_acct")
    team_credential(b, ds, username="xferB_acct")
    return SimpleNamespace(a=a, b=b)


def _make(db, actor, ds, team, name, *, published=True):
    tmpl = create_template(
        TemplateCreateIn(
            name=name, team_id=team.id, datasource_id=ds.id, sql_text=SQL,
            params=[ParamDef(name="d", kind="single", label="日期")],
        ),
        db, actor, ip=None,
    )
    if published:
        publish(tmpl.id, PublishIn(note="上线"), db, actor, ip=None)
    return tmpl


@pytest.fixture
def task(db, ds, people, teams):
    return _make(db, people.author, ds, teams.a, "xfer-甲队任务")


def _row(db, actor, tmpl):
    return next(r for r in list_tasks(db, actor) if r.id == tmpl.id)


def _transfer(db, actor, tmpl, target):
    return transfer_task_author(tmpl.id, TaskAuthorIn(user_id=target.id), db, actor, ip=None)


def _can_edit(db, actor, tmpl):
    return permission_service.can_edit(permission_service.team_scope(db, actor), tmpl)


# ---------------------------------------------------------------- 发起人口径


def test_author_can_transfer_to_teammate(db, people, task):
    _transfer(db, people.author, task, people.other)
    db.refresh(task)
    assert task.author_id == people.other.id


def test_team_admin_can_transfer(db, people, task):
    _transfer(db, people.a_admin, task, people.other)
    db.refresh(task)
    assert task.author_id == people.other.id


def test_platform_admin_can_transfer(db, people, task):
    """平台管理员不是甲队成员,照样能代办 —— 离职交接的兜底路径。"""
    _transfer(db, people.admin, task, people.other)
    db.refresh(task)
    assert task.author_id == people.other.id


def test_edit_grantee_can_edit_but_cannot_transfer(db, people, task):
    """**本文件最关键的一条**:被授予编辑权的人有 can_manage,却不能处分归属。

    edit 是「来帮着改这个 SQL」的委托;若它连带包含转移作者,任何被临时拉来改 SQL 的
    同事都能把作者改成自己,而授权给他的团队管理员事后只能从审计里发现。
    """
    grant_task_editor(task.id, EditorIn(user_id=people.third.id), db, people.a_admin, ip=None)
    assert _can_edit(db, people.third, task) is True
    with pytest.raises(PermissionDeniedError, match="不含"):
        _transfer(db, people.third, task, people.other)
    db.refresh(task)
    assert task.author_id == people.author.id


def test_plain_teammate_cannot_transfer(db, people, task):
    with pytest.raises(PermissionDeniedError):
        _transfer(db, people.other, task, people.third)


def test_outsider_cannot_transfer(db, people, task):
    with pytest.raises(PermissionDeniedError):
        _transfer(db, people.b_dev, task, people.other)


def test_business_user_cannot_transfer(db, people, task):
    with pytest.raises(PermissionDeniedError):
        _transfer(db, people.biz, task, people.other)


def test_ex_author_removed_from_team_cannot_transfer(db, people, teams, task):
    """作者已被移出团队但任务还在:他自己转不动了,只能由管理员代办。

    这正是「离职交接要先转作者、再降角色」那条操作顺序的由来。
    """
    db.execute(
        TeamMember.__table__.delete().where(
            TeamMember.team_id == teams.a.id, TeamMember.user_id == people.author.id
        )
    )
    db.commit()
    with pytest.raises(PermissionDeniedError, match="无权"):
        _transfer(db, people.author, task, people.other)
    # 团队管理员兜底
    _transfer(db, people.a_admin, task, people.other)
    db.refresh(task)
    assert task.author_id == people.other.id


def test_orphan_task_rejected_even_for_platform_admin(db, people, task):
    """无主任务(team_id 为空)恒拒:接收人被定义为「该团队的成员」,没有团队就没有合法接收人。"""
    task.team_id = None
    db.commit()
    scope = permission_service.team_scope(db, people.admin)
    assert permission_service.can_transfer_author(scope, task) is False
    with pytest.raises(RubicError, match="还没有所属团队"):
        _transfer(db, people.admin, task, people.other)


# ---------------------------------------------------------------- 接收人校验


def test_transfer_to_unknown_user_is_404(db, people, task):
    with pytest.raises(NotFoundError, match="用户不存在"):
        transfer_task_author(task.id, TaskAuthorIn(user_id=999999), db, people.author, ip=None)


def test_transfer_to_current_author_is_rejected(db, people, task):
    with pytest.raises(RubicError, match="已经是"):
        _transfer(db, people.author, task, people.author)


def test_transfer_to_inactive_user_is_rejected(db, people, task):
    people.other.is_active = False
    db.commit()
    try:
        with pytest.raises(RubicError, match="停用"):
            _transfer(db, people.author, task, people.other)
    finally:
        people.other.is_active = True
        db.commit()


def test_transfer_to_non_member_is_rejected(db, people, task):
    with pytest.raises(RubicError, match="不是团队"):
        _transfer(db, people.author, task, people.b_dev)


# ---------------------------------------------------------------- 连带效果


def test_old_author_loses_edit_new_author_gains(db, people, task):
    assert _can_edit(db, people.author, task) is True
    assert _can_edit(db, people.other, task) is False
    _transfer(db, people.author, task, people.other)
    db.refresh(task)
    assert _can_edit(db, people.author, task) is False
    assert _can_edit(db, people.other, task) is True


def test_team_admin_author_keeps_edit_after_transfer(db, ds, people, teams):
    """原作者恰是团队管理员时,转出后仍能编辑 —— 权限来自第 2 条阶梯而非作者身份。"""
    t = _make(db, people.a_admin, ds, teams.a, "xfer-团管的任务")
    _transfer(db, people.a_admin, t, people.other)
    db.refresh(t)
    assert _can_edit(db, people.a_admin, t) is True


def test_redundant_edit_grant_is_cleared(db, people, task):
    """新作者原先那条 edit 授权行随转移清掉:作者身份已覆盖它,留着就成了一条
    在 UI 上看不见(author 盖住 granted)、因而撤不掉的僵尸授权。"""
    grant_task_editor(task.id, EditorIn(user_id=people.other.id), db, people.a_admin, ip=None)
    since = max_audit_id(db)
    _transfer(db, people.a_admin, task, people.other)
    rows = db.scalars(
        select(Permission).where(
            Permission.resource_id == str(task.id),
            Permission.subject_id == str(people.other.id),
            Permission.action == "edit",
        )
    ).all()
    assert rows == []
    assert one_audit_row(db, since).detail["revoked_redundant_edit"] is True


def test_other_editors_and_business_grants_survive(db, people, task):
    """团队没变,别人的 edit 授权与业务方授权的前提都还成立 ——
    与「转移团队」要连带撤销的情形正好相反。"""
    grant_task_editor(task.id, EditorIn(user_id=people.third.id), db, people.a_admin, ip=None)
    permission_service.grant(
        db, subject_type="user", subject_id=str(people.biz.id),
        resource_type="template", resource_id=str(task.id),
        actions=["view", "run"], granted_by=people.a_admin.id,
    )
    _transfer(db, people.author, task, people.other)
    db.refresh(task)
    assert _can_edit(db, people.third, task) is True
    assert permission_service.can_run(permission_service.team_scope(db, people.biz), task)


def test_template_versions_author_is_untouched(db, people, task):
    """版本表记的是「这一版是谁保存的」,是不可变历史快照,转移不许改写它。"""
    update_template(
        task.id, TemplateUpdateIn(name="xfer-甲队任务-改名"), db, people.author, ip=None
    )
    before = {
        v.id: (v.author_id, v.accepted_by)
        for v in db.scalars(select(TemplateVersion).where(TemplateVersion.template_id == task.id))
    }
    assert before
    _transfer(db, people.author, task, people.other)
    after = {
        v.id: (v.author_id, v.accepted_by)
        for v in db.scalars(select(TemplateVersion).where(TemplateVersion.template_id == task.id))
    }
    assert after == before


def test_developed_by_me_and_flag_follow_author(db, people, task):
    assert _row(db, people.author, task).developed_by_me is True
    assert _row(db, people.other, task).developed_by_me is False
    _transfer(db, people.author, task, people.other)
    assert _row(db, people.author, task).developed_by_me is False
    assert _row(db, people.other, task).developed_by_me is True


def test_can_transfer_author_flag_in_list(db, people, task):
    """列表下发的能力位:作者 / 团队管理员 / 平台管理员为真,被授 edit 与普通成员为假。"""
    grant_task_editor(task.id, EditorIn(user_id=people.third.id), db, people.a_admin, ip=None)
    assert _row(db, people.author, task).can_transfer_author is True
    assert _row(db, people.a_admin, task).can_transfer_author is True
    assert _row(db, people.admin, task).can_transfer_author is True
    third = _row(db, people.third, task)
    assert (third.can_manage, third.can_transfer_author) == (True, False)
    other = _row(db, people.other, task)
    assert (other.can_manage, other.can_transfer_author) == (False, False)


def test_can_transfer_author_is_subset_of_can_edit(db, people, task):
    """不变量:任何人对任何任务,能转移 ⇒ 能编辑。防以后有人只在其中一边加口径。"""
    grant_task_editor(task.id, EditorIn(user_id=people.third.id), db, people.a_admin, ip=None)
    for actor in (people.admin, people.a_admin, people.author, people.other,
                  people.third, people.b_dev, people.biz):
        scope = permission_service.team_scope(db, actor)
        if permission_service.can_transfer_author(scope, task):
            assert permission_service.can_edit(scope, task), actor.name


def test_archived_task_can_be_transferred(db, ds, people, teams):
    """回收站里的任务同样要能交接(can_edit 本来也不看 status)。"""
    t = _make(db, people.author, ds, teams.a, "xfer-草稿任务", published=False)
    _transfer(db, people.author, t, people.other)
    db.refresh(t)
    assert t.author_id == people.other.id


def test_failed_run_notice_follows_new_author(db, people, task):
    """订阅定时运行失败的收件人含作者 —— 转移后改发新作者,这正是离职场景最想修的。"""
    assert people.author.id in notify_service._team_fixers(db, task, include_author=True)
    _transfer(db, people.author, task, people.other)
    db.refresh(task)
    fixers = notify_service._team_fixers(db, task, include_author=True)
    assert people.other.id in fixers
    assert people.author.id not in fixers


# ---------------------------------------------------------------- 候选人名单


def test_candidates_are_active_teammates_without_current_author(db, people, task):
    got = {m["user_id"] for m in task_author_candidates(task.id, db, people.author)}
    assert got == {people.a_admin.id, people.other.id, people.third.id}


def test_candidates_exclude_inactive(db, people, task):
    people.third.is_active = False
    db.commit()
    try:
        got = {m["user_id"] for m in task_author_candidates(task.id, db, people.author)}
        assert people.third.id not in got
    finally:
        people.third.is_active = True
        db.commit()


def test_candidates_guarded_like_the_transfer(db, people, task):
    """能看到候选名单的人恰好就是能转移的人。"""
    with pytest.raises(PermissionDeniedError):
        task_author_candidates(task.id, db, people.other)


# ---------------------------------------------------------------- 审计与通知


@pytest.mark.parametrize(
    "actor_key, expected",
    [("author", "author"), ("a_admin", "team_admin"), ("admin", "platform_admin")],
)
def test_audit_records_from_to_and_initiated_as(db, people, task, actor_key, expected):
    since = max_audit_id(db)
    _transfer(db, getattr(people, actor_key), task, people.other)
    row = one_audit_row(db, since)
    assert row.action == ACTION_TASK_AUTHOR_TRANSFER
    assert row.resource_id == str(task.id)
    assert row.detail["from_user_id"] == people.author.id
    assert row.detail["from_user_name"] == people.author.name
    assert row.detail["to_user_id"] == people.other.id
    assert row.detail["initiated_as"] == expected


def _new_notes(db, floor: int):
    """本次新增的通知。用 id 水位线(同 conftest.max_audit_id 的写法),
    不把全表 id 读进内存 —— 通知表只会越长越大。"""
    return list(db.scalars(select(Notification).where(Notification.id > floor)))


def test_team_admin_transferring_own_task_is_recorded_as_author(db, ds, people, teams):
    """作者本人恰好也是团队管理员时,记「本人交接」而不是「管理员代办」——
    这条字段存在的全部理由就是分清这两者,优先级取「最贴身的主张」。"""
    t = _make(db, people.a_admin, ds, teams.a, "xfer-团管自己的任务")
    since = max_audit_id(db)
    _transfer(db, people.a_admin, t, people.other)
    assert one_audit_row(db, since).detail["initiated_as"] == "author"


def test_both_parties_are_notified(db, people, task):
    floor = db.scalar(select(func.max(Notification.id))) or 0
    _transfer(db, people.a_admin, task, people.other)
    fresh = _new_notes(db, floor)
    assert {n.user_id for n in fresh} == {people.author.id, people.other.id}
    assert len({n.title for n in fresh}) == 2


def test_inactive_old_author_gets_no_notification(db, people, task):
    """离职清理置 is_active=False 后,给原作者的那条不再发(人已经登录不进来了)。"""
    people.author.is_active = False
    db.commit()
    try:
        floor = db.scalar(select(func.max(Notification.id))) or 0
        _transfer(db, people.a_admin, task, people.other)
        assert [n.user_id for n in _new_notes(db, floor)] == [people.other.id]
    finally:
        people.author.is_active = True
        db.commit()

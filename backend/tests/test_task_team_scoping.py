"""任务列表 / 详情 / 运行记录 / 建任务:团队收窄在**真实路由**上成立。

test_permission_service 覆盖判定函数本身,这里覆盖它们被接进路由后的实际效果 ——
一条规则在服务层对、在路由层没接上,等于没做。
"""
from types import SimpleNamespace

import pytest

from app.api.routes.query import my_jobs
from app.api.routes.tasks import (
    grant_task_editor,
    list_task_editors,
    list_tasks,
    revoke_task_editor,
    task_run_records,
    transfer_task_team,
)
from app.api.routes.templates import create_template, get_template, publish, update_template
from app.core.exceptions import NotFoundError, PermissionDeniedError, RubicError
from app.models.query_job import JOB_SUCCESS, SOURCE_RUN, QueryJob
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER
from app.schemas.common import ParamDef
from app.schemas.permission import GrantIn
from app.schemas.team import EditorIn, TaskTeamIn
from app.schemas.template import PublishIn, TemplateCreateIn, TemplateUpdateIn
from app.services import permission_service

pytestmark = pytest.mark.usefixtures("clean_credentials")

SQL = "SELECT c FROM o WHERE d = :d"

# ID 段 8660–8666
ADMIN, A_ADMIN, A_AUTHOR, A_OTHER, B_DEV, BIZ, NO_TEAM = 8660, 8661, 8662, 8663, 8664, 8665, 8666





@pytest.fixture
def ds(db, datasource_factory):
    return datasource_factory("scope-mysql")


@pytest.fixture
def people(db, user_factory):
    return SimpleNamespace(
        admin=user_factory(ADMIN, ROLE_ADMIN, "平台管理员", prefix="scope"),
        a_admin=user_factory(A_ADMIN, ROLE_DEVELOPER, "甲队团队管理员", prefix="scope"),
        author=user_factory(A_AUTHOR, ROLE_DEVELOPER, "甲队作者", prefix="scope"),
        other=user_factory(A_OTHER, ROLE_DEVELOPER, "甲队另一成员", prefix="scope"),
        b_dev=user_factory(B_DEV, ROLE_DEVELOPER, "乙队成员", prefix="scope"),
        biz=user_factory(BIZ, ROLE_USER, "业务使用者", prefix="scope"),
        no_team=user_factory(NO_TEAM, ROLE_DEVELOPER, "无团队开发者", prefix="scope"),
    )




@pytest.fixture
def teams(db, ds, people, team_factory, team_credential):
    a = team_factory(
        "scope-team-A",
        [(people.a_admin, True), (people.author, False), (people.other, False)],
    )
    b = team_factory("scope-team-B", [(people.b_dev, True)])
    team_credential(a, ds, username="scopeA_acct")
    team_credential(b, ds, username="scopeB_acct")
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
def task_a(db, ds, people, teams):
    return _make(db, people.author, ds, teams.a, "scope-甲队任务")


@pytest.fixture
def draft_a(db, ds, people, teams):
    return _make(db, people.author, ds, teams.a, "scope-甲队草稿", published=False)


@pytest.fixture
def task_b(db, ds, people, teams):
    return _make(db, people.b_dev, ds, teams.b, "scope-乙队任务")


def _ids(rows):
    """按 id 而不是 name 比较:每个用例都会新建同名任务(_make 不是 get-or-create),
    库又不按用例清理,按名字断言会互相干扰。"""
    return {r.id for r in rows}


def _row(db, actor, tmpl):
    """任务在某人的任务列表里的那一行。多个用例要断言同一行上的不同能力位。"""
    return next(r for r in list_tasks(db, actor) if r.id == tmpl.id)


def _grant_biz(db, tmpl, user, granter, actions=("view", "run")):
    """把业务侧授权给某人。与 /api/tasks/{id}/editors 的 edit 授权刻意分开
    (见 models/permission.BUSINESS_ACTIONS),同兄弟测试文件里的 _grant_* 写法。"""
    permission_service.grant(
        db, subject_type="user", subject_id=str(user.id),
        resource_type="template", resource_id=str(tmpl.id),
        actions=list(actions), granted_by=granter.id,
    )


# ---------------------------------------------------------------- 列表


def test_list_tasks_is_scoped_by_team(db, people, task_a, task_b, draft_a):
    """核心断言:开发者只看得到本团队任务(含草稿),看不到别队的。"""
    assert _ids(list_tasks(db, people.author)) >= {task_a.id, draft_a.id}
    assert task_b.id not in _ids(list_tasks(db, people.author))
    assert task_a.id not in _ids(list_tasks(db, people.b_dev))
    # 平台管理员看全部
    admin_ids = _ids(list_tasks(db, people.admin))
    assert {task_a.id, task_b.id, draft_a.id} <= admin_ids


def test_list_tasks_carries_team_and_can_manage_flags(db, people, teams, task_a):
    """can_manage 是服务端算好的单一布尔 —— 前端只消费它,不自己算团队规则。"""
    def row(actor):
        return _row(db, actor, task_a)

    assert row(people.author).team_id == teams.a.id
    assert row(people.author).team_name == teams.a.name
    assert row(people.author).can_manage is True          # 作者
    assert row(people.a_admin).can_manage is True         # 团队管理员
    assert row(people.other).can_manage is False          # 同队但非作者
    assert row(people.admin).can_manage is True           # 平台管理员
    # 同队成员可运行(团队就是取数身份的边界,拦住「运行」只是形式)
    assert row(people.other).can_run is True


def test_developed_by_me_is_author_or_granted_editor(db, people, task_a):
    """「我开发的」= 作者 + 被授予编辑权的人。团队管理员/平台管理员能编辑,但那是治理权限,
    不算「我开发的」—— 否则他们一勾这个筛选就等于没筛。"""
    def row(actor):
        return _row(db, actor, task_a)

    assert row(people.author).developed_by_me is True
    assert row(people.other).developed_by_me is False    # 同队但没被授权
    grant_task_editor(task_a.id, EditorIn(user_id=people.other.id), db, people.a_admin, ip=None)
    assert row(people.other).developed_by_me is True     # 授予编辑权后算「我开发的」
    assert row(people.a_admin).developed_by_me is False  # 团队管理员:能编辑,但那是治理权限
    assert row(people.admin).developed_by_me is False    # 平台管理员同理


def test_developed_by_me_agrees_with_editor_list_for_admins(db, people, task_a, team_factory):
    """平台管理员也可能是团队成员、也可能被真授予过编辑权。两个入口(任务列表的
    developed_by_me 与编辑人名单的 source)必须对同一条授权行给出同一个答案 ——
    曾经 team_scope 给管理员开「0 查询」快路径,他的 edit_ids 恒空,这里就会自相矛盾。"""
    team_factory("scope-team-A", [(people.admin, False)])  # 管理员也可以是团队成员
    grant_task_editor(task_a.id, EditorIn(user_id=people.admin.id), db, people.a_admin, ip=None)

    sources = {e["user_id"]: e["source"] for e in list_task_editors(task_a.id, db, people.author)}
    assert sources[people.admin.id] == "granted"
    row = next(r for r in list_tasks(db, people.admin) if r.id == task_a.id)
    assert row.developed_by_me is True


def test_empty_list_for_developer_without_team(db, people, task_a):
    """无团队的开发者什么都看不到 —— 前端据此给出「请联系管理员把你加入团队」的空态。"""
    assert list_tasks(db, people.no_team) == []


def test_business_user_sees_only_granted_published(db, people, task_a, draft_a):
    assert list_tasks(db, people.biz) == []
    for t in (task_a, draft_a):
        _grant_biz(db, t, people.biz, people.author)
    biz_ids = _ids(list_tasks(db, people.biz))
    assert task_a.id in biz_ids
    assert draft_a.id not in biz_ids, "草稿不该因为一条 view 授权就暴露给业务方"


# ---------------------------------------------------------------- 详情


def test_get_template_hides_latest_version_from_outsiders(db, people, task_a):
    """SQL 原文/最新版本只给团队内部人;被授权的业务方只看得到已上线的那一面。"""
    assert get_template(task_a.id, db, people.other).latest_version is not None
    with pytest.raises(PermissionDeniedError, match="无权查看"):
        get_template(task_a.id, db, people.b_dev)

    _grant_biz(db, task_a, people.biz, people.author, actions=("view",))
    detail = get_template(task_a.id, db, people.biz)
    assert detail.published_version is not None
    assert detail.latest_version is None


def test_can_view_detail_marks_team_insiders(db, people, task_a):
    """can_view_detail 决定 ⋮ 菜单里出不出「查看」(只读打开任务详情)。

    它必须与 get_template 的分级严格同源:凡是这一位为真的人,GET /templates/{id} 都会
    给他 latest_version(SQL 原文);为假的人要么根本看不到,要么只拿得到已上线的那一面。
    两处若漂移,前端就会给出一个点开来是空的入口,或者反过来漏掉一个本该有的入口
    (分级本身由上一个用例覆盖,这里只断这一位跟没跟上)。
    """
    def row(actor):
        return _row(db, actor, task_a)

    # 同队但无编辑权的开发者:正是这一位要服务的人 —— 编辑不了,但看得到怎么写的
    assert row(people.other).can_view_detail is True
    # 有编辑权的人这一位也为真(菜单里由前端决定只给「编辑」,不在后端裁)
    assert row(people.author).can_view_detail is True
    assert row(people.a_admin).can_view_detail is True
    assert row(people.admin).can_view_detail is True

    # 被授予 view 的业务使用者:看得见这个任务,但不给只读详情入口 ——
    # 给了等于把 SQL 原文摊给他,而接口本来也不会返回 latest_version
    _grant_biz(db, task_a, people.biz, people.author, actions=("view",))
    assert row(people.biz).can_view_detail is False


# ---------------------------------------------------------------- 编辑


def test_teammate_cannot_edit_until_granted(db, people, teams, task_a):
    with pytest.raises(PermissionDeniedError, match="无权编辑"):
        update_template(task_a.id, TemplateUpdateIn(name="改个名"), db, people.other, ip=None)

    grant_task_editor(task_a.id, EditorIn(user_id=people.other.id), db, people.a_admin, ip=None)
    update_template(task_a.id, TemplateUpdateIn(name="scope-甲队任务"), db, people.other, ip=None)

    # 撤销后又不行了
    revoke_task_editor(task_a.id, people.other.id, db, people.a_admin, ip=None)
    with pytest.raises(PermissionDeniedError, match="无权编辑"):
        update_template(task_a.id, TemplateUpdateIn(name="再改"), db, people.other, ip=None)


def test_cross_team_developer_cannot_edit(db, people, task_a):
    with pytest.raises(PermissionDeniedError, match="无权编辑"):
        update_template(task_a.id, TemplateUpdateIn(name="越权改"), db, people.b_dev, ip=None)


def test_editor_grant_requires_team_membership(db, people, task_a):
    """只能把编辑权授予**本团队成员** —— 否则就成了一条跨团队的旁路。"""
    with pytest.raises(RubicError, match="不是团队"):
        grant_task_editor(task_a.id, EditorIn(user_id=people.b_dev.id), db, people.a_admin, ip=None)


def test_only_team_admin_grants_editors(db, people, task_a):
    with pytest.raises(PermissionDeniedError, match="团队管理员"):
        grant_task_editor(task_a.id, EditorIn(user_id=people.other.id), db, people.other, ip=None)


def test_list_editors_marks_implicit_sources(db, people, task_a):
    """作者与团队管理员是身份的推论(隐式、不可撤销),granted 才是一条授权行。"""
    grant_task_editor(task_a.id, EditorIn(user_id=people.other.id), db, people.a_admin, ip=None)
    rows = {r["user_id"]: r["source"] for r in list_task_editors(task_a.id, db, people.author)}
    assert rows[people.author.id] == "author"
    assert rows[people.a_admin.id] == "team_admin"
    assert rows[people.other.id] == "granted"


# ---------------------------------------------------------------- 建任务


def test_create_requires_a_team_you_belong_to(db, ds, people, teams):
    with pytest.raises(PermissionDeniedError, match="不是团队"):
        create_template(
            TemplateCreateIn(
                name="scope-越权建任务", team_id=teams.b.id, datasource_id=ds.id,
                sql_text=SQL, params=[ParamDef(name="d", kind="single", label="日期")],
            ),
            db, people.author, ip=None,
        )


def test_developer_without_team_cannot_create(db, ds, people, teams):
    with pytest.raises(PermissionDeniedError, match="还不属于任何团队"):
        create_template(
            TemplateCreateIn(
                name="scope-无团队建任务", team_id=teams.a.id, datasource_id=ds.id,
                sql_text=SQL, params=[ParamDef(name="d", kind="single", label="日期")],
            ),
            db, people.no_team, ip=None,
        )


def test_platform_admin_can_create_in_any_team(db, ds, people, teams):
    tmpl = _make(db, people.admin, ds, teams.b, "scope-管理员在乙队建的任务")
    assert tmpl.team_id == teams.b.id


# ---------------------------------------------------------------- 运行记录


def _job(db, tmpl, user) -> QueryJob:
    job = QueryJob(
        user_id=user.id, template_id=tmpl.id, template_version_id=tmpl.published_version_id,
        datasource_id=tmpl.datasource_id, params={}, status=JOB_SUCCESS, source=SOURCE_RUN,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_run_records_visible_to_insiders_only(db, people, task_a):
    """团队内部人看该任务下全部人的运行;被授权的业务方只看自己的。"""
    _grant_biz(db, task_a, people.biz, people.author)
    biz_job = _job(db, task_a, people.biz)
    author_job = _job(db, task_a, people.author)

    insider_ids = {j.id for j in task_run_records(task_a.id, db, people.other)}
    assert {biz_job.id, author_job.id} <= insider_ids

    biz_ids = {j.id for j in task_run_records(task_a.id, db, people.biz)}
    assert biz_ids == {biz_job.id}

    with pytest.raises(PermissionDeniedError, match="无权查看"):
        task_run_records(task_a.id, db, people.b_dev)


def test_my_jobs_is_scoped_by_team(db, people, task_a, task_b):
    """收口本次最宽的口子:此前任何开发者都能列到别人的全部运行记录。"""
    a_job = _job(db, task_a, people.author)
    b_job = _job(db, task_b, people.b_dev)

    assert a_job.id in {j.id for j in my_jobs(db, people.other)}
    assert b_job.id not in {j.id for j in my_jobs(db, people.other)}
    assert {a_job.id, b_job.id} <= {j.id for j in my_jobs(db, people.admin)}


# ---------------------------------------------------------------- 转移团队


def test_transfer_team_moves_scope_and_clears_editors(db, people, teams, task_a):
    """转移团队会同时改变可见范围与取数身份,故原团队的编辑权授权必须一并撤销。"""
    grant_task_editor(task_a.id, EditorIn(user_id=people.other.id), db, people.a_admin, ip=None)
    assert permission_service.can_edit_template(db, people.other, task_a.id) is True

    transfer_task_team(task_a.id, TaskTeamIn(team_id=teams.b.id), db, people.admin, ip=None)
    db.expire_all()

    assert permission_service.can_edit_template(db, people.other, task_a.id) is False
    assert task_a.id not in _ids(list_tasks(db, people.author))
    assert task_a.id in _ids(list_tasks(db, people.b_dev))


def test_transfer_to_same_team_is_rejected(db, people, teams, task_a):
    with pytest.raises(RubicError, match="已经属于"):
        transfer_task_team(task_a.id, TaskTeamIn(team_id=teams.a.id), db, people.admin, ip=None)


def test_transfer_to_unknown_team_is_404(db, people, task_a):
    with pytest.raises(NotFoundError, match="团队不存在"):
        transfer_task_team(task_a.id, TaskTeamIn(team_id=99999), db, people.admin, ip=None)


# ---------------------------------------------------------------- 提权回归


def test_business_grant_endpoint_rejects_edit_action(db, people, task_a):
    """schema 层的 Literal 收口:自由字符串会让任何能授权的人给自己塞一个 edit。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GrantIn(subject_id=str(people.biz.id), resource_id=str(task_a.id), actions=["edit"])

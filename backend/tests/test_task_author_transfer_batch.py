"""批量转移任务作者(多选离职交接):全成功才生效、通知合并、审计逐条。

单任务那一路的口径由 test_task_author_transfer.py 钉死,本文件只钉「批量」独有的三件事:

1. **全成功才生效**——任一条不行就整批拒绝,且一次写入都不许发生(作者没变、审计零条、
   通知零条)。每一种拒绝理由都各有一条用例专门验「零副作用」。
2. **置灰的理由 = 点下去会说的那句话**——候选人接口与执行接口共用同一个 author_transfer_plan,
   test_candidates_eligible_ids_are_exactly_what_transfer_accepts 是这条不变量的可执行证明。
3. **审计逐条、通知合并**——N 个任务 N 条审计(共享 batch_id),但收件人各只收一条汇总。
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.api.deps import require_task_author
from app.api.routes.tasks import (
    author_transfer_candidates,
    batch_transfer_task_author,
    grant_task_editor,
    transfer_task_author,
)
from app.api.routes.templates import create_template, publish
from app.core.exceptions import BatchRejectedError, NotFoundError, PermissionDeniedError, RubicError
from app.models.audit import ACTION_TASK_AUTHOR_TRANSFER
from app.models.permission import Permission
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER
from app.schemas.common import ParamDef
from app.schemas.team import BatchAuthorTransferIn, EditorIn, TaskAuthorIn
from app.schemas.template import PublishIn, TemplateCreateIn
from tests.conftest import max_audit_id, new_audit_rows, new_notifications, note_floor

pytestmark = pytest.mark.usefixtures("clean_credentials")

SQL = "SELECT c FROM o WHERE d = :d"

# ID 段 9280–9288
ADMIN, A_ADMIN, A_AUTHOR1, A_AUTHOR2, A_RECEIVER, A_THIRD, A_GONE, B_DEV, BIZ = (
    9280, 9281, 9282, 9283, 9284, 9285, 9286, 9287, 9288,
)


@pytest.fixture
def ds(db, datasource_factory):
    return datasource_factory("bxfer-mysql")


@pytest.fixture
def people(db, user_factory):
    gone = user_factory(A_GONE, ROLE_DEVELOPER, "甲队已停用的人", prefix="bxfer")
    if gone.is_active:
        gone.is_active = False
        db.commit()
    return SimpleNamespace(
        admin=user_factory(ADMIN, ROLE_ADMIN, "平台管理员", prefix="bxfer"),
        a_admin=user_factory(A_ADMIN, ROLE_DEVELOPER, "甲队团队管理员", prefix="bxfer"),
        author1=user_factory(A_AUTHOR1, ROLE_DEVELOPER, "甲队作者一", prefix="bxfer"),
        author2=user_factory(A_AUTHOR2, ROLE_DEVELOPER, "甲队作者二", prefix="bxfer"),
        receiver=user_factory(A_RECEIVER, ROLE_DEVELOPER, "甲队接手人", prefix="bxfer"),
        third=user_factory(A_THIRD, ROLE_DEVELOPER, "甲队第三人", prefix="bxfer"),
        gone=gone,
        b_dev=user_factory(B_DEV, ROLE_DEVELOPER, "乙队成员", prefix="bxfer"),
        biz=user_factory(BIZ, ROLE_USER, "业务使用者", prefix="bxfer"),
    )


@pytest.fixture
def teams(db, ds, people, team_factory, team_credential):
    a = team_factory(
        "bxfer-team-A",
        [
            (people.a_admin, True),
            (people.author1, False),
            (people.author2, False),
            (people.receiver, False),
            (people.third, False),
            (people.gone, False),
        ],
    )
    b = team_factory("bxfer-team-B", [(people.b_dev, True)])
    team_credential(a, ds, username="bxferA_acct")
    team_credential(b, ds, username="bxferB_acct")
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
def tasks(db, ds, people, teams):
    """甲队三个任务(两个作者一的、一个作者二的)+ 乙队一个。跨作者是批量独有的形状:
    一次交接里原作者可能不止一个,通知因此要分组。"""
    return SimpleNamespace(
        a1=_make(db, people.author1, ds, teams.a, "bxfer-甲队任务一"),
        a2=_make(db, people.author1, ds, teams.a, "bxfer-甲队任务二"),
        a3=_make(db, people.author2, ds, teams.a, "bxfer-甲队任务三"),
        b1=_make(db, people.b_dev, ds, teams.b, "bxfer-乙队任务"),
    )


def _batch(db, actor, tmpls, target):
    return batch_transfer_task_author(
        BatchAuthorTransferIn(user_id=target.id, template_ids=[t.id for t in tmpls]),
        db, actor, ip=None,
    )


def _authors(db, tmpls):
    for t in tmpls:
        db.refresh(t)
    return [t.author_id for t in tmpls]


def _assert_untouched(db, tmpls, before, *, since_audit, note_floor):
    """整批拒绝后必须一切照旧:作者没变、没写审计、没发通知。

    三项缺一不可 —— 只断言作者没变的话,一个「先发通知再校验」的实现照样能通过。
    """
    assert _authors(db, tmpls) == before
    assert new_audit_rows(db, since_audit) == []
    assert new_notifications(db, note_floor) == []


# ---------------------------------------------------------------- 成功路径


def test_batch_transfers_every_selected_task(db, people, tasks):
    all_a = [tasks.a1, tasks.a2, tasks.a3]
    out = _batch(db, people.a_admin, all_a, people.receiver)
    assert out.count == 3
    assert _authors(db, all_a) == [people.receiver.id] * 3
    assert out.to_user_id == people.receiver.id


def test_duplicate_ids_transfer_once(db, people, tasks):
    """勾重了(或前端重复提交)不该转两次、记两条审计。"""
    since = max_audit_id(db)
    out = batch_transfer_task_author(
        BatchAuthorTransferIn(
            user_id=people.receiver.id, template_ids=[tasks.a1.id, tasks.a1.id]
        ),
        db, people.a_admin, ip=None,
    )
    assert out.count == 1
    assert len(new_audit_rows(db, since)) == 1


def test_redundant_edit_grant_is_cleared_per_task(db, people, tasks):
    """接手人原先那条 edit 授权被作者身份盖住,留着就会在下次转移时静默复活。"""
    grant_task_editor(tasks.a1.id, EditorIn(user_id=people.receiver.id), db, people.a_admin, ip=None)
    since = max_audit_id(db)
    out = _batch(db, people.a_admin, [tasks.a1, tasks.a2], people.receiver)

    assert db.scalar(
        select(func.count()).select_from(Permission).where(
            Permission.subject_id == str(people.receiver.id),
            Permission.resource_id == str(tasks.a1.id),
        )
    ) == 0
    assert out.count == 2
    # 「哪几条清掉了冗余授权」的事实只在审计里(回执刻意不再回一份逐任务明细)
    by_task = {r.resource_id: r.detail["revoked_redundant_edit"] for r in new_audit_rows(db, since)}
    assert by_task == {str(tasks.a1.id): True, str(tasks.a2.id): False}


# ---------------------------------------------------------------- 整批拒绝(零副作用)


def test_cross_team_task_rejects_whole_batch(db, people, tasks):
    picked = [tasks.a1, tasks.a2, tasks.b1]
    before, since, floor = _authors(db, picked), max_audit_id(db), note_floor(db)
    with pytest.raises(BatchRejectedError) as e:
        _batch(db, people.a_admin, picked, people.receiver)
    assert "未生效" in e.value.message and tasks.b1.name in e.value.message
    _assert_untouched(db, picked, before, since_audit=since, note_floor=floor)


def test_edit_grantee_rejects_whole_batch(db, people, tasks):
    """被授予编辑权的人有 can_manage 却不能处分归属 —— 批量入口同样挡住,
    且理由要说清是「编辑权不含转移」而不是笼统的「无权」。"""
    grant_task_editor(tasks.a1.id, EditorIn(user_id=people.third.id), db, people.a_admin, ip=None)
    picked = [tasks.a1, tasks.a2]
    before, since, floor = _authors(db, picked), max_audit_id(db), note_floor(db)
    with pytest.raises(BatchRejectedError) as e:
        _batch(db, people.third, picked, people.receiver)
    assert "不含" in e.value.message
    assert {r["code"] for r in e.value.rejections} == {"no_right"}
    _assert_untouched(db, picked, before, since_audit=since, note_floor=floor)


def test_receiver_already_author_of_one_item_rejects_batch(db, people, tasks):
    picked = [tasks.a1, tasks.a3]
    before, since, floor = _authors(db, picked), max_audit_id(db), note_floor(db)
    with pytest.raises(BatchRejectedError) as e:
        _batch(db, people.a_admin, picked, people.author1)   # a1 本就是他的
    assert {r["code"] for r in e.value.rejections} == {"already_author"}
    _assert_untouched(db, picked, before, since_audit=since, note_floor=floor)


def test_inactive_receiver_rejects_batch(db, people, tasks):
    picked = [tasks.a1, tasks.a2]
    before, since, floor = _authors(db, picked), max_audit_id(db), note_floor(db)
    with pytest.raises(BatchRejectedError) as e:
        _batch(db, people.a_admin, picked, people.gone)
    assert {r["code"] for r in e.value.rejections} == {"inactive"}
    _assert_untouched(db, picked, before, since_audit=since, note_floor=floor)


def test_receiver_outside_team_rejects_batch(db, people, tasks):
    picked = [tasks.a1]
    before, since, floor = _authors(db, picked), max_audit_id(db), note_floor(db)
    with pytest.raises(BatchRejectedError) as e:
        _batch(db, people.a_admin, picked, people.b_dev)
    assert {r["code"] for r in e.value.rejections} == {"not_in_team"}
    _assert_untouched(db, picked, before, since_audit=since, note_floor=floor)


def test_orphan_task_rejects_batch_with_its_own_sentence(db, people, tasks):
    """无主任务在单任务路径上是一条 400 异常,这里是清单上的一行 —— 同一句话,两种载体。"""
    tasks.a2.team_id = None
    db.commit()
    try:
        picked = [tasks.a1, tasks.a2]
        before, since, floor = _authors(db, picked), max_audit_id(db), note_floor(db)
        with pytest.raises(BatchRejectedError) as e:
            _batch(db, people.admin, picked, people.receiver)
        assert {r["code"] for r in e.value.rejections} == {"no_team"}
        assert "还没有所属团队" in e.value.message
        _assert_untouched(db, picked, before, since_audit=since, note_floor=floor)
    finally:
        db.refresh(tasks.a2)


def test_missing_template_is_a_rejection_not_404(db, people, tasks):
    """批量的价值是一次说清所有问题:为一个已删任务回 404、把别的问题藏起来,用户要试 N 轮。"""
    with pytest.raises(BatchRejectedError) as e:
        batch_transfer_task_author(
            BatchAuthorTransferIn(
                user_id=people.receiver.id, template_ids=[tasks.a1.id, 99999999]
            ),
            db, people.a_admin, ip=None,
        )
    assert {r["code"] for r in e.value.rejections} == {"not_found"}
    db.refresh(tasks.a1)
    assert tasks.a1.author_id == people.author1.id


def test_unknown_receiver_is_404(db, people, tasks):
    """接手人是整批的前置条件,不是批里的一条。"""
    with pytest.raises(NotFoundError):
        batch_transfer_task_author(
            BatchAuthorTransferIn(user_id=99999999, template_ids=[tasks.a1.id]),
            db, people.a_admin, ip=None,
        )


def test_empty_and_oversized_selection_are_refused(db, people, tasks):
    with pytest.raises(RubicError, match="请先选择"):
        batch_transfer_task_author(
            BatchAuthorTransferIn(user_id=people.receiver.id, template_ids=[]),
            db, people.a_admin, ip=None,
        )
    with pytest.raises(RubicError, match="分批"):
        batch_transfer_task_author(
            BatchAuthorTransferIn(user_id=people.receiver.id, template_ids=list(range(1, 402))),
            db, people.a_admin, ip=None,
        )


# ---------------------------------------------------------------- 审计


def test_one_audit_row_per_task_sharing_a_batch_id(db, people, tasks):
    picked = [tasks.a1, tasks.a2, tasks.a3]
    since = max_audit_id(db)
    out = _batch(db, people.a_admin, picked, people.receiver)
    rows = new_audit_rows(db, since)

    assert len(rows) == 3
    assert {r.action for r in rows} == {ACTION_TASK_AUTHOR_TRANSFER}
    # 按任务筛得到:AuditLog.resource_id 是单值,一条汇总行会让「任务 X 发生过什么」
    # 查不到这次归属变更 —— 那正是这个动作码独立成码时写下的理由
    assert {r.resource_id for r in rows} == {str(t.id) for t in picked}
    assert {r.detail["batch_id"] for r in rows} == {out.batch_id}
    assert {r.detail["batch_size"] for r in rows} == {3}


def test_batch_audit_detail_keeps_the_single_task_shape(db, people, tasks):
    """批量那条 detail 必须是单任务那条的**超集**(只多 batch_id / batch_size),
    否则同一个动作码在审计页上会长出两种读法。"""
    since = max_audit_id(db)
    transfer_task_author(
        tasks.a1.id, TaskAuthorIn(user_id=people.receiver.id), db, people.a_admin, ip=None
    )
    single = new_audit_rows(db, since)[0].detail

    since = max_audit_id(db)
    _batch(db, people.a_admin, [tasks.a3], people.receiver)
    batched = new_audit_rows(db, since)[0].detail

    assert set(batched) - set(single) == {"batch_id", "batch_size"}
    assert set(single) - set(batched) == set()


def test_single_task_route_has_no_batch_id(db, people, tasks):
    """「detail 里有 batch_id」就是「这是一次批量交接的一部分」的判据,单条不许带。"""
    since = max_audit_id(db)
    transfer_task_author(
        tasks.a1.id, TaskAuthorIn(user_id=people.receiver.id), db, people.a_admin, ip=None
    )
    assert "batch_id" not in new_audit_rows(db, since)[0].detail


def test_initiated_as_is_computed_per_task(db, ds, people, teams, tasks):
    """一次批量里发起人身份可能逐条不同:自己的任务是「本人交接」,别人的是「代办」。"""
    own = _make(db, people.a_admin, ds, teams.a, "bxfer-团管自己的任务")
    since = max_audit_id(db)
    _batch(db, people.a_admin, [own, tasks.a1], people.receiver)
    by_task = {r.resource_id: r.detail["initiated_as"] for r in new_audit_rows(db, since)}
    assert by_task == {str(own.id): "author", str(tasks.a1.id): "team_admin"}


# ---------------------------------------------------------------- 通知(合并)


def test_notifications_are_merged_per_person(db, people, tasks):
    """3 个任务、2 个原作者 → 3 条通知(接手人 1 + 原作者各 1),不是 6 条。"""
    floor = note_floor(db)
    _batch(db, people.a_admin, [tasks.a1, tasks.a2, tasks.a3], people.receiver)
    fresh = new_notifications(db, floor)

    assert len(fresh) == 3
    by_user = {n.user_id: n for n in fresh}
    assert set(by_user) == {people.receiver.id, people.author1.id, people.author2.id}
    # 接手人那条要列全这一批,否则「你被指定为 3 个任务的作者」等于没说是哪三个
    for t in (tasks.a1, tasks.a2, tasks.a3):
        assert t.name in by_user[people.receiver.id].body
    # 原作者那条只列他自己的两个
    assert tasks.a3.name not in by_user[people.author1.id].body


def test_inactive_old_author_group_is_skipped(db, people, tasks):
    people.author1.is_active = False
    db.commit()
    try:
        floor = note_floor(db)
        _batch(db, people.a_admin, [tasks.a1, tasks.a3], people.receiver)
        assert {n.user_id for n in new_notifications(db, floor)} == {
            people.receiver.id, people.author2.id
        }
    finally:
        people.author1.is_active = True
        db.commit()


def test_single_selection_reuses_the_single_task_wording(db, people, tasks):
    """批量入口只勾了一个时,收到的通知应与从 ⋮ 菜单转逐字一致,而不是第二套措辞。"""
    floor = note_floor(db)
    _batch(db, people.a_admin, [tasks.a1], people.receiver)
    titles = {n.user_id: n.title for n in new_notifications(db, floor)}
    assert titles[people.receiver.id] == "你被指定为任务作者"
    assert titles[people.author1.id] == "你的任务已移交他人"


def test_notification_failure_does_not_undo_the_batch(db, people, tasks, monkeypatch):
    """业务已 commit,此时让异常冒上去只会让前端重试,而重试会撞上「已经是作者」——
    用户看到的是「第一次失败、第二次说我早就转过了」,比不发通知糟得多。"""
    from app.services import notify_service

    def boom(*a, **kw):
        raise RuntimeError("飞书炸了")

    monkeypatch.setattr(notify_service, "notify_author_transferred_batch", boom)
    out = _batch(db, people.a_admin, [tasks.a1, tasks.a3], people.receiver)
    assert out.count == 2
    assert _authors(db, [tasks.a1, tasks.a3]) == [people.receiver.id] * 2


# ---------------------------------------------------------------- 候选人接口(先选人那一步)


def _candidate(db, actor, user_id):
    out = author_transfer_candidates(db, actor)
    return out, next((c for c in out.candidates if c.user_id == user_id), None)


def _ids(groups):
    return {tid for g in groups for tid in g.template_ids}


def test_candidates_eligible_ids_are_exactly_what_transfer_accepts(db, people, tasks):
    """**本文件最关键的一条**:前端置灰所依据的集合,与执行接口放行的集合必须逐一相等。

    候选接口与执行接口共用同一个 author_transfer_plan,这条用例就是那个共用的可执行证明 ——
    规则若长出第二份表述,症状是「下拉里选得到、点了报错」。
    """
    out, cand = _candidate(db, people.a_admin, people.receiver.id)
    assert cand is not None

    # ① eligible 整批提交必须全部成功
    res = batch_transfer_task_author(
        BatchAuthorTransferIn(
            user_id=people.receiver.id, template_ids=cand.eligible_template_ids
        ),
        db, people.a_admin, ip=None,
    )
    assert res.count == len(cand.eligible_template_ids)

    # ② 三截互不相交:顶层 blocked(与接手人无关)、他的 eligible、他的 blocked。
    #    而「eligible ⊎ 他的 blocked」对每个候选人都是同一个集合 —— 我有处分权的那些
    base = _ids(out.blocked)
    mine = set(cand.eligible_template_ids) | _ids(cand.blocked)
    assert set(cand.eligible_template_ids) & _ids(cand.blocked) == set()
    assert base & mine == set()
    for other in out.candidates:
        assert set(other.eligible_template_ids) | _ids(other.blocked) == mine


def test_blocked_reason_is_the_same_sentence_transfer_would_say(db, people, tasks):
    """置灰时显示的那句话,与点下去会说的那句话,来自同一次调用。

    注意 already_author 的话里带着任务名,所以它天然是「一个任务一组」;按团队说的
    not_in_team 才会把一批任务归并成一组。分组键是 (code, 整句话),两种形状都容得下。
    """
    _, cand = _candidate(db, people.a_admin, people.author1.id)
    group = next(g for g in cand.blocked if tasks.a1.id in g.template_ids)
    assert group.code == "already_author"
    with pytest.raises(BatchRejectedError) as e:
        _batch(db, people.a_admin, [tasks.a1], people.author1)
    assert e.value.rejections[0]["message"] == group.message


def test_candidates_are_narrowed_by_transfer_right(db, people, tasks):
    """普通成员一个都处分不了 → 没有候选人,且他看得见的任务全进顶层 blocked
    (批量视角下没有「那一个任务」,守卫退化成逐行收窄,不是 403)。

    业务用户则连门都进不来 —— 结果注定为空,不必把团队成员名单交给他。后者是路由的
    Depends,直接调函数绕不过去,故单独验那个守卫本身。
    """
    out = author_transfer_candidates(db, people.third)
    assert out.candidates == []
    # 理由由服务端给,前端原样显示;这里只确认它确实说了话、且盖住了他看得见的任务
    assert {g.code for g in out.blocked} == {"no_right"}
    assert tasks.a1.id in _ids(out.blocked)

    with pytest.raises(PermissionDeniedError):
        require_task_author(people.biz)


def test_inactive_members_are_not_candidates(db, people, tasks):
    """离职清理只置 is_active、不删 TeamMember 行 —— 名单里还在,但接不了活。"""
    out = author_transfer_candidates(db, people.a_admin)
    assert people.gone.id not in {c.user_id for c in out.candidates}

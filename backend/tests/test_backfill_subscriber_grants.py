"""存量代订阅者补齐 view/run/download(app.backfill_subscriber_grants)。

造的是「改口径之前」的形状:代订阅行(added_by 非空)+ 只有一条 view 授权。
"""
import pytest
from sqlalchemy import select

from app import backfill_subscriber_grants as bf
from app.models import audit as A
from app.models.permission import (
    ACTION_DOWNLOAD,
    ACTION_RUN,
    ACTION_VIEW,
    RESOURCE_TEMPLATE,
    SUBJECT_USER,
    Permission,
)
from app.models.subscription import TaskSubscription
from app.models.user import ROLE_DEVELOPER, ROLE_USER
from app.services import permission_service
from tests.conftest import max_audit_id, new_audit_rows

# ID 段 9230–9234
AUTHOR, MEMBER, PROXIED, SELF_SUB = 9230, 9231, 9232, 9233


@pytest.fixture
def author(user_factory):
    return user_factory(AUTHOR, ROLE_DEVELOPER, "补授作者", prefix="bfg")


@pytest.fixture
def member(user_factory):
    return user_factory(MEMBER, ROLE_DEVELOPER, "补授队员", prefix="bfg")


@pytest.fixture
def proxied(user_factory):
    """早先被代订阅、只拿到 view 的业务方 —— 本脚本要补的人。"""
    return user_factory(PROXIED, ROLE_USER, "补授代订阅者", prefix="bfg")


@pytest.fixture
def self_sub(user_factory):
    """手工只授了 view、自己订阅的人 —— 不是代订阅来的,脚本不动。"""
    return user_factory(SELF_SUB, ROLE_USER, "补授自助订阅者", prefix="bfg")


@pytest.fixture
def tmpl(db, author, member, proxied, self_sub, datasource_factory, team_factory,
         team_credential, subscribed_task_factory):
    ds = datasource_factory("bfg-mysql")
    team = team_factory("bfg-team", [(author, True), (member, False)])
    team_credential(team, ds, username="bfg_team_acct")
    t = subscribed_task_factory(author, ds, team, name="bfg-任务")
    for u in (proxied, self_sub):
        permission_service.grant(
            db, subject_type=SUBJECT_USER, subject_id=str(u.id),
            resource_type=RESOURCE_TEMPLATE, resource_id=str(t.id),
            actions=[ACTION_VIEW], granted_by=author.id,
        )
    db.add_all([
        TaskSubscription(template_id=t.id, user_id=proxied.id, added_by=author.id),
        TaskSubscription(template_id=t.id, user_id=member.id, added_by=author.id),
        TaskSubscription(template_id=t.id, user_id=self_sub.id, added_by=None),
    ])
    db.commit()
    return t


def _actions(db, tmpl, user) -> set[str]:
    return set(
        db.scalars(
            select(Permission.action).where(
                Permission.subject_id == str(user.id),
                Permission.resource_type == RESOURCE_TEMPLATE,
                Permission.resource_id == str(tmpl.id),
            )
        )
    )


def test_find_gaps_targets_only_proxied_outsiders(db, tmpl, author, proxied):
    gaps = bf.find_gaps(db, template_id=tmpl.id)

    # 团队成员(身份自带权限)与自助订阅者都不在名单里
    assert [(g.user_id, g.missing, g.added_by) for g in gaps] == [
        (proxied.id, [ACTION_RUN, ACTION_DOWNLOAD], author.id)
    ]


def test_dry_run_writes_nothing(db, tmpl, proxied):
    bf.find_gaps(db, template_id=tmpl.id)
    assert _actions(db, tmpl, proxied) == {ACTION_VIEW}


def test_apply_fills_gaps_with_audit_and_is_idempotent(db, tmpl, author, member, proxied, self_sub):
    floor = max_audit_id(db)

    n = bf.apply_gaps(db, bf.find_gaps(db, template_id=tmpl.id))

    assert n == 2
    assert _actions(db, tmpl, proxied) == {ACTION_VIEW, ACTION_RUN, ACTION_DOWNLOAD}
    assert _actions(db, tmpl, self_sub) == {ACTION_VIEW}
    assert _actions(db, tmpl, member) == set()
    rows = [r for r in new_audit_rows(db, floor) if r.action == A.ACTION_PERMISSION_GRANT]
    assert len(rows) == 1
    assert rows[0].detail["via"] == bf.VIA
    assert rows[0].detail["actions_created"] == [ACTION_RUN, ACTION_DOWNLOAD]
    granted_by = set(
        db.scalars(
            select(Permission.granted_by).where(
                Permission.subject_id == str(proxied.id),
                Permission.resource_id == str(tmpl.id),
                Permission.action.in_([ACTION_RUN, ACTION_DOWNLOAD]),
            )
        )
    )
    assert granted_by == {author.id}

    # 再跑一次:没有缺口了
    assert bf.find_gaps(db, template_id=tmpl.id) == []

"""授权判定:管理员全通、作者对自有模板全通、按用户授权命中、无授权拒绝。"""
from app.models.datasource import DataSource
from app.models.permission import ACTION_RUN, ACTION_VIEW, RESOURCE_TEMPLATE, SUBJECT_USER
from app.models.template import SqlTemplate
from app.models.user import ROLE_ADMIN, ROLE_USER, User
from app.services import permission_service as ps


def _mk_user(db, uid, role=ROLE_USER):
    u = User(id=uid, feishu_open_id=f"ou_{uid}", name=f"u{uid}", role=role)
    db.add(u)
    db.flush()
    return u


def _mk_template(db, author_id):
    ds = DataSource(name=f"ds_{author_id}", engine="mysql", host="h", port=3306, username="u")
    db.add(ds)
    db.flush()
    t = SqlTemplate(name=f"t_{author_id}", datasource_id=ds.id, dialect="mysql", author_id=author_id)
    db.add(t)
    db.flush()
    return t


def test_admin_can_everything(db):
    admin = _mk_user(db, 1001, ROLE_ADMIN)
    t = _mk_template(db, author_id=9999)
    assert ps.can(db, admin, ACTION_RUN, RESOURCE_TEMPLATE, t.id) is True


def test_author_can_own_template(db):
    author = _mk_user(db, 1002)
    t = _mk_template(db, author_id=author.id)
    assert ps.can(db, author, ACTION_RUN, RESOURCE_TEMPLATE, t.id) is True


def test_granted_user_can_run(db):
    author = _mk_user(db, 1003)
    grantee = _mk_user(db, 1004)
    t = _mk_template(db, author_id=author.id)
    ps.grant(
        db, subject_type=SUBJECT_USER, subject_id=str(grantee.id),
        resource_type=RESOURCE_TEMPLATE, resource_id=str(t.id),
        actions=[ACTION_RUN], granted_by=author.id,
    )
    assert ps.can(db, grantee, ACTION_RUN, RESOURCE_TEMPLATE, t.id) is True
    # 未授予的动作不放行
    assert ps.can(db, grantee, ACTION_VIEW, RESOURCE_TEMPLATE, t.id) is False


def test_stranger_denied(db):
    author = _mk_user(db, 1005)
    stranger = _mk_user(db, 1006)
    t = _mk_template(db, author_id=author.id)
    assert ps.can(db, stranger, ACTION_RUN, RESOURCE_TEMPLATE, t.id) is False


def test_action_template_ids_admin_is_none(db):
    admin = _mk_user(db, 1007, ROLE_ADMIN)
    assert ps.action_template_ids(db, admin, ACTION_VIEW) is None

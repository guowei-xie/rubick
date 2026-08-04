"""根因修复:授权对象在「真正授权」时才按 open_id 解析/落库(搜索不再造壳用户)。

说明:User.id 是 BIGINT 主键,SQLite 下不做 rowid 自增(与 mock_login / lookup 的
`User(...)` 无 id 插入同源),故「全新用户在授权时首次建行」属 MySQL 路径,不在此单测。
这里覆盖 SQLite 可验证的核心保证:按 open_id 解析到既有用户、授权落到其 id、幂等。
"""
from sqlalchemy import select

from app.api.routes.permissions import grant as grant_route
from app.models.permission import Permission
from app.models.user import ROLE_ADMIN, User
from app.schemas.permission import GrantIn


def test_grant_resolves_subject_by_open_id(db):
    admin = User(id=2001, feishu_open_id="ou_admin_grant", name="a", role=ROLE_ADMIN)
    # 既有壳用户(无登录):模拟此前已授权过、库里已有此人
    shell = User(id=2050, feishu_open_id="ou_shell", name="旧名", role="user")
    db.add_all([admin, shell])
    db.flush()

    # 授权时传 open_id(+ 最新资料),应解析到既有用户、不新建、并顺带合并资料
    out = grant_route(
        GrantIn(subject_open_id="ou_shell", subject_name="新名", resource_id="55"),
        db, admin, ip=None,
    )
    assert out and all(p.subject_id == "2050" for p in out)
    # 未新增用户行,且资料被合并更新
    assert len(db.scalars(select(User).where(User.feishu_open_id == "ou_shell")).all()) == 1
    assert db.get(User, 2050).name == "新名"

    perms = db.scalars(select(Permission).where(Permission.subject_id == "2050")).all()
    assert {p.action for p in perms} == {"view", "run", "download"}

    # 幂等:再次授权同一人不重复授权行
    grant_route(GrantIn(subject_open_id="ou_shell", resource_id="55"), db, admin, ip=None)
    assert len(db.scalars(select(Permission).where(Permission.subject_id == "2050")).all()) == 3


def test_grant_still_accepts_known_subject_id(db):
    """兼容老路径:已知平台用户直接用 subject_id 授权。"""
    admin = User(id=2002, feishu_open_id="ou_admin_grant2", name="a", role=ROLE_ADMIN)
    grantee = User(id=2003, feishu_open_id="ou_known", name="老用户", role="user")
    db.add_all([admin, grantee])
    db.flush()

    out = grant_route(
        GrantIn(subject_id=str(grantee.id), resource_id="66", actions=["view"]),
        db, admin, ip=None,
    )
    assert out and out[0].subject_id == "2003"

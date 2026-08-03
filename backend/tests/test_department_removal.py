"""取消部门级授权:授权 schema 只接受个人主体 + 迁移(清部门权限行 / 删部门 schema)幂等。"""
import pytest
from pydantic import ValidationError
from sqlalchemy import inspect as sa_inspect, text

from app.core.database import engine, tbl
from app.migrate import _drop_department_schema, _purge_department_permissions
from app.models.permission import Permission
from app.models.user import User
from app.schemas.permission import GrantIn


# ---- 授权入参 schema:仅个人 ----

def test_grantin_defaults_to_user():
    g = GrantIn(subject_id="1", resource_id="1")
    assert g.subject_type == "user"


def test_grantin_rejects_department():
    with pytest.raises(ValidationError):
        GrantIn(subject_type="department", subject_id="1", resource_id="1")


# ---- 迁移:清除部门授权行(幂等) ----

def _count(conn, where: str) -> int:
    return conn.execute(text(f"SELECT COUNT(*) FROM {tbl('permissions')} WHERE {where}")).scalar()


def test_purge_department_permissions_idempotent(db):
    db.add_all([
        Permission(subject_type="department", subject_id="1", resource_type="template", resource_id="1", action="view"),
        Permission(subject_type="department", subject_id="2", resource_type="template", resource_id="1", action="run"),
        Permission(subject_type="user", subject_id="900", resource_type="template", resource_id="1", action="view"),
    ])
    db.commit()
    db.close()  # 迁移用自己的 Session/事务

    _purge_department_permissions()
    with engine.begin() as conn:
        assert _count(conn, "subject_type = 'department'") == 0
        assert _count(conn, "subject_type = 'user' AND subject_id = '900'") == 1

    # 幂等:再跑一次不报错,部门行仍为 0
    _purge_department_permissions()
    with engine.begin() as conn:
        assert _count(conn, "subject_type = 'department'") == 0


# ---- 迁移:删除部门 schema(表 + 列),幂等,且保留用户数据 ----

def test_drop_department_schema_idempotent(db):
    users, depts = tbl("users"), tbl("departments")

    # 造一个用户(模型已无 department_id)
    u = User(id=7001, feishu_open_id="ou_dept_mig", name="mig", role="user")
    db.add(u)
    db.commit()
    db.close()

    # 模拟「旧 schema」:补回 departments 表 + users.department_id 列并填值
    with engine.begin() as conn:
        conn.execute(text(f"CREATE TABLE IF NOT EXISTS {depts} (id INTEGER PRIMARY KEY, name VARCHAR(128))"))
        conn.execute(text(f"ALTER TABLE {users} ADD COLUMN department_id BIGINT"))
        conn.execute(text(f"UPDATE {users} SET department_id = 5 WHERE id = 7001"))
    assert "department_id" in {c["name"] for c in sa_inspect(engine).get_columns(users)}

    _drop_department_schema()

    cols = {c["name"] for c in sa_inspect(engine).get_columns(users)}
    assert "department_id" not in cols
    assert not sa_inspect(engine).has_table(depts)
    # 用户数据在重建中保留
    with engine.begin() as conn:
        assert conn.execute(text(f"SELECT name FROM {users} WHERE id = 7001")).scalar() == "mig"

    # 幂等:列/表已不存在,再跑一次不报错、无副作用
    _drop_department_schema()
    assert "department_id" not in {c["name"] for c in sa_inspect(engine).get_columns(users)}
    assert not sa_inspect(engine).has_table(depts)

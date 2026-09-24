"""清壳 + 用户 id 重排脚本(app.renumber_users)的验证。

模拟「实际几人却因授权搜通讯录刷出一堆壳用户、id 虚高」的场景:
  - 登录用户按 created_at 占 1..K;
  - 无登录但承载授权/被引用的壳用户保留、排在登录用户之后;
  - 无登录且无任何引用的孤儿壳用户被删除;
  - 所有引用 user.id 的表(真实外键 + 软引用 + permissions.subject_id 字符串)同步改写;
  - 自增计数器归位。
"""
from sqlalchemy import text

import app.models  # noqa: F401  注册所有模型(供元数据护栏遍历)
from app.core.database import Base, engine, tbl
from app.models.user import User
from app.renumber_users import NUMERIC_REFS, apply_plan, build_plan, reset_counter


def test_numeric_refs_cover_all_user_foreign_keys():
    """护栏:凡在模型里声明了指向 users.id 的外键,都必须在 NUMERIC_REFS 里,
    否则重排改号时会漏改该引用。软引用(无 FK 声明)仍需人工维护,不在此护栏内。"""
    covered = {(tbl(t), c) for t, c in NUMERIC_REFS}
    missing = [
        f"{table.name}.{col.name}"
        for table in Base.metadata.tables.values()
        for col in table.columns
        for fk in col.foreign_keys
        if fk.column.table is User.__table__ and (table.name, col.name) not in covered
    ]
    assert not missing, f"这些指向 users.id 的外键未纳入 NUMERIC_REFS:{missing}"

_TS = "2026-06-01 00:00:00"

# 被本测试触碰的表(每次测试前后清空,避免与其他测试互相污染)
_SEEDED = [
    "users", "permissions", "query_jobs", "sql_templates",
    "template_versions", "audit_logs", "download_events", "notifications",
]


def _dependents(names: list[str]) -> list[str]:
    """清某张表就得连「外键挂在它上面」的表一起清(传递闭包),且子表先删。

    手抄清单会漏:SQLite 会复用被删掉的最大 id,留下的孤儿订阅会被后面某个测试新建的
    同号任务「继承」,凭空多出几个订阅者。从元数据推导,加新表时不必记得回来改这里。
    """
    wanted = {tbl(n) for n in names}
    grew = True
    while grew:
        grew = False
        for t in Base.metadata.tables.values():
            if t.name not in wanted and any(fk.column.table.name in wanted for fk in t.foreign_keys):
                wanted.add(t.name)
                grew = True
    # sorted_tables 是父先子后,倒过来删
    return [t.name for t in reversed(Base.metadata.sorted_tables) if t.name in wanted]


_TABLES = _dependents(_SEEDED)


def _wipe(conn):
    for t in _TABLES:
        conn.execute(text(f"DELETE FROM {t}"))


def _user(conn, uid, open_id, created, *, login):
    conn.execute(
        text(
            f"INSERT INTO {tbl('users')} "
            "(id, feishu_open_id, name, role, is_active, created_at, updated_at, last_login_at) "
            "VALUES (:id, :oid, :name, 'user', 1, :c, :c, :ll)"
        ),
        {"id": uid, "oid": open_id, "name": open_id, "c": created, "ll": _TS if login else None},
    )


def _seed(conn):
    _wipe(conn)
    # 3 个登录用户(注册先后:a<b<c)
    _user(conn, 5, "ou_a", "2026-01-01", login=True)
    _user(conn, 100, "ou_b", "2026-02-01", login=True)
    _user(conn, 263, "ou_c", "2026-03-01", login=True)
    # 2 个无登录但被引用的壳用户(保留)
    _user(conn, 50, "ou_shell_perm", "2026-01-15", login=False)   # 被 permission.subject_id 引用
    _user(conn, 77, "ou_shell_job", "2026-01-20", login=False)    # 被 query_jobs.user_id 引用
    # 2 个无登录且无引用的孤儿(删除)
    _user(conn, 200, "ou_orphan1", "2026-01-10", login=False)
    _user(conn, 201, "ou_orphan2", "2026-01-11", login=False)

    # 引用:覆盖 字符串主体 / granted_by / 真实外键 / 软引用
    conn.execute(text(
        f"INSERT INTO {tbl('permissions')} "
        "(subject_type, subject_id, resource_type, resource_id, action, granted_by, created_at, updated_at) "
        "VALUES ('user', '50', 'template', '1', 'view', 100, :c, :c)"
    ), {"c": _TS})
    conn.execute(text(
        f"INSERT INTO {tbl('query_jobs')} "
        "(id, user_id, template_id, datasource_id, params, status, source, created_at, updated_at) "
        "VALUES (1, 77, 1, 1, '{}', 'success', 'run', :c, :c)"
    ), {"c": _TS})
    conn.execute(text(
        f"INSERT INTO {tbl('sql_templates')} "
        "(id, name, datasource_id, dialect, status, author_id, tags, created_at, updated_at) "
        "VALUES (1, 't', 1, 'mysql', 'draft', 100, '[]', :c, :c)"
    ), {"c": _TS})
    conn.execute(text(
        f"INSERT INTO {tbl('audit_logs')} "
        "(id, user_id, action, detail, created_at, updated_at) VALUES (1, 263, 'login', '{}', :c, :c)"
    ), {"c": _TS})
    conn.execute(text(
        f"INSERT INTO {tbl('notifications')} "
        "(id, user_id, title, level, is_read, feishu_sent, created_at, updated_at) "
        "VALUES (1, 5, 'hi', 'info', 0, 0, :c, :c)"
    ), {"c": _TS})


def _uid(conn, open_id):
    return conn.execute(
        text(f"SELECT id FROM {tbl('users')} WHERE feishu_open_id = :o"), {"o": open_id}
    ).scalar()


def _scalar(conn, sql):
    return conn.execute(text(sql)).scalar()


def test_build_plan_is_pure_read_and_correct():
    with engine.begin() as conn:
        _seed(conn)
    with engine.connect() as conn:
        plan = build_plan(conn)
        # 登录用户按 created_at 占 1..3,壳用户排其后占 4..5
        assert plan.mapping == {5: 1, 100: 2, 263: 3, 50: 4, 77: 5}
        assert plan.orphans == [200, 201]
        assert plan.referenced_shells == [50, 77]
        assert plan.next_id == 6
        # dry-run(仅 build_plan)不写库:总行数仍为 7
        assert _scalar(conn, f"SELECT COUNT(*) FROM {tbl('users')}") == 7
    with engine.begin() as conn:
        _wipe(conn)


def test_apply_renumbers_and_rewrites_all_references():
    with engine.begin() as conn:
        _seed(conn)
        plan = build_plan(conn)
    with engine.begin() as conn:
        apply_plan(conn, plan)
    with engine.begin() as conn:
        reset_counter(conn, plan)

    with engine.connect() as conn:
        # 孤儿删除,存活重排为连续 1..5
        ids = [r for (r,) in conn.execute(text(f"SELECT id FROM {tbl('users')} ORDER BY id"))]
        assert ids == [1, 2, 3, 4, 5]
        assert _uid(conn, "ou_a") == 1
        assert _uid(conn, "ou_b") == 2
        assert _uid(conn, "ou_c") == 3
        assert _uid(conn, "ou_shell_perm") == 4
        assert _uid(conn, "ou_shell_job") == 5
        assert _uid(conn, "ou_orphan1") is None
        assert _uid(conn, "ou_orphan2") is None

        # 引用同步改写
        assert _scalar(conn, f"SELECT subject_id FROM {tbl('permissions')} WHERE subject_type='user'") == "4"
        assert _scalar(conn, f"SELECT granted_by FROM {tbl('permissions')}") == 2
        assert _scalar(conn, f"SELECT user_id FROM {tbl('query_jobs')}") == 5
        assert _scalar(conn, f"SELECT author_id FROM {tbl('sql_templates')}") == 2
        assert _scalar(conn, f"SELECT user_id FROM {tbl('audit_logs')}") == 3
        assert _scalar(conn, f"SELECT user_id FROM {tbl('notifications')}") == 1

        # 幂等:已连续,再算计划应为恒等且无孤儿
        again = build_plan(conn)
        assert again.mapping == {1: 1, 2: 2, 3: 3, 4: 4, 5: 5}
        assert again.orphans == []

    with engine.begin() as conn:
        _wipe(conn)

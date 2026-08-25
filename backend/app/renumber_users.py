"""一次性:清理孤儿壳用户 + 把真实用户按注册顺序重排为 1..N,并归位自增计数器。

背景:授权选人搜通讯录会为每个命中者 upsert 一条「壳用户」(无 last_login,不显示在
用户管理),每条占用一个自增 ID,ID 因而虚高(如实际 5 人却涨到 263)。本脚本:

  1. 删除「无登录且无任何引用」的孤儿壳用户;
  2. 把存活用户(登录用户优先、再按 created_at)重排为连续 1..N;
  3. 同步改写所有引用 user.id 的表(真实外键 + 软引用 + permissions.subject_id 字符串);
  4. 归位自增计数器,使下一个新用户从 N+1 开始。

**这是破坏性、一次性脚本**,不接入 deploy.sh / migrate.py 的常规路径。安全要求:
  - 先在本地 SQLite 验证,再在部署窗口对线上 MySQL 执行;执行前先备份相关表。
  - 执行后**务必轮换 JWT_SECRET**(config.ini):id 是 JWT 的 sub,旧 token 会指向
    不同的人——不轮换即安全隐患。5 个用户重新登录成本可忽略。

用法(在 backend 目录):
    python -m app.renumber_users            # dry-run:只打印计划,不写库(默认)
    python -m app.renumber_users --apply    # 实际执行
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

from sqlalchemy import text

import app.models  # noqa: F401  注册所有模型
from app.core.database import engine, tbl

# 引用 user.id 的数值列(真实外键 + 无外键软引用):(逻辑表名, 列名)
NUMERIC_REFS: list[tuple[str, str]] = [
    ("query_jobs", "user_id"),          # 真实外键
    ("sql_templates", "author_id"),     # 真实外键
    ("template_versions", "author_id"), # 真实外键
    ("template_versions", "accepted_by"),
    ("audit_logs", "user_id"),
    ("download_events", "user_id"),
    ("notifications", "user_id"),
    ("permissions", "granted_by"),
    ("team_members", "user_id"),        # 真实外键(团队成员)
    ("task_subscriptions", "user_id"),  # 真实外键(任务订阅者)
    # 软引用(无外键)
    ("teams", "created_by"),                          # 建团队的平台管理员
    ("team_members", "added_by"),                      # 谁把该成员加进来的
    ("team_datasource_credentials", "updated_by"),     # 上次改团队账号的人
    ("template_enum_values", "updated_by"),            # 谁触发的那次候选值更新
    ("task_schedules", "updated_by"),                  # 最后修改订阅计划的人
    ("task_subscription_events", "user_id"),           # 订阅留痕的当事人
    ("task_subscription_events", "operator_id"),       # 订阅留痕的触发者
]

# permissions.subject_id 是 VARCHAR,存的是 user.id 的字符串,仅 subject_type='user' 且为数字时才是用户引用
STRING_REF_TABLE = "permissions"
STRING_REF_COL = "subject_id"
STRING_REF_WHERE = "subject_type = 'user'"


@dataclass
class Plan:
    mapping: dict[int, int]          # 存活用户 old_id -> new_id(1..N)
    orphans: list[int]               # 待删除的孤儿壳用户 id
    referenced_shells: list[int]     # 无登录但被引用、故保留的壳用户 old_id
    next_id: int                     # 归位后的下一个自增值(N+1)


def _referenced_user_ids(conn) -> set[int]:
    """收集所有被引用到的 user.id(数值列去重 + subject_id 数字字符串)。"""
    ids: set[int] = set()
    for table, col in NUMERIC_REFS:
        rows = conn.execute(
            text(f"SELECT DISTINCT {col} FROM {tbl(table)} WHERE {col} IS NOT NULL")
        ).scalars()
        ids.update(int(v) for v in rows if v is not None)
    subs = conn.execute(
        text(
            f"SELECT DISTINCT {STRING_REF_COL} FROM {tbl(STRING_REF_TABLE)} "
            f"WHERE {STRING_REF_WHERE} AND {STRING_REF_COL} IS NOT NULL"
        )
    ).scalars()
    ids.update(int(s) for s in subs if s and str(s).isdigit())
    return ids


def build_plan(conn) -> Plan:
    """基于当前库状态算出重排计划(纯读,不写)。"""
    users = conn.execute(
        text(f"SELECT id, created_at, last_login_at FROM {tbl('users')}")
    ).mappings().all()
    referenced = _referenced_user_ids(conn)

    survivors = []  # (has_login, created_at, id)
    orphans: list[int] = []
    referenced_shells: list[int] = []
    for u in users:
        uid = int(u["id"])
        has_login = u["last_login_at"] is not None
        if has_login or uid in referenced:
            survivors.append((has_login, u["created_at"], uid))
            if not has_login:
                referenced_shells.append(uid)
        else:
            orphans.append(uid)

    # 排序:登录用户优先(has_login=True 排前),再按 created_at,再按原 id 稳定兜底。
    # created_at 可能为 None,统一转成可比较的空串排最前。
    survivors.sort(key=lambda t: (not t[0], str(t[1] or ""), t[2]))
    mapping = {uid: new_id for new_id, (_, _, uid) in enumerate(survivors, start=1)}

    return Plan(
        mapping=mapping,
        orphans=sorted(orphans),
        referenced_shells=sorted(referenced_shells),
        next_id=len(survivors) + 1,
    )


def _lit(v, quote: bool) -> str:
    return f"'{v}'" if quote else str(int(v))


def _case_update(conn, table: str, col: str, remap: dict[int, int], *, quote: bool, where_extra: str = "") -> None:
    """按 remap(old->new)对 {table}.{col} 批量改写。id 均为整数,内联字面值安全。"""
    if not remap:
        return
    whens = " ".join(f"WHEN {_lit(o, quote)} THEN {_lit(n, quote)}" for o, n in remap.items())
    keys = ", ".join(_lit(o, quote) for o in remap)
    sql = f"UPDATE {table} SET {col} = CASE {col} {whens} END WHERE {col} IN ({keys})"
    if where_extra:
        sql += f" AND {where_extra}"
    conn.execute(text(sql))


def _remap_all(conn, remap: dict[int, int]) -> None:
    """把 user.id 主键及其所有引用列按 remap 改写(单段位移,调用方保证目标区间无冲突)。"""
    _case_update(conn, tbl("users"), "id", remap, quote=False)
    for table, col in NUMERIC_REFS:
        _case_update(conn, tbl(table), col, remap, quote=False)
    _case_update(conn, tbl(STRING_REF_TABLE), STRING_REF_COL, remap, quote=True, where_extra=STRING_REF_WHERE)


def print_plan(plan: Plan) -> None:
    n = len(plan.mapping)
    print(f"[renumber] 存活用户 {n} 人,孤儿壳用户 {len(plan.orphans)} 个待删除。")
    if plan.referenced_shells:
        print(f"[renumber] 其中 {len(plan.referenced_shells)} 个是「无登录但承载授权/引用」的壳用户,予以保留:"
              f"{plan.referenced_shells}")
    changed = {o: nw for o, nw in plan.mapping.items() if o != nw}
    if changed:
        print(f"[renumber] id 变更映射({len(changed)} 条 old -> new):")
        for old in sorted(changed):
            print(f"    {old:>6} -> {plan.mapping[old]}")
    else:
        print("[renumber] 现有 id 已是连续 1..N,无需改号。")
    if plan.orphans:
        print(f"[renumber] 待删除孤儿 id:{plan.orphans}")
    print(f"[renumber] 自增计数器将归位到 {plan.next_id}(下一个新用户 id)。")


def apply_plan(conn, plan: Plan) -> None:
    """删孤儿 + 两段位移改号。conn 需处于事务中(由调用方 engine.begin() 提供)。
    不含计数器归位(见 reset_counter)——MySQL 的 ALTER 是 DDL、含隐式提交,须独立事务。"""
    users = tbl("users")

    # 1. 删除孤儿壳用户
    if plan.orphans:
        keys = ", ".join(str(int(i)) for i in plan.orphans)
        conn.execute(text(f"DELETE FROM {users} WHERE id IN ({keys})"))

    # 2. 两段位移避免主键瞬时冲突:old -> OFFSET+new,再 OFFSET+new -> new
    max_id = conn.execute(text(f"SELECT COALESCE(MAX(id), 0) FROM {users}")).scalar() or 0
    offset = int(max_id) + 1000
    _remap_all(conn, {old: offset + new for old, new in plan.mapping.items()})
    _remap_all(conn, {offset + new: new for new in plan.mapping.values()})


def reset_counter(conn, plan: Plan) -> None:
    """把自增计数器归位到 next_id,使下一个新用户从此开始。"""
    users = tbl("users")
    dialect = engine.dialect.name
    if dialect == "mysql":
        conn.execute(text(f"ALTER TABLE {users} AUTO_INCREMENT = {plan.next_id}"))
    elif dialect == "sqlite":
        # sqlite_sequence 仅在存在 AUTOINCREMENT 表时才建;本库主键为 BIGINT、无该表,
        # SQLite 自行按 rowid 递增即可。存在时才回填(本地验证足够,线上是 MySQL)。
        has_seq = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
        ).first()
        if has_seq:
            conn.exec_driver_sql(
                f"UPDATE sqlite_sequence SET seq = {plan.next_id - 1} WHERE name = '{users}'"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="清理孤儿壳用户并把用户 id 重排为连续 1..N")
    parser.add_argument("--apply", action="store_true", help="实际执行(默认仅 dry-run 打印计划)")
    args = parser.parse_args()

    print(f"[renumber] 目标库:{engine.url}")
    with engine.connect() as conn:
        plan = build_plan(conn)
        print_plan(plan)

        changed = any(o != nw for o, nw in plan.mapping.items())
        if not changed and not plan.orphans:
            print("[renumber] 无需处理,退出。")
            return

        if not args.apply:
            print("[renumber] dry-run 结束,未写库。确认无误后加 --apply 执行。")
            return

    # 数据改写:单事务;MySQL 内临时关外键校验以便改主键与引用(SQLite 默认不强制外键)。
    dialect = engine.dialect.name
    with engine.begin() as conn:
        if dialect == "mysql":
            conn.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
        apply_plan(conn, plan)
        if dialect == "mysql":
            conn.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")

    # 计数器归位:独立事务(MySQL ALTER 含隐式提交,不能混在上面的事务里)。
    with engine.begin() as conn:
        reset_counter(conn, plan)

    print("[renumber] 完成。请记得轮换 config.ini 的 JWT_SECRET 并重启,让所有旧登录态失效。")


if __name__ == "__main__":
    main()

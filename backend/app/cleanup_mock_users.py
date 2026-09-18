"""一次性:从库里清除 mock 登录造出来的假账号(ou_admin / ou_analyst / ou_dev / ou_viewer)及其残留。

背景:MOCK_AUTH=true 时登录页可直接以固定 open_id 登入(见 frontend LoginPage 的 MOCK_USERS)。
本地开发若把 DATABASE_URL 指向线上库,这些假账号就会落进正式库 —— bitest 已经这样被污染:
假账号不仅出现在用户管理列表里,还带出一条归档任务、一条团队成员身份和若干 mock 登录审计。
本脚本按 open_id 精确定位这批账号,连同**只属于它们**的残留一起删掉。

删什么(全部按「引用面」推导,不写死 id):
  1. users 里 open_id 命中 MOCK_OPEN_IDS 的行;
  2. 这些账号名下的任务(sql_templates.author_id)及其版本 / 候选值缓存;
  3. 这些账号的团队成员身份、取数记录、下载事件、通知、被授予的权限;
  4. 与这些账号相关的审计:它们自己的动作(user_id),以及真人对它们的操作
     (resource_type='user' 指向它们,或 detail.target_user_id 指向它们)。

不删什么(命中即中止,交人判断,绝不擅自改真实数据):
  - 假账号名下的任务若已有授权或取数记录 —— 说明真人在用,不是残留;
  - 假账号写的版本挂在真人的任务下;
  - 存活行里指向假账号的软引用(teams.created_by、team_members.added_by、
    credentials.updated_by、enum_values.updated_by、permissions.granted_by、
    template_versions.accepted_by)—— 置空还是改挂真人,得人来定。

**破坏性、一次性脚本**,不接入 deploy.sh / migrate.py 的常规路径。安全设计:
  - 默认 dry-run,只打印计划;--apply 才写库;
  - --apply 前自动把所有待删行整行备份成 JSON(落在 DATA_DIR/backups/),可据此还原;
  - 全部删除在**单个事务**内完成,删完当场复核引用面归零才提交,任何异常整体回滚;
  - 本地缺表(SQLite 老库没有 teams 等)自动跳过,便于先在本地演练。

安全要求(与 migrate.py / renumber_users.py 同):先在本地 SQLite 验证,再对线上 MySQL 执行。

用法(在 backend 目录):
    python -m app.cleanup_mock_users            # dry-run:只打印计划,不写库(默认)
    python -m app.cleanup_mock_users --apply    # 实际执行(先备份,再单事务删除)
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import inspect, text

import app.models  # noqa: F401  注册所有模型
from app.core.config import settings
from app.core.database import engine, tbl

# 假账号的唯一判定依据:飞书 open_id 精确匹配(与 frontend LoginPage 的 MOCK_USERS 一致)。
# 真实账号的 open_id 都是 ou_ + 32 位 hex,不会与这四个撞上。
MOCK_OPEN_IDS = ("ou_admin", "ou_analyst", "ou_dev", "ou_viewer")
_OPEN_IDS_CSV = ", ".join(f"'{o}'" for o in MOCK_OPEN_IDS)

# 删除顺序:子表先于父表(MySQL 上 users / sql_templates 有真实外键指向)。
# 这里用的就是逻辑表名 —— 计划里的 id 集合、tbl()、SOFT_REFS 全用同一套键,不另起别名。
DELETE_ORDER: list[str] = [
    "audit_logs",
    "download_events",
    "notifications",
    "permissions",
    "query_jobs",
    "template_enum_values",
    "template_versions",
    "sql_templates",
    "team_members",
    "users",
]

# 存活行里指向 user.id 的软引用(无外键):命中假账号即中止。(逻辑表名, 列名)
# 「谁引用了 user.id」的完整清单(含真实外键那几列)在 renumber_users.NUMERIC_REFS,
# 本文件只需要软引用那一半;将来新增引用 user.id 的列,两处都要跟上。
SOFT_REFS: list[tuple[str, str]] = [
    ("teams", "created_by"),
    ("team_members", "added_by"),
    ("team_datasource_credentials", "updated_by"),
    ("template_enum_values", "updated_by"),
    ("permissions", "granted_by"),
    ("template_versions", "accepted_by"),
]


@dataclass
class Plan:
    users: list[dict] = field(default_factory=list)          # 假账号整行(供打印/备份)
    ids: dict[str, list[int]] = field(default_factory=dict)  # 逻辑表名 -> 待删 id
    present: set[str] = field(default_factory=set)           # 库里真实存在的表(只反射一次)
    blockers: list[str] = field(default_factory=list)        # 非空即中止

    @property
    def user_ids(self) -> list[int]:
        return self.ids.get("users", [])

    @property
    def uid_csv(self) -> str:
        """假账号 id 的 SQL IN 列表,给数值列用。"""
        return _in_csv(self.user_ids)

    @property
    def uid_csv_quoted(self) -> str:
        """同上但带引号:permissions.subject_id 是字符串列,存的是 user.id。"""
        return ", ".join(f"'{i}'" for i in self.user_ids)

    @property
    def perm_subject_where(self) -> str:
        """permissions 里「授权对象是这批假账号」的条件(本文件三处共用,免得各写一遍)。"""
        return f"subject_type = 'user' AND subject_id IN ({self.uid_csv_quoted})"

    def has(self, table: str) -> bool:
        """库里有没有这张表 —— 本地老库缺 teams 等,缺表就跳过而不是报错。"""
        return tbl(table) in self.present


def _in_csv(ids) -> str:
    """把 id 列表拼成 SQL IN 列表。全部强转 int,不存在注入面。"""
    return ", ".join(str(int(i)) for i in ids)


def _rows(conn, sql: str) -> list[dict]:
    return [dict(m) for m in conn.execute(text(sql)).mappings().all()]


def _ids(conn, sql: str) -> list[int]:
    return [int(r["id"]) for r in _rows(conn, sql)]


def _count(conn, sql: str) -> int:
    return int(conn.execute(text(sql)).scalar() or 0)


def build_plan(conn) -> Plan:
    """纯读:算出要删哪些行,以及有没有该中止的情况。"""
    plan = Plan(present=set(inspect(conn).get_table_names()))

    plan.users = _rows(
        conn,
        f"SELECT * FROM {tbl('users')} WHERE feishu_open_id IN ({_OPEN_IDS_CSV}) ORDER BY id",
    )
    plan.ids["users"] = [int(u["id"]) for u in plan.users]
    if not plan.user_ids:
        return plan

    uids = plan.uid_csv

    # 1) 假账号名下的任务 —— 有人在用就不算残留,中止
    if plan.has("sql_templates"):
        tmpls = _rows(
            conn,
            f"SELECT id, name, status FROM {tbl('sql_templates')} WHERE author_id IN ({uids})",
        )
        plan.ids["sql_templates"] = [int(t["id"]) for t in tmpls]
        for t in tmpls:
            tid = int(t["id"])
            # 只看**真人**的使用痕迹:授权给假账号、假账号自己跑的记录同属残留,会随本次清理一起删。
            used = 0
            if plan.has("query_jobs"):
                used += _count(
                    conn,
                    f"SELECT COUNT(*) FROM {tbl('query_jobs')} "
                    f"WHERE template_id = {tid} AND user_id NOT IN ({uids})",
                )
            if plan.has("permissions"):
                used += _count(
                    conn,
                    f"SELECT COUNT(*) FROM {tbl('permissions')} "
                    f"WHERE resource_type = 'template' AND resource_id = '{tid}' "
                    f"AND NOT ({plan.perm_subject_where})",
                )
            if used:
                plan.blockers.append(
                    f"任务 id={tid}「{t['name']}」由假账号创建,但已有 {used} 条真人的授权/取数记录,"
                    f"说明真人在用 —— 请先把作者改挂到真人,再重跑本脚本"
                )

    tids = _in_csv(plan.ids.get("sql_templates", []))

    # 2) 版本:属于上面那些任务的全删;假账号写在真人任务下的版本则中止
    if plan.has("template_versions"):
        if tids:
            plan.ids["template_versions"] = _ids(
                conn,
                f"SELECT id FROM {tbl('template_versions')} WHERE template_id IN ({tids})",
            )
        strays = _rows(
            conn,
            f"SELECT id, template_id FROM {tbl('template_versions')} WHERE author_id IN ({uids})"
            + (f" AND template_id NOT IN ({tids})" if tids else ""),
        )
        for s in strays:
            plan.blockers.append(
                f"版本 id={s['id']} 由假账号编写,但挂在真人任务 template_id={s['template_id']} 下 —— "
                f"请先把该版本作者改挂到真人,再重跑本脚本"
            )

    # 3) 候选值缓存(随任务走)
    if plan.has("template_enum_values") and tids:
        plan.ids["template_enum_values"] = _ids(
            conn,
            f"SELECT id FROM {tbl('template_enum_values')} WHERE template_id IN ({tids})",
        )

    # 4) 假账号自己的取数 / 下载 / 通知 / 团队成员身份 / 被授予的权限
    for table, where in [
        ("query_jobs", f"user_id IN ({uids})"),
        ("download_events", f"user_id IN ({uids})"),
        ("notifications", f"user_id IN ({uids})"),
        ("team_members", f"user_id IN ({uids})"),
        ("permissions", plan.perm_subject_where),
    ]:
        if plan.has(table):
            plan.ids[table] = _ids(conn, f"SELECT id FROM {tbl(table)} WHERE {where}")

    # 5) 审计:假账号自己的动作 + 真人对假账号的操作
    if plan.has("audit_logs"):
        plan.ids["audit_logs"] = _mock_audit_ids(conn, plan)

    # 6) 存活行里指向假账号的软引用 —— 置空还是改挂真人,得人来定。
    #    本来就要删的行不算(它们马上不存在了),故排除该表自己那份待删 id。
    for table, col in SOFT_REFS:
        if not plan.has(table):
            continue
        doomed = _in_csv(plan.ids.get(table, []))
        skip = f" AND id NOT IN ({doomed})" if doomed else ""
        for h in _rows(conn, f"SELECT id FROM {tbl(table)} WHERE {col} IN ({uids}){skip}"):
            plan.blockers.append(
                f"{tbl(table)} id={h['id']} 的 {col} 指向假账号,且该行要保留 —— "
                f"请先决定置空还是改挂真人,再重跑本脚本"
            )

    return plan


def _mock_audit_ids(conn, plan: Plan) -> list[int]:
    """挑出与假账号相关的审计行 id。

    三类:① 假账号自己的动作(user_id);② 真人对假账号的操作(resource_type='user' 指向它);
    ③ 动作落在别的资源上、但 detail 里的 target_user_id 是它(如「撤销团队管理员」)。

    第三类的判定放在 Python 里做 —— detail 是 JSON,SQL 侧的 LIKE 会被空格/键序坑到。
    但 WHERE 仍先把明显无关的行挡在库里:audit_logs 是全库写入量最大、只增不删的表
    (见 models/audit.py),把它整表拉进内存来筛,代价会随审计历史无止境地涨。
    LIKE 只作粗筛(带 target_user_id 这个键的行才可能命中),精确判定仍由下面的解析负责。
    """
    mock_ids = set(plan.user_ids)
    rows = _rows(
        conn,
        f"SELECT id, user_id, resource_type, resource_id, detail FROM {tbl('audit_logs')} "
        f"WHERE user_id IN ({plan.uid_csv}) "
        f"OR (resource_type = 'user' AND resource_id IN ({plan.uid_csv_quoted})) "
        f"OR detail LIKE '%target_user_id%'",
    )
    hit: list[int] = []
    for r in rows:
        uid = r.get("user_id")
        if uid is not None and int(uid) in mock_ids:
            hit.append(int(r["id"]))
            continue
        if r.get("resource_type") == "user" and str(r.get("resource_id") or "").isdigit():
            if int(r["resource_id"]) in mock_ids:
                hit.append(int(r["id"]))
                continue
        detail = r.get("detail")
        if detail:
            try:
                target = json.loads(detail).get("target_user_id")
            except (ValueError, TypeError, AttributeError):
                target = None
            if target is not None and str(target).isdigit() and int(target) in mock_ids:
                hit.append(int(r["id"]))
    return sorted(hit)


def print_plan(plan: Plan) -> None:
    if not plan.user_ids:
        print("[cleanup] 库里没有这批 mock 账号,无事可做。")
        return
    print(f"[cleanup] 命中 mock 账号 {len(plan.users)} 个:")
    for u in plan.users:
        print(
            f"    id={u['id']:<4} open_id={u['feishu_open_id']:<12} role={u.get('role')}"
            f"  最后登录={u.get('last_login_at')}"
        )
    print("[cleanup] 待删除的关联行:")
    total = len(plan.user_ids)
    for table in DELETE_ORDER:
        if table == "users":  # 账号本身已在上面逐个列出
            continue
        ids = plan.ids.get(table, [])
        total += len(ids)
        mark = "" if ids else "  (无)"
        print(f"    {tbl(table):<38} {len(ids):>3} 条{mark}" + (f"  ids={ids}" if ids else ""))
    print(f"[cleanup] 合计将删除 {total} 行(含 {len(plan.user_ids)} 个账号本身)。")
    if plan.blockers:
        print("\n[cleanup] 以下情况需要人来决定,已中止:")
        for b in plan.blockers:
            print(f"    ! {b}")


def backup(conn, plan: Plan) -> Path:
    """把所有待删行整行导出为 JSON,供还原。"""
    out: dict[str, list[dict]] = {}
    for table in DELETE_ORDER:
        ids = plan.ids.get(table, [])
        if ids:
            out[tbl(table)] = _rows(
                conn, f"SELECT * FROM {tbl(table)} WHERE id IN ({_in_csv(ids)})"
            )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = settings.backup_dir_path / f"mock_cleanup_{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "database": str(engine.url),
        "created_at": stamp,
        "mock_open_ids": list(MOCK_OPEN_IDS),
        "rows": out,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def apply_plan(conn, plan: Plan) -> dict[str, int]:
    """按子表→父表顺序删除。conn 需处于事务中(由调用方 engine.begin() 提供)。"""
    deleted: dict[str, int] = {}
    for table in DELETE_ORDER:
        ids = plan.ids.get(table, [])
        if not ids or not plan.has(table):
            continue
        res = conn.execute(text(f"DELETE FROM {tbl(table)} WHERE id IN ({_in_csv(ids)})"))
        deleted[tbl(table)] = res.rowcount
    return deleted


def verify(conn, plan: Plan) -> list[str]:
    """删完当场复核:账号没了,引用面归零。返回问题列表(空=干净)。

    刻意与 build_plan 各写一遍条件、不共用代码:这是对「删对了吗」的独立断言,
    共用一份推导就等于自己给自己作证。
    """
    problems: list[str] = []

    left = _count(
        conn, f"SELECT COUNT(*) FROM {tbl('users')} WHERE feishu_open_id IN ({_OPEN_IDS_CSV})"
    )
    if left:
        problems.append(f"{tbl('users')} 里还剩 {left} 个 mock 账号")

    uids = plan.uid_csv
    checks = [
        ("query_jobs", f"user_id IN ({uids})"),
        ("download_events", f"user_id IN ({uids})"),
        ("notifications", f"user_id IN ({uids})"),
        ("team_members", f"user_id IN ({uids}) OR added_by IN ({uids})"),
        ("sql_templates", f"author_id IN ({uids})"),
        ("template_versions", f"author_id IN ({uids}) OR accepted_by IN ({uids})"),
        ("template_enum_values", f"updated_by IN ({uids})"),
        ("teams", f"created_by IN ({uids})"),
        ("team_datasource_credentials", f"updated_by IN ({uids})"),
        ("permissions", f"({plan.perm_subject_where}) OR granted_by IN ({uids})"),
        ("audit_logs", f"user_id IN ({uids})"),
    ]
    for table, where in checks:
        if not plan.has(table):
            continue
        n = _count(conn, f"SELECT COUNT(*) FROM {tbl(table)} WHERE {where}")
        if n:
            problems.append(f"{tbl(table)} 仍有 {n} 行引用 mock 账号({where})")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description="清除 mock 登录造出来的假账号及其残留")
    parser.add_argument("--apply", action="store_true", help="实际执行(默认仅 dry-run 打印计划)")
    args = parser.parse_args()

    print(f"[cleanup] 目标库:{engine.url}")
    with engine.connect() as conn:
        plan = build_plan(conn)
        print_plan(plan)

        if not plan.user_ids:
            return
        if plan.blockers:
            raise SystemExit(1)
        if not args.apply:
            print("\n[cleanup] dry-run 结束,未写库。确认无误后加 --apply 执行。")
            return

        path = backup(conn, plan)
        print(f"\n[cleanup] 已备份待删行 → {path}")

    with engine.begin() as conn:
        deleted = apply_plan(conn, plan)
        print("[cleanup] 删除结果:")
        for table, n in deleted.items():
            print(f"    {table:<38} -{n}")
        problems = verify(conn, plan)
        if problems:
            print("[cleanup] 复核不通过,整体回滚:")
            for p in problems:
                print(f"    ! {p}")
            raise SystemExit(f"cleanup aborted: {problems}")
        print("[cleanup] 复核通过(账号已清空、引用面归零),提交事务。")

    print("[cleanup] 完成。")


if __name__ == "__main__":
    main()

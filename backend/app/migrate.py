"""平台元数据库的建表与轻量前向迁移(幂等)。

替代 Alembic:本项目 schema 简单,用 `create_all` 建新表 + 少量幂等 `ALTER` 处理增量列,
再对存量敏感字段做加密升级。可重复执行。

用法(部署时):
    (cd backend && python -m app.migrate)

安全:涉及线上库结构与数据变更,**先在本地 SQLite 验证**,再在部署窗口对线上库执行。
"""
from __future__ import annotations

from sqlalchemy import func, inspect as sa_inspect, select, text
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

import app.models  # noqa: F401  注册所有模型
from app.core.database import Base, engine, tbl
from app.models.team import DEFAULT_TEAM_NAME, Team, TeamMember
from app.models.template import SqlTemplate, TemplateVersion
from app.models.user import (
    ROLE_ADMIN,
    ROLE_DEVELOPER,
    ROLE_USER,
    SYSTEM_SCHEDULER_NAME,
    SYSTEM_SCHEDULER_OPEN_ID,
    User,
)
from app.reencrypt_secrets import main as reencrypt_secrets


def _has_column(table: str, column: str) -> bool:
    """SQLAlchemy 自省判断列是否存在(而非靠捕获异常),让真正的失败如实抛出。"""
    return column in {c["name"] for c in sa_inspect(engine).get_columns(table)}


def _ensure_column(table: str, column: str, coltype: str) -> None:
    """若列不存在则 ADD COLUMN;已存在则幂等跳过。"""
    if _has_column(table, column):
        print(f"[migrate] {table}.{column} 已存在,跳过")
        return
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"))
    print(f"[migrate] {table}: 新增列 {column}")


def _ensure_index(table: str, name: str, columns: str) -> None:
    """若索引不存在则 CREATE INDEX(MySQL 无 IF NOT EXISTS,靠自省判断)。幂等。

    注意:线上大表建索引是 online DDL,放部署窗口执行。
    """
    if name in {i["name"] for i in sa_inspect(engine).get_indexes(table)}:
        print(f"[migrate] 索引 {name} 已存在,跳过")
        return
    with engine.begin() as conn:
        conn.execute(text(f"CREATE INDEX {name} ON {table} ({columns})"))
    print(f"[migrate] {table}: 新增索引 {name}")


def _drop_column(table_obj, column: str) -> None:
    """幂等删除列,与 _ensure_column 对称,方言感知。

    MySQL:先删列上的外键约束(名字查 information_schema),再 DROP COLUMN。
    SQLite:列带外键无法直接 DROP,按当前模型定义重建表并回拷数据(仅本地验证会走到)。
    """
    table = table_obj.name
    if not _has_column(table, column):
        print(f"[migrate] {table}.{column} 不存在,跳过")
        return
    if engine.dialect.name == "mysql":
        with engine.begin() as conn:
            fks = conn.execute(
                text(
                    "SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t "
                    "AND COLUMN_NAME = :c AND REFERENCED_TABLE_NAME IS NOT NULL"
                ),
                {"t": table, "c": column},
            ).scalars().all()
            for fk in fks:
                conn.execute(text(f"ALTER TABLE {table} DROP FOREIGN KEY {fk}"))
            conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
        print(f"[migrate] {table}: 删除列 {column}(及外键 {fks or '无'})")
    else:
        with engine.begin() as conn:
            # 先删旧表上的命名索引:重命名后索引名不变,重建时会与新表同名索引冲突
            for r in conn.execute(text(f"PRAGMA index_list('{table}')")).mappings().all():
                if not str(r["name"]).startswith("sqlite_autoindex_"):
                    conn.execute(text(f"DROP INDEX IF EXISTS {r['name']}"))
            tmp = f"_{table}_old"
            conn.execute(text(f"ALTER TABLE {table} RENAME TO {tmp}"))
            table_obj.create(bind=conn)  # 按新模型重建(不含被删列,含原索引)
            cols = ", ".join(c.name for c in table_obj.columns)
            conn.execute(text(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM {tmp}"))
            conn.execute(text(f"DROP TABLE {tmp}"))
        print(f"[migrate] {table}: 重建表以移除列 {column}(SQLite)")


def _retired_columns() -> list[tuple]:
    """已从模型退役、需要在存量库里一并清掉的列:[(Table, 列名)]。

    **删模型列时必须在这里登记。** 死列若是 NOT NULL 又无默认值,会让该表的所有
    INSERT 在 MySQL 严格模式下直接失败(1364 Field 'x' doesn't have a default value),
    而错误落在写入端、与业务代码无关,极难从现象倒推。
    """
    from app.models.credential import TeamDataSourceCredential
    from app.models.query_job import QueryJob

    return [
        # 「入口库」只活了几个小时:它是为「账号进不去数据源那个库就连不上」准备的补丁,
        # 而随后 Hive 建会话改成压根不进入任何库(见 connectors/hive.py::_open_session),
        # 这个配置项就没有存在的理由了。线上两行的值都是 NULL,删掉无损。
        (TeamDataSourceCredential.__table__, "entry_database"),
        # d4d4961 取消「变量正/反选」后 modes 成为死列,当时漏了迁移:
        # 线上 rubick_query_jobs.modes 残留为 json NOT NULL,自 8/3 起阻断了全部取数入队。
        (QueryJob.__table__, "modes"),
        # 取数身份从「个人账号」改为「团队账号」后,run_as_user_id 被 run_as_team_id 取代。
        # 线上从未有过这一列(个人账号功能未曾发布),故这条对线上是 no-op;
        # 登记它是为了清掉本地开发库里已经加出来的那一列。
        (QueryJob.__table__, "run_as_user_id"),
    ]


def _drop_retired_columns() -> None:
    """幂等清理 _retired_columns() 登记的死列。"""
    for table_obj, column in _retired_columns():
        _drop_column(table_obj, column)


def _assert_no_blocking_orphan_columns() -> None:
    """体检:库里存在、模型已无,且 NOT NULL 无默认值的列会阻断该表所有写入,直接报错。

    宁可在部署窗口的迁移里失败(有人看着、信息明确),也不要放到运行期变成
    一句「服务器内部错误」——modes 列就是这么让「运行任务」静默瘫了一整天。
    """
    insp = sa_inspect(engine)
    blocking: list[str] = []
    for table in Base.metadata.tables.values():
        if not insp.has_table(table.name):
            continue
        model_columns = {c.name for c in table.columns}
        for col in insp.get_columns(table.name):
            if col["name"] not in model_columns and not col["nullable"] and col["default"] is None:
                blocking.append(f"{table.name}.{col['name']}")
    if blocking:
        raise RuntimeError(
            "以下列已从模型退役但库中残留,且 NOT NULL 无默认值,会阻断该表所有 INSERT:"
            + "、".join(blocking)
            + "。请在 app/migrate.py 的 _retired_columns() 里登记后重跑迁移。"
        )
    print("[migrate] 死列体检通过:无阻断写入的残留列")


def _drop_table(name: str) -> None:
    """幂等删除表。"""
    if sa_inspect(engine).has_table(name):
        with engine.begin() as conn:
            conn.execute(text(f"DROP TABLE {name}"))
        print(f"[migrate] 删除表 {name}")
    else:
        print(f"[migrate] 表 {name} 不存在,跳过")


def _purge_department_permissions() -> None:
    """取消部门级授权:清除存量 subject_type='department' 的权限行。幂等(二次删 0 行)。

    字符串字面值用单引号即可;reencrypt 那条「列名不加双引号」的坑针对的是标识符,与此无关。
    """
    with engine.begin() as conn:
        res = conn.execute(
            text(f"DELETE FROM {tbl('permissions')} WHERE subject_type = 'department'")
        )
    print(f"[migrate] 清除部门授权行:{res.rowcount} 行")


def _drop_department_schema() -> None:
    """删除部门数据 schema:users.department_id 列 + departments 表。幂等。"""
    from app.models.user import User  # 当前模型已不含 department_id

    _drop_column(User.__table__, "department_id")
    _drop_table(tbl("departments"))


def _migrate_pending_accept() -> None:
    """发布流简化为 草稿→发布:存量 pending_accept(必然无已发布版本)回退 draft。"""
    with engine.begin() as conn:
        res = conn.execute(
            text(f"UPDATE {tbl('sql_templates')} SET status='draft' WHERE status='pending_accept'")
        )
    print(f"[migrate] pending_accept → draft:{res.rowcount} 行")


def _single_param(name: str, label: str, desc: str) -> dict:
    """构造一个 v2 单值参数定义。"""
    d = {"name": name, "kind": "single", "label": label}
    if desc:
        d["description"] = desc
    return d


def _migrate_params_v2() -> None:
    """参数定义 v1 → v2:一切参数只分 single(单值文本)/list(值列表),一律必填、无默认值。

    映射:text/string/number/date/enum → single(date 补格式提示,enum 的 options 折叠进说明);
    multi_enum → list(保留 enum_sql,list_mode 按 sql_text 判定);
    date_range x → x_start + x_end 两个 single;number_range x → x_min + x_max。
    幂等:版本内所有参数已含 "kind" 键则跳过。
    结束打印行为变更清单(原选填/有默认值的参数,现改为必填且无默认)。
    """
    aliases = {"string": "text", "daterange": "date_range"}
    changed_versions = 0
    report: list[str] = []  # 行为变更清单

    with Session(engine) as db:
        for ver in db.query(TemplateVersion).order_by(TemplateVersion.id):
            old = ver.params or []
            if not old or all(isinstance(p, dict) and "kind" in p for p in old):
                continue

            new_params: list[dict] = []
            for p in old:
                p = dict(p)
                if "kind" in p:  # 已是 v2
                    new_params.append(p)
                    continue
                t = aliases.get(p.get("type") or "text", p.get("type") or "text")
                name = p.get("name", "")
                label = p.get("label") or name
                desc = p.get("description") or ""

                if p.get("required") is False or p.get("all_when_empty") or p.get("default") not in (None, ""):
                    report.append(
                        f"  版本 {ver.id}(模板 {ver.template_id} v{ver.version_no})参数 {name}"
                        f"(原 required={p.get('required')} all_when_empty={p.get('all_when_empty')}"
                        f" default={p.get('default')!r})"
                    )

                if t == "multi_enum":
                    d = {"name": name, "kind": "list", "label": label}
                    if desc:
                        d["description"] = desc
                    if p.get("enum_sql"):
                        d["enum_sql"] = p["enum_sql"]
                    new_params.append(d)
                elif t == "date_range":
                    hint = ("、" + desc) if desc else ""
                    new_params.append(_single_param(f"{name}_start", f"{label}(起)", f"格式 YYYY-MM-DD{hint}"))
                    new_params.append(_single_param(f"{name}_end", f"{label}(止)", f"格式 YYYY-MM-DD{hint}"))
                elif t == "number_range":
                    new_params.append(_single_param(f"{name}_min", f"{label}(最小)", desc))
                    new_params.append(_single_param(f"{name}_max", f"{label}(最大)", desc))
                else:  # text / number / date / enum
                    if t == "date":
                        desc = (desc + ";" if desc else "") + "格式 YYYY-MM-DD"
                    if t == "enum" and p.get("options"):
                        desc = (desc + ";" if desc else "") + "可选值:" + " / ".join(map(str, p["options"]))
                    new_params.append(_single_param(name, label, desc))

            ver.params = new_params
            changed_versions += 1
        db.commit()

    print(f"[migrate] params v1 → v2:重写 {changed_versions} 个版本")
    if report:
        print("[migrate] ⚠ 行为变更:以下参数原为选填/有默认值,现改为必填且无默认:")
        print("\n".join(report))


def _param_needs_v3(p: dict) -> bool:
    """v2 → v3 是否需要迁移该参数:仍带 description/list_mode,或 list 参数缺 allow_bulk_input。"""
    return (
        "description" in p
        or "list_mode" in p
        or (p.get("kind") == "list" and "allow_bulk_input" not in p)
    )


def _migrate_params_v3() -> None:
    """参数定义 v2 → v3:中文名/说明合并为单一 label(变量说明),去掉 list_mode 方向,
    给存量 list 参数补 allow_bulk_input=True(保持既有「上传/粘贴」可用)。幂等。
    """
    changed_versions = 0
    with Session(engine) as db:
        for ver in db.query(TemplateVersion).order_by(TemplateVersion.id):
            old = ver.params or []
            if not old or not any(isinstance(p, dict) and _param_needs_v3(p) for p in old):
                continue

            new_params: list[dict] = []
            for p in old:
                p = dict(p)
                desc = p.pop("description", None)
                p.pop("list_mode", None)
                # 中文名(label)+ 说明(description)合并为单一「变量说明」,两者都在则拼接保留
                label = p.get("label")
                if desc:
                    p["label"] = f"{label}({desc})" if label else desc
                if p.get("kind") == "list":
                    p.setdefault("allow_bulk_input", True)  # 存量列表保持可上传/粘贴
                new_params.append(p)

            ver.params = new_params
            changed_versions += 1
        db.commit()

    print(f"[migrate] params v2 → v3:重写 {changed_versions} 个版本")


# ---------------------------------------------------------------- 团队功能


def _default_team_needed(db: Session) -> tuple[bool, str]:
    """是否需要建「默认团队」,以及原因(打印用)。

    两种情况:
      ① 有任务还没团队(存量库):不并入的话这些任务会变成「无主任务」,除平台管理员外谁都看不到;
      ② 库里已有开发者/管理员但一个团队都没有:否则他们连建任务时能选的团队都没有,
        而报错会发生在业务侧、离这次迁移很远,没人联想得到。
    全新空库两条都不成立 ⇒ 不造一个没人要的团队。
    """
    orphan_tasks = db.scalar(
        select(func.count()).select_from(SqlTemplate).where(SqlTemplate.team_id.is_(None))
    ) or 0
    if orphan_tasks:
        return True, f"{orphan_tasks} 个存量任务还没有团队"
    teams = db.scalar(select(func.count()).select_from(Team)) or 0
    authors = db.scalar(
        select(func.count()).select_from(User).where(User.role.in_((ROLE_ADMIN, ROLE_DEVELOPER)))
    ) or 0
    if teams == 0 and authors:
        return True, f"库里已有 {authors} 名管理员/开发者,但一个团队都没有"
    return False, ""


def _migrate_default_team() -> None:
    """存量任务并入「默认团队」,并把管理员/开发者纳入该团队。

    **自检式幂等**:靠「还有没有 team_id 为空的任务 / 有没有团队」判断,不靠版本号或标记位,
    因此可以反复执行。

    刻意**不**把「任务作者」一律加进团队:历史上普通用户也能当作者,而加入团队等于把该团队
    取数账号的全部数据权限交给他(见 models/team.py)。这类任务只打印出来交人工决定
    (改角色 / 加成员 / 转移任务),不静默扩权。
    """
    with Session(engine) as db:
        needed, why = _default_team_needed(db)
        if not needed:
            print("[migrate] 默认团队:无需建立(任务都已有团队,或是全新空库)")
            return
        print(f"[migrate] 默认团队:需要建立 —— {why}")

        team = db.scalar(select(Team).where(Team.name == DEFAULT_TEAM_NAME))
        if team is None:
            team = Team(
                name=DEFAULT_TEAM_NAME,
                description="团队功能上线前的存量任务与成员。请由平台管理员按实际组织拆分。",
            )
            db.add(team)
            db.flush()
            print(f"[migrate] 建立团队《{DEFAULT_TEAM_NAME}》id={team.id}")

        # 成员:管理员当团队管理员,开发者当普通成员。已存在的成员行跳过(幂等)。
        existing = {
            uid for (uid,) in db.execute(
                select(TeamMember.user_id).where(TeamMember.team_id == team.id)
            )
        }
        added = 0
        for u in db.scalars(
            select(User).where(User.role.in_((ROLE_ADMIN, ROLE_DEVELOPER))).order_by(User.id)
        ):
            if u.id in existing:
                continue
            db.add(TeamMember(team_id=team.id, user_id=u.id, is_team_admin=u.role == ROLE_ADMIN))
            added += 1

        # 任务并入:只动 team_id 为空的行
        merged = db.execute(
            sa_update(SqlTemplate)
            .where(SqlTemplate.team_id.is_(None))
            .values(team_id=team.id)
        ).rowcount or 0
        db.commit()
        print(f"[migrate] 默认团队:并入 {merged} 个任务、新增 {added} 名成员")

        # 体检:作者不在团队里的任务 —— 这些作者切换后会失去自己任务的可见性
        stranded = db.execute(
            select(SqlTemplate.id, SqlTemplate.name, User.name, User.role)
            .join(User, User.id == SqlTemplate.author_id)
            .outerjoin(
                TeamMember,
                (TeamMember.team_id == SqlTemplate.team_id)
                & (TeamMember.user_id == SqlTemplate.author_id),
            )
            .where(TeamMember.id.is_(None))
            .order_by(SqlTemplate.id)
        ).all()
        if stranded:
            print(
                f"[migrate] ⚠️ 以下 {len(stranded)} 个任务的作者不在其所属团队内,"
                "切换后作者将看不到自己的任务。请人工决定(改角色并加入团队 / 转移任务):"
            )
            for tid, tname, uname, urole in stranded:
                print(f"    任务#{tid} 《{tname}》 作者={uname}({urole})")


def _assert_every_template_has_team() -> None:
    """体检:任务必属团队。

    留一行空 team_id 就意味着一个「无主任务」——除平台管理员外谁都看不到,也解析不出取数身份
    (见 models/template.py 对 team_id 可空的说明)。宁可在部署窗口失败(有人看着、信息明确),
    也不要到运行期变成一句「无权查看该任务」。
    """
    with Session(engine) as db:
        rows = db.execute(
            select(SqlTemplate.id, SqlTemplate.name).where(SqlTemplate.team_id.is_(None))
        ).all()
    if rows:
        raise RuntimeError(
            f"以下 {len(rows)} 个任务没有所属团队,平台无法判定其可见性与取数身份:"
            + "、".join(f"#{tid}《{name}》" for tid, name in rows)
            + "。请先建团队并为它们指定归属(或重跑 _migrate_default_team)。"
        )
    print("[migrate] 任务归属体检通过:每个任务都有所属团队")


def _ensure_system_scheduler_user() -> None:
    """幂等创建订阅定时运行的系统用户(见 models/user.py 的常量说明)。

    is_active=False:它只作为订阅 job 的 user_id 出现在运行记录里,永远登录不进来。
    按哨兵 open_id 判存在 —— open_id 是用户表的 upsert 主键,任何登录路径都撞不上它。
    """
    with Session(engine) as db:
        exists = db.scalar(select(User.id).where(User.feishu_open_id == SYSTEM_SCHEDULER_OPEN_ID))
        if exists:
            print("[migrate] 系统用户(定时运行)已存在,跳过")
            return
        db.add(
            User(
                feishu_open_id=SYSTEM_SCHEDULER_OPEN_ID,
                name=SYSTEM_SCHEDULER_NAME,
                role=ROLE_USER,
                is_active=False,
            )
        )
        db.commit()
    print("[migrate] 已创建系统用户(定时运行)")


def main() -> None:
    print("[migrate] create_all on", engine.url)
    # 建缺失的表(如新表)。共享枚举候选值表 template_enum_values,以及团队三张表
    # teams / team_members / team_datasource_credentials,都是靠这一步建出来的;
    # 它们没有增量列,不需要下面的 _ensure_column。
    Base.metadata.create_all(bind=engine)

    # 增量列:按任务的查询超时(P0-3)
    _ensure_column(tbl("sql_templates"), "timeout_seconds", "INTEGER")
    # 增量列:通知所属任务 id,支持点击深链(免前端再查 job)
    _ensure_column(tbl("notifications"), "template_id", "BIGINT")
    # 增量列:审计资源名称快照,任务改名/数据源删除后日志仍可读
    _ensure_column(tbl("audit_logs"), "resource_name", "VARCHAR(200)")
    # 增量索引:审计按时间范围检索 + 分页 COUNT(全库写入量最大的表,无索引会全表扫)
    _ensure_index(tbl("audit_logs"), f"ix_{tbl('audit_logs')}_created_at", "created_at")
    # 增量列:本次取数实际使用的库身份(= 任务所属团队的团队账号),存量行留空
    _ensure_column(tbl("query_jobs"), "run_as_team_id", "BIGINT")
    _ensure_column(tbl("query_jobs"), "run_as_username", "VARCHAR(128)")
    # 增量列:订阅运行的结果被下一期成功结果取代的时刻(驱动订阅结果保留期);
    # 非订阅行恒为空。订阅三张表 task_schedules / task_subscriptions /
    # task_subscription_events 由上面的 create_all 建出,无增量列。
    _ensure_column(tbl("query_jobs"), "superseded_at", "DATETIME")

    # 开始执行的时刻 —— 有了它才算得出排队等待时长(duration_ms 只含执行)。
    # 存量行留空:它们确实没有这个记录,运营分析据此只统计有值的样本,不拿 0 充数。
    _ensure_column(tbl("query_jobs"), "started_at", "DATETIME")

    # 运营分析要的三条索引。**定义在模型的 __table_args__ 里**(QueryJob / DownloadEvent),
    # 这里只是给存量库补建 —— create_all 不会给已存在的表加索引。与 audit_logs.created_at
    # 同一套两处写法:模型描述表的全貌,迁移负责把老库追上来。
    _ensure_index(tbl("query_jobs"), f"ix_{tbl('query_jobs')}_created_at", "created_at")
    _ensure_index(
        tbl("query_jobs"), f"ix_{tbl('query_jobs')}_source_status_created",
        "source, status, created_at",
    )
    _ensure_index(
        tbl("download_events"), f"ix_{tbl('download_events')}_created_at", "created_at"
    )
    # 刻意**不加**的索引,写下来免得后人以为漏了:
    #   · query_jobs(template_id, created_at) —— 团队视角的 `template_id IN (...) AND 时间窗`。
    #     与上面那条复合索引有部分重叠,先不叠加,实测慢了再补;
    #   · template_versions / sql_templates / task_subscription_events / permissions ——
    #     行数在千级以内,全扫的代价低于多一个索引的写入与维护成本。
    # 增量列 + 索引:任务所属团队(可见性边界 + 取数身份来源)。
    # 只能加**可空**列(_ensure_column 的固有限制;补 NOT NULL 需 MySQL MODIFY / SQLite 重建表,
    # 既测不到又会挡住代码回滚)——「恒有值」靠下面的回填 + 体检 + 应用层必填三处保证。
    _ensure_column(tbl("sql_templates"), "team_id", "BIGINT")
    _ensure_index(tbl("sql_templates"), f"ix_{tbl('sql_templates')}_team_id", "team_id")

    # 存量敏感字段明文 → 密文(P0-2)
    reencrypt_secrets()

    # 取消部门级授权:清部门权限行 + 删部门 schema(表/列)(幂等)
    _purge_department_permissions()
    _drop_department_schema()

    # 清理已退役的死列,并体检是否还有会阻断写入的残留列(幂等)
    _drop_retired_columns()
    _assert_no_blocking_orphan_columns()

    # 发布流简化 + 参数定义迁移(幂等)
    _migrate_pending_accept()
    _migrate_params_v2()
    _migrate_params_v3()

    # 团队功能:存量任务并入默认团队,并体检「任务必属团队」(幂等)。
    # 顺序必须在 _ensure_column(team_id) 之后 —— 否则回填的目标列还不存在。
    _migrate_default_team()
    _assert_every_template_has_team()

    # 个人取数账号已被团队账号取代。线上从未有过这张表(该功能未曾发布),故对线上是 no-op;
    # 这一步是为了清掉本地开发库里已经建出来的那张表。
    _drop_table(tbl("user_datasource_credentials"))

    # 订阅定时运行的系统身份(幂等)
    _ensure_system_scheduler_user()

    print("[migrate] 完成。")


if __name__ == "__main__":
    main()

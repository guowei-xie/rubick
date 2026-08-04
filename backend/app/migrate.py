"""平台元数据库的建表与轻量前向迁移(幂等)。

替代 Alembic:本项目 schema 简单,用 `create_all` 建新表 + 少量幂等 `ALTER` 处理增量列,
再对存量敏感字段做加密升级。可重复执行。

用法(部署时):
    (cd backend && python -m app.migrate)

安全:涉及线上库结构与数据变更,**先在本地 SQLite 验证**,再在部署窗口对线上库执行。
"""
from __future__ import annotations

from sqlalchemy import inspect as sa_inspect, text
from sqlalchemy.orm import Session

import app.models  # noqa: F401  注册所有模型
from app.core.database import Base, engine, tbl
from app.models.template import TemplateVersion
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


def main() -> None:
    print("[migrate] create_all on", engine.url)
    Base.metadata.create_all(bind=engine)  # 建缺失的表(如新表)

    # 增量列:按任务的查询超时(P0-3)
    _ensure_column(tbl("sql_templates"), "timeout_seconds", "INTEGER")
    # 增量列:通知所属任务 id,支持点击深链(免前端再查 job)
    _ensure_column(tbl("notifications"), "template_id", "BIGINT")
    # 增量列:审计资源名称快照,任务改名/数据源删除后日志仍可读
    _ensure_column(tbl("audit_logs"), "resource_name", "VARCHAR(200)")
    # 增量索引:审计按时间范围检索 + 分页 COUNT(全库写入量最大的表,无索引会全表扫)
    _ensure_index(tbl("audit_logs"), f"ix_{tbl('audit_logs')}_created_at", "created_at")

    # 存量敏感字段明文 → 密文(P0-2)
    reencrypt_secrets()

    # 取消部门级授权:清部门权限行 + 删部门 schema(表/列)(幂等)
    _purge_department_permissions()
    _drop_department_schema()

    # 发布流简化 + 参数定义迁移(幂等)
    _migrate_pending_accept()
    _migrate_params_v2()
    _migrate_params_v3()

    print("[migrate] 完成。")


if __name__ == "__main__":
    main()

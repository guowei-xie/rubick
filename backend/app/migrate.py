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
from app.services import params_service


def _ensure_column(table: str, column: str, coltype: str) -> None:
    """若列不存在则 ADD COLUMN;已存在则幂等跳过。

    用 SQLAlchemy 自省判断存在性(而非靠捕获重复列异常),这样真正的 ALTER 失败
    (类型错误 / 权限不足 / 锁超时)会如实抛出,不被误当成「已存在」吞掉。
    """
    existing = {c["name"] for c in sa_inspect(engine).get_columns(table)}
    if column in existing:
        print(f"[migrate] {table}.{column} 已存在,跳过")
        return
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"))
    print(f"[migrate] {table}: 新增列 {column}")


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
                    # 值列表:方向随 SQL 写法(作者用 = 写的历史数据默认 in)
                    d = {
                        "name": name,
                        "kind": "list",
                        "label": label,
                        "list_mode": params_service.detect_list_mode(ver.sql_text, name) or "in",
                    }
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


def main() -> None:
    print("[migrate] create_all on", engine.url)
    Base.metadata.create_all(bind=engine)  # 建缺失的表(如新表)

    # 增量列:按任务的查询超时(P0-3)
    _ensure_column(tbl("sql_templates"), "timeout_seconds", "INTEGER")
    # 增量列:通知所属任务 id,支持点击深链(免前端再查 job)
    _ensure_column(tbl("notifications"), "template_id", "BIGINT")

    # 存量敏感字段明文 → 密文(P0-2)
    reencrypt_secrets()

    # 发布流简化 + 参数定义 v2(幂等)
    _migrate_pending_accept()
    _migrate_params_v2()

    print("[migrate] 完成。")


if __name__ == "__main__":
    main()

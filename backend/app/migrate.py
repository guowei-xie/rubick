"""平台元数据库的建表与轻量前向迁移(幂等)。

替代 Alembic:本项目 schema 简单,用 `create_all` 建新表 + 少量幂等 `ALTER` 处理增量列,
再对存量敏感字段做加密升级。可重复执行。

用法(部署时):
    (cd backend && python -m app.migrate)

安全:涉及线上库结构与数据变更,**先在本地 SQLite 验证**,再在部署窗口对线上库执行。
"""
from __future__ import annotations

from sqlalchemy import inspect as sa_inspect, text

import app.models  # noqa: F401  注册所有模型
from app.core.database import Base, engine, tbl
from app.reencrypt_secrets import main as reencrypt_secrets


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


def main() -> None:
    print("[migrate] create_all on", engine.url)
    Base.metadata.create_all(bind=engine)  # 建缺失的表(如新表)

    # 增量列:按任务的查询超时(P0-3)
    _ensure_column(tbl("sql_templates"), "timeout_seconds", "INTEGER")
    # 增量列:通知所属任务 id,支持点击深链(免前端再查 job)
    _ensure_column(tbl("notifications"), "template_id", "BIGINT")

    # 存量敏感字段明文 → 密文(P0-2)
    reencrypt_secrets()

    print("[migrate] 完成。")


if __name__ == "__main__":
    main()

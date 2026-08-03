"""一次性把库内明文敏感字段升级为密文(幂等:已加密的行跳过)。

覆盖:数据源密码、用户飞书 token / refresh_token。

用法:
    (cd backend && python -m app.reencrypt_secrets)

安全:
- 本脚本用原生 SQL 读写,绕过 EncryptedText 透明加解密,避免二次加密。
- **务必先在本地 SQLite 验证,再在部署窗口对线上库执行**(勿直接改线上库)。
- 幂等:带 enc:: 前缀的行视为已加密,自动跳过;可重复运行。
"""
from __future__ import annotations

from sqlalchemy import text

from app.core import crypto
from app.core.database import engine, tbl


def _migrate_column(conn, table: str, col: str) -> int:
    # 列名不加引号:MySQL 把双引号当字符串字面量(非标识符)会报 1064;这些列名均为安全标识符
    rows = conn.execute(
        text(f"SELECT id, {col} FROM {table} WHERE {col} IS NOT NULL")
    ).fetchall()
    n = 0
    for rid, val in rows:
        if val is None or crypto.is_encrypted(val):
            continue
        conn.execute(
            text(f"UPDATE {table} SET {col} = :v WHERE id = :id"),
            {"v": crypto.encrypt(val), "id": rid},
        )
        n += 1
    return n


TARGETS = [
    (tbl("data_sources"), "password"),
    (tbl("users"), "feishu_token"),
    (tbl("users"), "feishu_refresh_token"),
]


def main() -> None:
    with engine.begin() as conn:
        total = 0
        for table, col in TARGETS:
            migrated = _migrate_column(conn, table, col)
            total += migrated
            print(f"[reencrypt] {table}.{col}: 升级 {migrated} 行")
        print(f"[reencrypt] 完成,共升级 {total} 行明文为密文。")


if __name__ == "__main__":
    main()

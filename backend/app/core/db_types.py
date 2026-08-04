"""自定义 SQLAlchemy 列类型。"""
from __future__ import annotations

from sqlalchemy import BigInteger, Integer, Text
from sqlalchemy.types import TypeDecorator

from app.core import crypto

# 自增大整型主键。MySQL 用 BIGINT AUTO_INCREMENT;SQLite 不把 BIGINT 当 rowid 别名,
# 主键自增会失效(插入报 NOT NULL),故在 SQLite 侧退化成 INTEGER。
# 所有 BigInteger 主键统一用这个,避免出现「有的表能自增、有的不能」的两套约定。
BigIntPk = BigInteger().with_variant(Integer, "sqlite")


class EncryptedText(TypeDecorator):
    """透明加密的 Text 列:写入时加密、读取时解密。

    历史明文可平滑读出(crypto.decrypt 对无前缀值原样返回),下次写入即自动升级为密文。
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return crypto.encrypt(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return crypto.decrypt(value)

"""自定义 SQLAlchemy 列类型。"""
from __future__ import annotations

from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.core import crypto


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

"""库内敏感字段的对称加解密。

用于数据源密码、用户飞书 token 等落库敏感值:写时加密、读时解密(见 db_types.EncryptedText)。
密钥来自 config.SECRET_ENCRYPTION_KEY;留空则由 JWT_SECRET 派生——保证开箱可用,生产建议
单独配置 SECRET_ENCRYPTION_KEY(与 JWT_SECRET 区分,轮换 JWT 密钥时不影响已加密数据)。

密文带 `enc::v1::` 前缀,以便与历史明文无歧义地区分,支持平滑迁移(见 decrypt)。
"""
from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

_PREFIX = "enc::v1::"  # 密文标记


@lru_cache
def _fernet() -> Fernet:
    secret = settings.SECRET_ENCRYPTION_KEY or settings.JWT_SECRET
    # Fernet 需要 32 字节 urlsafe-base64 密钥:对配置密钥做 SHA-256 派生
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def encrypt(plaintext: str) -> str:
    token = _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _PREFIX + token


def decrypt(value: str) -> str:
    """解密。对历史明文(无 enc:: 前缀,或密钥变更后解不开)原样返回,保证平滑迁移与可读性。"""
    if not is_encrypted(value):
        return value  # 历史明文
    try:
        return _fernet().decrypt(value[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:
        return value

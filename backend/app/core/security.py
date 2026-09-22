"""JWT 签发与校验;开放 API token 的生成与哈希。"""
from __future__ import annotations
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from app.core.config import settings


def create_access_token(subject: str, extra: dict | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None


def create_download_token(job_id: int) -> str:
    """短期下载令牌:放进 URL query,供浏览器新标签页直接下载(无法携带 Authorization 头)。"""
    now = datetime.now(timezone.utc)
    payload = {
        "purpose": "download",
        "job_id": job_id,
        "iat": now,
        "exp": now + timedelta(seconds=settings.DOWNLOAD_URL_EXPIRE_SECONDS),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def verify_download_token(token: str) -> int | None:
    """校验下载令牌,返回 job_id;无效/过期/用途不符返回 None。"""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None
    if payload.get("purpose") != "download":
        return None
    return payload.get("job_id")


# 开放 API token 的固定前缀:一眼可辨(日志/文档里认出它),也让 v1 鉴权能在不查库时
# 就拒绝 JWT 等其它 Bearer 凭证(见 deps.get_api_user)
API_TOKEN_PREFIX = "rk_"


def generate_api_token() -> str:
    """生成一枚 API token(**明文**)。明文只在签发响应里出现一次,库里只存哈希。"""
    return API_TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_api_token(token: str) -> str:
    """token 的 SHA-256 hex(64 字符)。落库与按库查找都用它 —— 明文永不落库,
    库泄露不等于 token 泄露。token 本身已是高熵随机串,无需加盐。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

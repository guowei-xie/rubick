"""JWT 签发与校验。"""
from __future__ import annotations
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

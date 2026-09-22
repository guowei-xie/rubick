"""开放 API token 的签发 / 重置 / 吊销与校验。

每用户**单枚**长期 token(产品决策):重置就是再签发一次 —— 新 hash 覆盖旧的,
旧 token 立即失效;吊销把三列(hash / 签发时间 / 最近使用)一起清空。

库里只存 SHA-256 哈希(core/security.hash_api_token),明文只在签发响应里出现一次。
因此「重置」与「找回」不存在区别 —— 找不回,只能重来一枚。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import generate_api_token, hash_api_token
from app.models.audit import (
    ACTION_API_TOKEN_CREATE,
    ACTION_API_TOKEN_REVOKE,
    RESOURCE_USER,
)
from app.models.user import User
from app.services import audit_service

# last_used_at 的写入节流:距上次写入超过这个秒数才更新。
# Agent 轮询建议每 5s 一次,不节流的话每个请求都带一次 UPDATE;
# 「最近使用」是给「这枚 token 还活着吗」看的,分钟级精度足够。
LAST_USED_WRITE_INTERVAL_SECONDS = 60


def status_of(user: User) -> dict:
    """token 状态(给「API Token」管理页)。**永不包含 token 本体** —— 后端自己也没有。"""
    return {
        "exists": bool(user.api_token_hash),
        "issued_at": user.api_token_issued_at,
        "last_used_at": user.api_token_last_used_at,
    }


def issue(db: Session, user: User, ip: str | None = None) -> tuple[str, datetime]:
    """签发或重置(同一动作):返回 (明文 token, 签发时间),明文只存在于这次返回值里。"""
    had = bool(user.api_token_hash)  # 重置与首次签发在审计里分开说
    token = generate_api_token()
    now = datetime.now()
    user.api_token_hash = hash_api_token(token)
    user.api_token_issued_at = now
    user.api_token_last_used_at = None  # 新 token 还没有「最近使用」
    db.commit()
    audit_service.log(
        db, user=user, action=ACTION_API_TOKEN_CREATE,
        resource_type=RESOURCE_USER, resource_id=user.id, resource_name=user.name,
        # token 本体与哈希都不进 detail(_redact 也会拦 token 键,这里从源头就不放)
        detail={"replaced": had},
        ip=ip,
    )
    return token, now


def revoke(db: Session, user: User, ip: str | None = None) -> bool:
    """吊销当前 token。返回是否真的吊销了一枚(没有 token 时不留误导性的审计行)。"""
    if not user.api_token_hash:
        return False
    user.api_token_hash = None
    user.api_token_issued_at = None
    user.api_token_last_used_at = None
    db.commit()
    audit_service.log(
        db, user=user, action=ACTION_API_TOKEN_REVOKE,
        resource_type=RESOURCE_USER, resource_id=user.id, resource_name=user.name,
        ip=ip,
    )
    return True


def authenticate(db: Session, token: str) -> User | None:
    """按明文 token 找到其主人;无效 / 已吊销 / 账号已停用一律返回 None。

    「找不到」与「停用了」不分开告诉调用方:对持有无效凭证的人来说,
    这两个答案的差别本身就是信息。
    """
    user = db.scalar(select(User).where(User.api_token_hash == hash_api_token(token)))
    if user is None or not user.is_active:
        return None
    # 节流更新「最近使用」(理由见常量注释)。**在返回前 commit**:这本来就是一次独立的小写入,
    # 挂在请求会话上,失败不该拖累业务请求 —— 但它若真失败,说明库本身出问题了,照常抛出即可
    now = datetime.now()
    last = user.api_token_last_used_at
    if last is None or (now - last).total_seconds() >= LAST_USED_WRITE_INTERVAL_SECONDS:
        user.api_token_last_used_at = now
        db.commit()
    return user

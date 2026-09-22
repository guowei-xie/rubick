"""API Token 管理(Web 端,JWT 鉴权):查状态 / 签发或重置 / 吊销。

token 是「调用开放 API 的本人身份」,管理入口因此挂在 /auth 下、走 get_current_user。
明文只在 POST 的响应里出现一次;GET 永远只回状态(见 api_token_service.status_of)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.auth import ApiTokenCreateOut, ApiTokenStatusOut
from app.services import api_token_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/api-token", response_model=ApiTokenStatusOut)
def get_api_token(user: User = Depends(get_current_user)):
    """我的 token 状态:是否存在、签发时间、最近使用时间。"""
    return api_token_service.status_of(user)


@router.post("/api-token", response_model=ApiTokenCreateOut)
def create_api_token(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    ip: str | None = Depends(client_ip),
):
    """签发或重置 token(每用户单枚;重置即旧 token 立即失效)。**明文只在此返回一次**。"""
    token, issued_at = api_token_service.issue(db, user, ip=ip)
    return ApiTokenCreateOut(token=token, issued_at=issued_at)


@router.delete("/api-token")
def delete_api_token(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    ip: str | None = Depends(client_ip),
):
    """吊销当前 token。没有 token 时是幂等空操作,不记审计(同其它撤销类端点的约定)。"""
    revoked = api_token_service.revoke(db, user, ip=ip)
    return {"ok": True, "revoked": revoked}

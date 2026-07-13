from __future__ import annotations
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.user import User
from app.schemas.auth import FeishuCallbackIn, MockLoginIn, TokenOut
from app.schemas.common import UserOut
from app.services import audit_service, auth_service, feishu_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/config")
def auth_config():
    """前端据此决定展示哪些登录方式。

    配置了飞书凭证就展示飞书登录;MOCK_AUTH=true 时额外展示 mock 登录(联调期二者并存)。
    """
    feishu_ready = bool(settings.FEISHU_APP_ID)
    return {
        "mock_auth": settings.MOCK_AUTH,
        "feishu_authorize_url": feishu_service.build_authorize_url() if feishu_ready else None,
    }


@router.post("/mock-login", response_model=TokenOut)
def mock_login(data: MockLoginIn, request: Request, db: Session = Depends(get_db)):
    token, user = auth_service.mock_login(db, data.feishu_open_id)
    audit_service.log(db, user=user, action="login", detail={"mode": "mock"}, ip=client_ip(request))
    return TokenOut(access_token=token, user=UserOut.model_validate(user))


@router.post("/feishu/callback", response_model=TokenOut)
def feishu_callback(data: FeishuCallbackIn, request: Request, db: Session = Depends(get_db)):
    token, user = auth_service.login_with_code(db, data.code)
    audit_service.log(db, user=user, action="login", detail={"mode": "feishu"}, ip=client_ip(request))
    return TokenOut(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut.model_validate(user)

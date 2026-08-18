from __future__ import annotations
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.user import User
from app.schemas.auth import FeishuCallbackIn, MockLoginIn, TokenOut
from app.schemas.common import MyTeamOut, UserOut
from app.models.audit import ACTION_LOGIN
from app.services import audit_service, auth_service, feishu_service, team_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _me(db: Session, user: User) -> UserOut:
    """当前用户 + 我所属的团队。三个出口(mock 登录 / 飞书回调 / /me)共用这一处,
    免得出现「登录时拿到了团队、刷新后又没了」这种前后不一致。"""
    out = UserOut.model_validate(user)
    out.teams = [
        MyTeamOut(id=t.id, name=t.name, is_team_admin=is_admin)
        for t, is_admin in team_service.my_teams(db, user)
    ]
    return out


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
    audit_service.log(db, user=user, action=ACTION_LOGIN, detail={"mode": "mock"}, ip=client_ip(request))
    return TokenOut(access_token=token, user=_me(db, user))


@router.post("/feishu/callback", response_model=TokenOut)
def feishu_callback(data: FeishuCallbackIn, request: Request, db: Session = Depends(get_db)):
    token, user = auth_service.login_with_code(db, data.code)
    audit_service.log(db, user=user, action=ACTION_LOGIN, detail={"mode": "feishu"}, ip=client_ip(request))
    return TokenOut(access_token=token, user=_me(db, user))


@router.get("/me", response_model=UserOut)
def me(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _me(db, user)

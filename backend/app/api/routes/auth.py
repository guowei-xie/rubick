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
    """登录相关的公开配置:怎么登(展示哪些登录方式),以及登不进去时怎么申请权限。

    配置了飞书凭证就展示飞书登录;MOCK_AUTH=true 时额外展示 mock 登录(联调期二者并存)。

    申请链接放在这里而不是另开接口:它正是给**还没登录、也登不进来**的人看的,
    而本接口是全站唯一免鉴权的配置出口。没配就下发 None(不是 ""),
    让「没配」在前端只有一种形态 —— 界面据此整个隐藏入口,而不是渲一个点了没反应的按钮。

    app_base_url 也走这里:它是「对外怎么称呼本平台」的唯一真相(飞书回调、通知链接都
    从它派生),前端凡是需要生成**给别人/别的机器用**的绝对链接(如 Agent Skill 安装地址),
    都必须用它而不是浏览器地址栏 —— 用户可能正通过内网 IP 或反向代理访问。
    """
    feishu_ready = bool(settings.FEISHU_APP_ID)
    return {
        "mock_auth": settings.MOCK_AUTH,
        "feishu_authorize_url": feishu_service.build_authorize_url() if feishu_ready else None,
        "feishu_apply_url": settings.FEISHU_APP_APPLY_URL or None,
        "app_base_url": settings.APP_BASE_URL,
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

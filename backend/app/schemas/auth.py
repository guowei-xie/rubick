from __future__ import annotations
from pydantic import BaseModel

from app.schemas.common import UserOut


class MockLoginIn(BaseModel):
    """开发用:直接以某个已同步用户身份登录。"""

    feishu_open_id: str


class FeishuCallbackIn(BaseModel):
    code: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut

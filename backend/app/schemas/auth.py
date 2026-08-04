from __future__ import annotations
from pydantic import BaseModel

from app.schemas.common import UserOut


class MockLoginIn(BaseModel):
    """开发用:以指定 open_id 直接登录,库里没有该用户则按需创建。
    仅 MOCK_AUTH=true 时可用(默认关闭)。"""

    feishu_open_id: str


class FeishuCallbackIn(BaseModel):
    code: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut

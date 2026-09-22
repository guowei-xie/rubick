from __future__ import annotations
from datetime import datetime

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


class ApiTokenStatusOut(BaseModel):
    """API token 状态。**永不包含 token 本体**:库里只存哈希,后端也拿不回明文。"""

    exists: bool = False
    issued_at: datetime | None = None
    last_used_at: datetime | None = None


class ApiTokenCreateOut(BaseModel):
    """签发/重置的响应:明文 token **只在这一次响应里出现**,请立即复制保存。"""

    token: str
    issued_at: datetime

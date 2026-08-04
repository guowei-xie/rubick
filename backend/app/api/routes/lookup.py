"""授权时用的用户查找(挑授权对象)。管理者(管理员/开发者)可用。

有关键词时实时搜飞书通讯录(按本人可见范围),**只返回候选、不落库**——用户行推迟到真正
授权那一刻才按 open_id 生成(见 permissions.grant),避免搜索即制造壳用户、撑大用户表。
空查询只回历史(之前授权过的人)。不再有本地全员目录兜底 —— 飞书不可用时返回空。
候选统一带 open_id,前端授权时回传;历史用户额外带其已落库的 id。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_manager
from app.core.database import get_db
from app.models.permission import Permission
from app.models.user import User
from app.services import feishu_service

router = APIRouter(prefix="/lookup", tags=["lookup"])


@router.get("/users")
def lookup_users(q: str | None = None, db: Session = Depends(get_db), user: User = Depends(require_manager)):
    q = (q or "").strip()
    if not q:
        # 空查询:只显示历史 —— 之前被授权过的人
        granted = db.scalars(
            select(Permission.subject_id).where(Permission.subject_type == "user").distinct()
        )
        ids = [int(x) for x in granted if x and x.isdigit()]
        rows = list(db.scalars(select(User).where(User.id.in_(ids or [-1])).order_by(User.name).limit(50)))
        return [_user_row(u) for u in rows]

    # 有关键词:用当前管理员的 user_access_token 实时搜全公司;拿不到/失败则返回空
    token = feishu_service.valid_user_token(db, user)
    if not token:
        return []
    try:
        found = feishu_service.search_users(q, token)
    except Exception:  # noqa: BLE001 飞书失败 → 返回空,让前端提示重试
        return []

    # 只回候选、零 DB 写:落库推迟到 permissions.grant(按 open_id upsert)。
    return [
        {
            "id": None,  # 尚未落库;授权时才生成
            "open_id": u["open_id"],
            "name": u.get("name"),
            "email": u.get("email"),
            "avatar": u.get("avatar"),
            "employee_id": u.get("employee_id"),
        }
        for u in found
    ]


def _user_row(u: User, employee_id: str | None = None) -> dict:
    return {
        "id": u.id,
        "open_id": u.feishu_open_id,
        "name": u.name,
        "email": u.email,
        "avatar": u.avatar,
        "employee_id": employee_id,
    }

"""飞书接入。

- 登录:OAuth code 换用户信息(authen,不需通讯录权限)
- 授权选人:search_users 实时按本人可见范围搜通讯录(需 contact:user:search 权限)
- 通知:send_message 机器人卡片(需 im:message 权限)

所有请求走直连(trust_env=False):规避本地/沙箱注入的不稳定代理;真机无代理时行为一致。
tenant_access_token 带内存缓存(飞书有效期约 2h)。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import RubicError

_BASE = "https://open.feishu.cn/open-apis"
# 直连,不读取环境代理;飞书接口偶发慢,给足超时
_client = httpx.Client(trust_env=False, timeout=20)

_token_cache: dict = {"token": None, "exp": 0.0}

# 登录申请的权限:含「按本人可见范围搜通讯录」,才能在授权里搜全公司
OAUTH_SCOPES = " ".join(
    [
        "contact:user.base:readonly",
        "contact:department.base:readonly",
        "contact:contact.base:readonly",
        "contact:user:search",
    ]
)


def build_authorize_url(state: str = "rubic") -> str:
    # authorize 端点支持 scope;与 oidc/access_token 交换配套
    q = urlencode(
        {
            "app_id": settings.FEISHU_APP_ID,
            "redirect_uri": settings.FEISHU_REDIRECT_URI,
            "scope": OAUTH_SCOPES,
            "state": state,
        }
    )
    return f"{_BASE}/authen/v1/authorize?{q}"


def _tenant_access_token() -> str:
    if _token_cache["token"] and time.time() < _token_cache["exp"]:
        return _token_cache["token"]
    resp = _client.post(
        f"{_BASE}/auth/v3/tenant_access_token/internal",
        json={"app_id": settings.FEISHU_APP_ID, "app_secret": settings.FEISHU_APP_SECRET},
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RubicError(f"飞书鉴权失败:{data.get('msg')}")
    _token_cache["token"] = data["tenant_access_token"]
    _token_cache["exp"] = time.time() + data.get("expire", 7000) - 120
    return _token_cache["token"]


def _post_data(path: str, body: dict) -> dict:
    """带 tenant token 的 POST,校验 code==0,返回 data。"""
    resp = _client.post(
        f"{_BASE}{path}",
        headers={"Authorization": f"Bearer {_tenant_access_token()}"},
        json=body,
    )
    resp.raise_for_status()
    j = resp.json()
    if j.get("code") not in (0, None):
        raise RubicError(f"飞书接口失败({path}):{j.get('msg')}")
    return j.get("data", j)


def exchange_code(code: str) -> dict:
    """OIDC 换取用户身份。返回 {open_id, union_id, name, email, avatar,
    access_token, refresh_token, expires_in}(后三者是 user_access_token 相关,用于搜通讯录)。"""
    tok = _post_data("/authen/v1/oidc/access_token", {"grant_type": "authorization_code", "code": code})
    user_token = tok["access_token"]

    # 用 user_access_token 取用户资料
    resp = _client.get(
        f"{_BASE}/authen/v1/user_info", headers={"Authorization": f"Bearer {user_token}"}
    )
    resp.raise_for_status()
    info = resp.json().get("data", {})
    return {
        "open_id": info.get("open_id") or tok.get("open_id"),
        "union_id": info.get("union_id"),
        "name": info.get("name", "飞书用户"),
        "email": info.get("email") or info.get("enterprise_email"),
        "avatar": info.get("avatar_url"),
        "access_token": user_token,
        "refresh_token": tok.get("refresh_token"),
        "expires_in": tok.get("expires_in", 7000),
    }


def refresh_user_token(refresh_token: str) -> dict:
    """用 refresh_token 换新的 user_access_token。返回 {access_token, refresh_token, expires_in}。"""
    data = _post_data(
        "/authen/v1/oidc/refresh_access_token",
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
    )
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", refresh_token),
        "expires_in": data.get("expires_in", 7000),
    }


def valid_user_token(db: Session, user) -> str | None:
    """拿到该用户当前可用的 user_access_token;过期则用 refresh_token 刷新并落库。
    没有 token 或刷新失败返回 None —— 此时授权选人搜不出候选(无本地目录兜底,见 routes/lookup.py)。"""
    if not user.feishu_token:
        return None
    now = datetime.utcnow()
    if user.feishu_token_exp and user.feishu_token_exp > now + timedelta(seconds=60):
        return user.feishu_token
    if not user.feishu_refresh_token:
        return None
    try:
        fresh = refresh_user_token(user.feishu_refresh_token)
    except Exception:  # noqa: BLE001 刷新失败 → 回退
        return None
    user.feishu_token = fresh["access_token"]
    user.feishu_refresh_token = fresh["refresh_token"]
    user.feishu_token_exp = now + timedelta(seconds=int(fresh["expires_in"]))
    db.commit()
    return user.feishu_token


def search_users(query: str, user_access_token: str) -> list[dict]:
    """按登录用户可见范围搜通讯录。返回 [{open_id, name, email, avatar, employee_id}]。
    /search/v1/user 常只回 open_id + avatar,姓名/邮箱用 tenant token 批量补齐。"""
    resp = _client.get(
        f"{_BASE}/search/v1/user",
        headers={"Authorization": f"Bearer {user_access_token}"},
        params={"query": query, "page_size": 100},
    )
    resp.raise_for_status()
    found = resp.json().get("data", {}).get("users", []) or []
    open_ids = [u.get("open_id") for u in found if u.get("open_id")]
    # 补姓名是尽力而为:失败不能连累搜索结果(否则会被外层回退成「本地库中文 LIKE」,拼音就搜不到了)
    try:
        meta = _batch_get_users(open_ids)
    except Exception:  # noqa: BLE001
        meta = {}
    out = []
    for u in found:
        oid = u.get("open_id")
        if not oid:
            continue
        m = meta.get(oid, {})
        out.append(
            {
                "open_id": oid,
                "name": m.get("name") or u.get("name") or oid,
                "email": m.get("email") or m.get("enterprise_email"),
                # 补齐资料和 search 原始返回里 avatar 都是对象({avatar_72/240/…}),
                # 统一取 avatar_72 字符串;取不到落 None,绝不把 dict 写进库(否则 upsert 会炸)
                "avatar": _avatar_url(m.get("avatar") or u.get("avatar")),
                # user_id 即企业工号(如 HT13212);search 直接返回,批量接口作兜底
                "employee_id": u.get("user_id") or m.get("user_id"),
            }
        )
    return out


def _avatar_url(avatar) -> str | None:
    """飞书头像可能是 {avatar_72/240/640/origin} 对象或已是字符串;统一取一个字符串 URL。"""
    if isinstance(avatar, dict):
        return avatar.get("avatar_72") or avatar.get("avatar_240") or avatar.get("avatar_origin")
    return avatar or None


def _batch_get_users(open_ids: list[str]) -> dict[str, dict]:
    """按 open_id 批量取用户资料(tenant token,需 contact 权限)。返回 {open_id: item}。"""
    if not open_ids:
        return {}
    resp = _client.get(
        f"{_BASE}/contact/v3/users/batch",
        headers={"Authorization": f"Bearer {_tenant_access_token()}"},
        params=[("user_ids", i) for i in open_ids] + [("user_id_type", "open_id")],
    )
    resp.raise_for_status()
    items = (resp.json().get("data", {}) or {}).get("items", []) or []
    return {it.get("open_id"): it for it in items if it.get("open_id")}


def send_message(open_id: str, title: str, content: str, link: str | None = None) -> bool:
    """给指定用户发飞书消息(交互卡片)。返回是否发送成功;未配置凭证或失败返回 False。"""
    if not settings.FEISHU_APP_ID:
        return False
    try:
        app_token = _tenant_access_token()
        elements = [{"tag": "div", "text": {"tag": "lark_md", "content": content}}]
        if link:
            elements.append(
                {"tag": "action", "actions": [
                    {"tag": "button", "text": {"tag": "plain_text", "content": "查看/下载"},
                     "type": "primary", "url": link}
                ]}
            )
        card = {"header": {"title": {"tag": "plain_text", "content": title}}, "elements": elements}
        resp = _client.post(
            f"{_BASE}/im/v1/messages",
            headers={"Authorization": f"Bearer {app_token}"},
            params={"receive_id_type": "open_id"},
            json={"receive_id": open_id, "msg_type": "interactive", "content": json.dumps(card)},
        )
        return resp.json().get("code") == 0
    except Exception:
        return False

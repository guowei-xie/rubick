"""飞书接入。

- 登录:OAuth code 换用户信息(authen,不需通讯录权限)
- 组织同步:sync_contacts 拉取部门 + 用户(需 contact 通讯录读取权限)
- 通知:send_message 机器人卡片(需 im:message 权限)

所有请求走直连(trust_env=False):规避本地/沙箱注入的不稳定代理;真机无代理时行为一致。
tenant_access_token 带内存缓存(飞书有效期约 2h)。
"""
from __future__ import annotations

import json
import time
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import RubicError

_BASE = "https://open.feishu.cn/open-apis"
# 直连,不读取环境代理;飞书接口偶发慢,给足超时
_client = httpx.Client(trust_env=False, timeout=20)

_token_cache: dict = {"token": None, "exp": 0.0}


def build_authorize_url(state: str = "rubic") -> str:
    # 规范编码 redirect_uri;endpoint 用经典网页登录入口 authen/v1/index,与 access_token 交换配套
    q = urlencode(
        {"app_id": settings.FEISHU_APP_ID, "redirect_uri": settings.FEISHU_REDIRECT_URI, "state": state}
    )
    return f"{_BASE}/authen/v1/index?{q}"


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


def exchange_code(code: str) -> dict:
    """用登录 code 换取用户信息。返回 {open_id, name, email, avatar, union_id}。"""
    app_token = _tenant_access_token()
    resp = _client.post(
        f"{_BASE}/authen/v1/access_token",
        headers={"Authorization": f"Bearer {app_token}"},
        json={"grant_type": "authorization_code", "code": code},
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 0:
        raise RubicError(f"飞书登录失败:{body.get('msg')}")
    data = body["data"]
    return {
        "open_id": data["open_id"],
        "union_id": data.get("union_id"),
        "name": data.get("name", "飞书用户"),
        "email": data.get("email") or data.get("enterprise_email"),
        "avatar": data.get("avatar_url"),
    }


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


# ---------------- 通讯录同步 ----------------

def _paged(path: str, headers: dict, params: dict) -> list[dict]:
    """通用分页拉取,返回 data.items 汇总。"""
    items: list[dict] = []
    page_token = None
    while True:
        p = dict(params)
        if page_token:
            p["page_token"] = page_token
        r = _client.get(f"{_BASE}{path}", headers=headers, params=p).json()
        if r.get("code") != 0:
            raise RubicError(f"飞书通讯录读取失败({path}):{r.get('msg')}")
        data = r.get("data", {}) or {}
        items.extend(data.get("items") or [])
        page_token = data.get("page_token")
        if not data.get("has_more"):
            break
    return items


def _scope_department_ids(headers: dict) -> list[str]:
    ids: list[str] = []
    page_token = None
    while True:
        p = {"page_size": 50}
        if page_token:
            p["page_token"] = page_token
        r = _client.get(f"{_BASE}/contact/v3/scopes", headers=headers, params=p).json()
        if r.get("code") != 0:
            raise RubicError(
                "通讯录权限未开通:请在飞书开放平台为应用开通 contact 通讯录读取权限并发布。"
                f"(原始:{r.get('msg')})"
            )
        data = r.get("data", {}) or {}
        ids.extend(data.get("department_ids") or [])
        page_token = data.get("page_token")
        if not data.get("has_more"):
            break
    return ids


def sync_contacts(db: Session) -> dict:
    """把飞书通讯录(部门 + 用户)同步进平台库。返回 {departments, users}。

    需要应用已开通 contact 通讯录读取权限。按应用可见范围(scopes)拉取。
    """
    from app.models.user import Department, User  # 延迟导入避免循环
    from app.services import user_service

    headers = {"Authorization": f"Bearer {_tenant_access_token()}"}

    # 1) 应用可见的顶层部门,连同其所有子部门
    scope_ids = _scope_department_ids(headers) or ["0"]
    seen: dict[str, dict] = {}
    for did in scope_ids:
        for dep in _paged(
            "/contact/v3/departments",
            headers,
            {"parent_department_id": did, "fetch_child": True, "page_size": 50},
        ):
            seen[dep.get("open_department_id") or dep["department_id"]] = dep
        # 顶层部门本身也补一条
        if did != "0":
            r = _client.get(f"{_BASE}/contact/v3/departments/{did}", headers=headers).json()
            if r.get("code") == 0 and r.get("data", {}).get("department"):
                dep = r["data"]["department"]
                seen[dep.get("open_department_id") or dep["department_id"]] = dep

    # upsert 部门
    for fid, dep in seen.items():
        user_service.upsert_department(db, fid, dep.get("name", ""), dep.get("parent_department_id"))
    db.commit()

    # 部门 feishu_id -> 内部 id 映射
    dept_map = {d.feishu_dept_id: d.id for d in db.scalars(select(Department))}

    # 2) 每个部门下的用户
    for fid in seen:
        for u in _paged(
            "/contact/v3/users/find_by_department",
            headers,
            {"department_id": fid, "page_size": 50},
        ):
            if not u.get("open_id"):
                continue
            row = user_service.upsert_user(db, {
                "open_id": u["open_id"],
                "union_id": u.get("union_id"),
                "name": u.get("name"),
                "email": u.get("email") or u.get("enterprise_email"),
                "avatar": (u.get("avatar") or {}).get("avatar_72"),
            })
            dept_ids = u.get("department_ids") or []
            if dept_ids and dept_ids[0] in dept_map:
                row.department_id = dept_map[dept_ids[0]]
    db.commit()

    return {"departments": len(seen), "users": db.query(User).count()}

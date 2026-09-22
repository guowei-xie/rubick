"""API Token:签发/重置/吊销、鉴权(含与 JWT 通道的分离)、last_used_at 节流。

与仓库既有测试同风格:直接调路由函数与服务函数,不起 TestClient。
库不按用例清理,故每个用例占一个独立用户 id,避免 token 状态在用例间串扰。
"""
from datetime import datetime, timedelta

import pytest

from app.api.deps import get_api_user
from app.api.routes.api_tokens import create_api_token, delete_api_token, get_api_token
from app.core.exceptions import UnauthorizedError
from app.core.security import create_access_token, generate_api_token, hash_api_token
from app.models import audit as A
from app.models.user import ROLE_USER
from app.services import api_token_service
from tests.conftest import max_audit_id, new_audit_rows, one_audit_row

# ID 段 9310–9315
U_STATUS, U_RESET, U_REVOKE, U_THROTTLE, U_INACTIVE, U_DEP = 9310, 9311, 9312, 9313, 9314, 9315


@pytest.fixture
def u_status(user_factory):
    return user_factory(U_STATUS, ROLE_USER, "令牌用户·状态", prefix="tok")


@pytest.fixture
def u_reset(user_factory):
    return user_factory(U_RESET, ROLE_USER, "令牌用户·重置", prefix="tok")


@pytest.fixture
def u_revoke(user_factory):
    return user_factory(U_REVOKE, ROLE_USER, "令牌用户·吊销", prefix="tok")


@pytest.fixture
def u_throttle(user_factory):
    return user_factory(U_THROTTLE, ROLE_USER, "令牌用户·节流", prefix="tok")


@pytest.fixture
def u_inactive(user_factory):
    return user_factory(U_INACTIVE, ROLE_USER, "令牌用户·停用", prefix="tok")


@pytest.fixture
def u_dep(user_factory):
    return user_factory(U_DEP, ROLE_USER, "令牌用户·依赖", prefix="tok")


def test_generate_and_hash():
    token = generate_api_token()
    assert token.startswith("rk_")
    # urlsafe(32) ≈ 43 字符,加前缀后足够长;高熵随机串,无需加盐
    assert len(token) > 40
    h = hash_api_token(token)
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)
    assert hash_api_token(token) == h  # 确定性:落库与查找用同一个哈希


def test_issue_and_status(db, u_status):
    # 起始状态:没有 token,三个字段都不会出现
    # (直接调路由函数拿到的是 response_model 序列化之前的 dict,故按键断言)
    st = get_api_token(u_status)
    assert st["exists"] is False and st["issued_at"] is None and st["last_used_at"] is None

    since = max_audit_id(db)
    out = create_api_token(db, u_status, ip=None)
    assert out.token.startswith("rk_") and out.issued_at is not None

    st = get_api_token(u_status)
    assert st["exists"] is True and st["issued_at"] == out.issued_at
    # 响应形状里任何地方都不该再次给出明文(状态接口只回元信息)
    assert "token" not in st

    row = one_audit_row(db, since)
    assert row.action == A.ACTION_API_TOKEN_CREATE
    assert row.resource_type == A.RESOURCE_USER and row.resource_id == str(u_status.id)
    assert row.detail["replaced"] is False
    # 明文与哈希都不进审计
    assert out.token not in str(row.detail) and u_status.api_token_hash not in str(row.detail)


def test_reset_invalidates_old_token(db, u_reset):
    old, _ = api_token_service.issue(db, u_reset)
    assert api_token_service.authenticate(db, old) is not None

    since = max_audit_id(db)
    new, _ = api_token_service.issue(db, u_reset)
    assert new != old
    # 重置 = 签发一枚新的并让旧的立即失效
    assert api_token_service.authenticate(db, old) is None
    assert api_token_service.authenticate(db, new).id == u_reset.id

    row = one_audit_row(db, since)
    assert row.action == A.ACTION_API_TOKEN_CREATE
    assert row.detail["replaced"] is True


def test_revoke(db, u_revoke):
    token, _ = api_token_service.issue(db, u_revoke)

    since = max_audit_id(db)
    assert delete_api_token(db, u_revoke, ip=None) == {"ok": True, "revoked": True}
    assert api_token_service.authenticate(db, token) is None
    row = one_audit_row(db, since)
    assert row.action == A.ACTION_API_TOKEN_REVOKE
    assert row.resource_type == A.RESOURCE_USER and row.resource_id == str(u_revoke.id)
    assert get_api_token(u_revoke)["exists"] is False

    # 再吊销一次:什么都没发生,不留误导性的「吊销」记录(同其它撤销类端点的约定)
    since = max_audit_id(db)
    assert delete_api_token(db, u_revoke, ip=None) == {"ok": True, "revoked": False}
    assert new_audit_rows(db, since) == []


def test_last_used_at_is_throttled(db, u_throttle):
    token, _ = api_token_service.issue(db, u_throttle)
    assert u_throttle.api_token_last_used_at is None

    api_token_service.authenticate(db, token)
    db.refresh(u_throttle)
    first = u_throttle.api_token_last_used_at
    assert first is not None

    # 60 秒内的第二次调用不重复写库(Agent 轮询每 5s 一次,不节流就是每 5s 一次 UPDATE)
    api_token_service.authenticate(db, token)
    db.refresh(u_throttle)
    assert u_throttle.api_token_last_used_at == first

    # 把上次写入拨回 61 秒前:这次该更新了
    u_throttle.api_token_last_used_at = datetime.now() - timedelta(seconds=61)
    db.commit()
    api_token_service.authenticate(db, token)
    db.refresh(u_throttle)
    assert u_throttle.api_token_last_used_at > first


def test_inactive_user_token_is_rejected(db, u_inactive):
    token, _ = api_token_service.issue(db, u_inactive)
    u_inactive.is_active = False
    db.commit()
    # 「找不到」与「停用了」不区分地拒绝(见 authenticate 的注释)
    assert api_token_service.authenticate(db, token) is None


def test_get_api_user_dependency(db, u_dep):
    token, _ = api_token_service.issue(db, u_dep)
    assert get_api_user(authorization=f"Bearer {token}", db=db).id == u_dep.id

    # 登录 JWT 不能调 v1:鉴权通道分离,审计里的 via 才无歧义
    jwt_token = create_access_token(str(u_dep.id))
    with pytest.raises(UnauthorizedError):
        get_api_user(authorization=f"Bearer {jwt_token}", db=db)
    # rk_ 前缀但库里没有(伪造/已重置的旧值)
    with pytest.raises(UnauthorizedError):
        get_api_user(authorization="Bearer rk_no-such-token", db=db)
    # 缺头 / 非 Bearer
    with pytest.raises(UnauthorizedError):
        get_api_user(authorization=None, db=db)
    with pytest.raises(UnauthorizedError):
        get_api_user(authorization=f"Token {token}", db=db)


def test_authenticate_writes_one_audit_only_at_issue(db, u_dep):
    """authenticate 本身**不记审计**:它是每个 API 请求的必经之路,
    记了会把 audit_logs 淹没在轮询噪声里(治理事件就看不见了)。"""
    since = max_audit_id(db)
    token, _ = api_token_service.issue(db, u_dep)
    api_token_service.authenticate(db, token)
    rows = new_audit_rows(db, since)
    assert [r.action for r in rows] == [A.ACTION_API_TOKEN_CREATE]

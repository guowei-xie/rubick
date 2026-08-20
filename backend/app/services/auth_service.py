"""登录:mock 或飞书 OAuth,统一产出平台 JWT。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import UnauthorizedError
from app.core.security import create_access_token
from app.models.user import ROLE_ADMIN, User
from app.services import feishu_service, user_service


def _bootstrap_admins() -> set[str]:
    return {x.strip().lower() for x in settings.BOOTSTRAP_ADMINS.split(",") if x.strip()}


def _maybe_promote(user: User, allow: set[str]) -> bool:
    """命中 BOOTSTRAP_ADMINS(邮箱/open_id)则提升为管理员(只升不降,手动调整仍生效)。
    返回是否发生提权。登录与启动提权共用同一条规则。"""
    if not allow or user.role == ROLE_ADMIN:
        return False
    ident = {(user.email or "").lower(), user.feishu_open_id.lower()}
    if ident & allow:
        user.role = ROLE_ADMIN
        return True
    return False


def apply_bootstrap_admins(db: Session) -> int:
    """启动时把已存在用户过一遍提权规则(与登录同一条 _maybe_promote)。

    让「配置文件加超管」在生产中可靠且即时:目标用户已在库中(此前登录过,或被授权时落过库)时,
    改配置重启即提权,无需其再登录一次。返回本次新提权人数。幂等。
    """
    allow = _bootstrap_admins()
    if not allow:
        return 0
    pending = list(db.scalars(select(User).where(User.role != ROLE_ADMIN)))
    # 名单里有邮箱形式的条目时,先把缺邮箱的人补一次 —— 一次批量请求,且仅在配了邮箱时才打。
    # 不补的话「配置写邮箱 + 重启」对从未登录过的人永远不生效,与 README 的承诺不符。
    if any("@" in x for x in allow):
        user_service.sync_profiles_from_feishu([u for u in pending if not u.email])
    promoted = 0
    for user in pending:
        if _maybe_promote(user, allow):
            promoted += 1
    if promoted:
        db.commit()
    return promoted


def _upsert_user(db: Session, profile: dict) -> User:
    user = user_service.upsert_user(db, profile)

    # 邮箱兜底:登录返回的 profile 不一定带 email(取决于 OAUTH_SCOPES 里有没有
    # contact:user.email:readonly),用通讯录补一次。失败静默,不影响登录。
    if not user.email:
        user_service.sync_profiles_from_feishu([user])

    # ⚠️ 顺序:提权按邮箱匹配 BOOTSTRAP_ADMINS,必须在上面补完邮箱之后 ——
    # 否则「配置里写邮箱」对首次登录的人永远不生效(且失败完全静默)。
    # tests/test_user_email.py::test_login_promotes_by_email_via_backfill 锁住这个顺序。
    _maybe_promote(user, _bootstrap_admins())  # 引导管理员:命中名单自动提升

    user.last_login_at = datetime.now(timezone.utc)  # 标记已登录 → 才会出现在用户管理
    # 存 user_access_token(用于按本人可见范围搜通讯录);exp 用 naive UTC 便于比较
    if profile.get("access_token"):
        user.feishu_token = profile["access_token"]
        user.feishu_refresh_token = profile.get("refresh_token")
        user.feishu_token_exp = datetime.utcnow() + timedelta(seconds=int(profile.get("expires_in", 7000)))
    db.commit()
    db.refresh(user)
    return user


def login_with_code(db: Session, code: str) -> tuple[str, User]:
    profile = feishu_service.exchange_code(code)
    user = _upsert_user(db, profile)
    return create_access_token(str(user.id)), user


def mock_login(db: Session, feishu_open_id: str) -> tuple[str, User]:
    """开发用 mock 登录。注意:信任前端传入的 open_id、无凭证校验,命中 BOOTSTRAP_ADMINS
    即自动提权——故由 MOCK_AUTH 开关严格保护,默认关闭,生产切勿开启(见 config.MOCK_AUTH)。"""
    if not settings.MOCK_AUTH:
        raise UnauthorizedError("mock 登录未启用")
    # 第二道护栏(第一道在 config._guard_mock_auth,启动即拒)。这里挡的是运行期把
    # MOCK_AUTH 改成 true 的情形:下面几行会 JIT 建号,连着远端库就是往正式库灌假账号。
    if not settings.DATABASE_IS_LOCAL:
        raise UnauthorizedError(
            f"mock 登录只允许连本地库使用,当前库是 {settings.database_display}"
        )
    user = db.scalar(select(User).where(User.feishu_open_id == feishu_open_id))
    if user is None:
        # mock 环境下按需创建(JIT),便于空库首次登录(无需 seed)
        user = User(feishu_open_id=feishu_open_id, name=feishu_open_id)
        db.add(user)
        db.flush()

    _maybe_promote(user, _bootstrap_admins())  # 引导管理员:命中名单自动提升

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return create_access_token(str(user.id)), user

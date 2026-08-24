"""用户的 upsert(按飞书 open_id)、资料补齐与平台角色变更。中立位置,供 auth 登录、
授权选人实时搜索与管理端共用,避免相互 import 造成循环依赖。不提交,由调用方统一 commit。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import ROLE_USER, User
from app.services import feishu_service


def apply_profile(user: User, profile: dict) -> User:
    """把资料合并到**已持有的** User 对象上(空值不覆盖)。不查库、不提交。

    与 upsert_user 分开,是因为「已经拿着对象」的调用方(补齐、回填)再查一次库不只是浪费:
    新建的用户还没 flush 时(SessionLocal 是 autoflush=False),那次 select 查不到它,
    会把同一个 open_id 建成第二行 —— commit 时撞 unique 约束。
    """
    user.union_id = profile.get("union_id") or user.union_id
    user.name = profile.get("name") or user.name
    user.email = profile.get("email") or user.email
    user.avatar = profile.get("avatar") or user.avatar
    return user


def upsert_user(db: Session, profile: dict) -> User:
    """按 open_id upsert 用户,合并可用的资料字段(空值不覆盖)。"""
    user = db.scalar(select(User).where(User.feishu_open_id == profile["open_id"]))
    if user is None:
        user = User(feishu_open_id=profile["open_id"], name=profile.get("name", "飞书用户"))
        db.add(user)
    return apply_profile(user, profile)


def sync_profiles_from_feishu(users: list[User]) -> int:
    """用飞书通讯录(tenant token)补齐这批用户的资料。返回被补到资料的人数。

    这是 email 落库的**唯一可信来源**:通讯录不依赖对方登录,故「授权时落库、从不登录」的
    用户也能补上。合并走 apply_profile(空值不覆盖),不另写一份规则,也不重新查库
    —— 传进来的对象可能是尚未 flush 的新用户。

    **绝不抛异常。**通讯录不可用、凭证没配、或人在应用「授权范围」外时静默返回 0 ——
    登录和授权都不能因为补邮箱失败而失败。

    不做缓存、不加 contact_synced_at 列:调用方只在 email 为空时才调,补上一次后此人
    永不再触发;只有「授权范围外的人」每次登录会多打一次飞书。本平台用户是个位数,
    登录频率就是天花板,加负缓存或加一列都是过度设计。量级变大时再在 feishu_service 里
    照 _token_cache 的写法加模块级负缓存。
    """
    targets = [u for u in users if u.feishu_open_id]
    if not targets:
        return 0
    try:
        profiles = feishu_service.fetch_contact_profiles([u.feishu_open_id for u in targets])
    except Exception:  # noqa: BLE001 通讯录不能连累调用方
        return 0
    filled = 0
    for user in targets:
        profile = profiles.get(user.feishu_open_id)
        if not profile:  # 授权范围外/已离职 → 跳过,不动现有资料
            continue
        apply_profile(user, profile)
        filled += 1
    return filled


def change_role(db: Session, user: User, role: str) -> list[dict] | None:
    """改平台角色,并做与之绑定的连带回收。不提交,由调用方统一 commit。

    降成普通用户 = 一次实质的数据权限回收,必须连带清队 —— 团队成员资格本身就是数据边界,
    只改角色的话他照样看得到并跑得动原团队的全部任务(permission_service.is_insider 只看
    团队关系,不看平台角色)。只在降为 ROLE_USER 时清:提成管理员本就全通,清队没有意义
    还会误伤。收在这里而不是路由里:将来任何一条改角色的路径(批量脚本、离职自动化)
    都必须绕不开这条不变量。

    返回值即审计要记的 removed_from_teams:清出的团队列表;[] = 清了队但他本来就不在
    任何团队;None = 本次动作不涉及清队(提权 / 平调)。
    """
    from app.services import team_service  # 惰性引入,避免 service 间顶层循环依赖

    user.role = role
    return team_service.remove_from_all_teams(db, user) if role == ROLE_USER else None


def name_or_email_like(q: str | None):
    """「按姓名或邮箱模糊匹配」的筛选条件;q 为空返回 None(表示不过滤)。

    管理端用户列表与团队候选人列表共用 —— 同一条检索规则只表述一次,免得加了拼音/工号
    匹配后两个页面行为不一致。用 LIKE '%q%' 而不建索引的理由见 models/user.py。
    """
    q = (q or "").strip()
    if not q:
        return None
    like = f"%{q}%"
    return User.name.like(like) | User.email.like(like)

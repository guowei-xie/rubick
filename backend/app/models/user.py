"""用户。两个入口按需创建(JIT):① 首次飞书扫码登录;② 授权选人时被**真正授权**的通讯录成员
(按 open_id upsert)。搜通讯录本身不落库,避免搜索即制造壳用户 —— 见 routes/lookup.py。"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, tbl
from app.core.db_types import BigIntPk, EncryptedText
from app.models.mixins import TimestampMixin

# 平台角色:三种。管理员可建/改/上线任意任务并互相可见,且独揽治理(用户角色赋权/数据源/审计);
# 开发者近似管理员但不含这三块治理;普通用户只填参取数。
ROLE_USER = "user"          # 普通用户(业务使用者)
ROLE_ADMIN = "admin"        # 管理员(含 SQL 编写与上线职责)
ROLE_DEVELOPER = "developer"  # 开发者:只在自己所属团队内取值(见 services/permission_service)

# 订阅定时运行的系统身份(migrate._ensure_system_scheduler_user 幂等创建)。
# QueryJob.user_id 非空,而定时运行没有发起人 —— 也**不能**填任务作者:
# can_access_job 有「user_id == 本人」的短路,作者离队后仍会借订阅记录看到结果。
# is_active=False 保证这个账号永远登录不进来,它只出现在运行记录的「运行人」一栏。
SYSTEM_SCHEDULER_OPEN_ID = "rubick-system-scheduler"
SYSTEM_SCHEDULER_NAME = "定时运行"


def is_platform_admin(user: "User") -> bool:
    """平台管理员:不受团队约束,可见且可编辑所有团队的所有任务。

    **唯一的「全通」来源**。放在角色常量旁边(而不是 permission_service)是因为它只是一条
    对 role 的判断,不依赖任何服务层 —— 搁在上层会逼着 team_service / credential_service
    为这一个谓词做延迟导入,把真实的依赖图藏起来。permission_service 会转出它,
    「全通判定只表述一次」的口径不变。
    """
    return user.role == ROLE_ADMIN


class User(Base, TimestampMixin):
    __tablename__ = tbl("users")

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    feishu_open_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    union_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    # 三个来源,合并规则一律「空值不覆盖」(user_service.upsert_user):
    #   ① 登录:OAuth user_info(需 OAUTH_SCOPES 里有 contact:user.email:readonly)
    #   ② 授权落库 / 登录兜底:飞书通讯录(tenant token,唯一可信来源)
    #   ③ 存量补齐:app/backfill_user_emails.py
    # **刻意不加 unique、不加索引**:两处检索都是 LIKE '%q%'(前导通配符用不上 B-tree 索引),
    # 而 users 表只有个位数行;加 unique 更有害 —— upsert 主键是 open_id,唯一约束换不来任何
    # 保证,却给回填引入新的崩溃模式(同一人双账号 / 离职邮箱复用 → IntegrityError)。
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    avatar: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default=ROLE_USER, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    # 最近登录时间;仅登录过的用户才在「用户管理」里展示
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # 飞书 user_access_token(登录时获取),用于按本人可见范围搜通讯录;约 2h 过期,用 refresh 刷新。
    # 落库前透明加密(EncryptedText),读取时自动解密。
    feishu_token: Mapped[Optional[str]] = mapped_column(EncryptedText, nullable=True)
    feishu_refresh_token: Mapped[Optional[str]] = mapped_column(EncryptedText, nullable=True)
    feishu_token_exp: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # 开放 API token(每用户**单枚**,重置即旧的失效)。库里只存 SHA-256 hex(64 字符),
    # 明文只在签发响应里出现一次、永不落库 —— 库泄露不等于 token 泄露。
    # 三列同生同灭:hash 为 None 即「没有 token」,另两列也无意义(签发/吊销成对维护,
    # 见 services/api_token_service)。
    api_token_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    api_token_issued_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # 节流写入:距上次写入超过 60s 才更新(见 api_token_service.authenticate),
    # 否则轮询中的 Agent 每 5 秒就带来一次 UPDATE
    api_token_last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

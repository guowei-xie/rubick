"""团队的取数账号 —— 让数据权限由数据库侧裁决。

平台的公共账号(DataSource.username/password)能看到该库的全部数据,任何能建任务的人都可借它越权取数。
本表让每个**团队**在每个数据源上登记一套库账号,任务运行时用**任务所属团队**的账号取数
(见 services/credential_service.for_template),取不到的数据由数据库自己拒绝,
平台不再需要模拟一套数据权限。

**为什么粒度是团队而不是人**:账号挂在人头上时,作者离职/换岗任务即失效,且账号维护责任散落到
每个开发者;更糟的是「试跑用本人账号、正式取数用作者账号」——试跑通过并不代表上线后能跑。
上移到团队后,两者是同一套身份,**试跑通过即代表上线后能跑**,维护责任也集中到团队管理员。

粒度是「团队 × 数据源」:不同引擎(Hive / MySQL)的账号体系本就不同,一个团队一套全局账号表达不了。
密码走 EncryptedText 透明加密,与数据源密码、飞书 token 同一套 Fernet 机制(见 core/crypto.py)。

**密码任何人不可见**:没有任何 Out 模型带 password(见 schemas/credential.py),
平台管理员也读不到。库**用户名**属半机密——Hive 在 auth=NONE 下用户名本身就是完整凭证——
故仅该团队的团队管理员、平台管理员与审计日志可见(见 credential_service 的 reveal_username)。
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, tbl
from app.core.db_types import EncryptedText
from app.models.mixins import TimestampMixin


class TeamDataSourceCredential(Base, TimestampMixin):
    """某个团队在某数据源上的取数身份。一队一源恒定一行。"""

    __tablename__ = tbl("team_datasource_credentials")
    __table_args__ = (
        UniqueConstraint("team_id", "datasource_id", name="uq_team_ds_cred"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(tbl("teams.id")), index=True)
    datasource_id: Mapped[int] = mapped_column(ForeignKey(tbl("data_sources.id")), index=True)

    # 库账号名。Hive 在 auth=NONE 下不发密码,此时身份就只由用户名表达,故 username 必填、password 可空
    username: Mapped[str] = mapped_column(String(128))
    # 落库前透明加密(EncryptedText),读取时自动解密;API 永不回传
    password: Mapped[Optional[str]] = mapped_column(EncryptedText, nullable=True)

    # 最近一次连通性测试**通过**的时间。None = 从未测通。
    # 这是上线卡点的唯一依据:改过用户名/密码就必须清空它(见 credential_service.upsert),
    # 否则「换了个错密码」的凭证会顶着旧的测通记录继续放行。
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # 最近一次测试失败的原因,给团队管理员自查用。引擎报错文本,不含密码
    last_verify_error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # 上次是谁改的。个人账号时代「谁配的」= 行的归属者,自证;团队账号是共享的,
    # 「上次谁动过」成了团队内的治理事实 —— 而团队管理员**读不到审计日志**(/audit 是 require_admin),
    # 所以这个事实必须落在业务表上,团队页才答得出来。软引用不做 FK。
    updated_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    # 刻意不声明 team / datasource 关系:全部调用点要么只用 id、要么手上已有对象。
    # 加了 lazy="joined" 会让每次凭证查询都多两个 LEFT JOIN,还会把 password 列拉出来解密。

    @hybrid_property
    def verified(self) -> bool:
        """凭证是否可用 = 通过过连接测试。

        hybrid 而非普通 property:同一条规则既要在 Python 里判(`cred.verified`),
        也要在 SQL 里筛(`where(TeamDataSourceCredential.verified)`)。两处共用这一条规则,
        以后给「就绪」加维度(如测通有效期)只改这里,不会出现「列表说就绪、运行时被拒」。
        """
        return self.last_verified_at is not None

    @verified.inplace.expression
    @classmethod
    def _verified_expr(cls):
        """SQL 侧的同一条规则。必须显式给出:`x is not None` 是 Python 的身份比较,
        无法被重载成 SQL,类级别求值只会得到常量 True。"""
        return cls.last_verified_at.is_not(None)

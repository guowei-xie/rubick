"""SQL 模板与版本 —— 产品 UI 里称「任务」。SQL 全程只存在平台元数据库,不进 Git。"""
from __future__ import annotations
from typing import Optional
from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, tbl
from app.models.mixins import TimestampMixin

# 生命周期(UI 措辞):草稿 → 已上线 →(下线后进「回收站」,可重新上线)
STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"
STATUS_ARCHIVED = "archived"


class SqlTemplate(Base, TimestampMixin):
    """任务主体:一条取数需求对应的可复用 SQL,含元信息与当前状态。"""

    __tablename__ = tbl("sql_templates")

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    # 业务域:已废弃,不再读写(留空列避免破坏性 DROP;如需清理可另起迁移单独删列)
    domain: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)

    datasource_id: Mapped[int] = mapped_column(ForeignKey(tbl("data_sources.id")))
    # hive / mysql;冗余存储,始终=数据源 engine,仅内部使用(不出现在 API)
    dialect: Mapped[str] = mapped_column(String(32))

    status: Mapped[str] = mapped_column(String(32), default=STATUS_DRAFT)
    author_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(tbl("users.id")))

    # 所属团队:任务的可见性边界,同时也是取数身份的来源(团队账号)。判定见 services/permission_service。
    # DB 侧可空**只为承载迁移前的存量行**:_ensure_column 无法给有数据的表加 NOT NULL 列,
    # 而补 NOT NULL 需 MySQL MODIFY / SQLite 重建表 —— 既测不到,又会让代码一旦回滚就整表
    # INSERT 全挂(错误 1364,即 query_jobs.modes 那次事故的翻版)。
    # 「恒有值」由三处保证:① migrate._assert_every_template_has_team 在部署期硬失败;
    # ② template_service.create_template 必填;③ 判定函数 fail-closed —— team_id 为空的
    # 「无主任务」除平台管理员外谁都看不到,不会静默泄漏。
    team_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey(tbl("teams.id")), index=True, nullable=True
    )

    # 该任务的查询超时(秒);None=按数据源引擎默认(Hive 用 HIVE_QUERY_TIMEOUT_SECONDS,其余用 QUERY_TIMEOUT_SECONDS)
    timeout_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # 「允许 API 调用」开关 —— 仅作**运行闸**:API 触发运行要求它为 True(fail-closed,
    # 默认关,见 query_service.enqueue);可见性与结果下载不受它影响,仍走权限模型。
    # server_default 让存量行直接落成 False —— 「上线前每个任务都得显式开」正是默认关的含义。
    allow_api: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="0"
    )

    # 指向当前"已上线"的版本;未上线(草稿/已下线)时为 None
    published_version_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(tbl("template_versions.id"), use_alter=True, name=tbl("fk_published_version")),
        nullable=True,
    )

    versions: Mapped[list["TemplateVersion"]] = relationship(
        back_populates="template",
        foreign_keys="TemplateVersion.template_id",
        cascade="all, delete-orphan",
    )

    # 便于列表展示创建人 / 数据源(引擎)/ 团队
    author = relationship("User", foreign_keys=[author_id], lazy="joined")
    datasource = relationship("DataSource", lazy="joined")
    team = relationship("Team", lazy="joined")

    @property
    def author_name(self) -> str | None:
        return self.author.name if self.author else None

    @property
    def team_name(self) -> str | None:
        return self.team.name if self.team else None

    @property
    def datasource_name(self) -> str | None:
        return self.datasource.name if self.datasource else None

    @property
    def engine(self) -> str | None:
        return self.datasource.engine if self.datasource else None


class TemplateVersion(Base, TimestampMixin):
    """任务的一个不可变版本:SQL 原文快照 + 参数定义 + 上线留痕。"""

    __tablename__ = tbl("template_versions")

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey(tbl("sql_templates.id")), index=True
    )
    version_no: Mapped[int] = mapped_column(Integer)  # 任务内自增
    sql_text: Mapped[str] = mapped_column(Text)
    # 参数定义,形状见 schemas.common.ParamDef:
    # [{name, kind(single|list), value_type(text|number), label, test_value,
    #   enum_sql, allow_bulk_input, enum_sql_duration_ms}](后三者仅 list 有意义)
    params: Mapped[list] = mapped_column(JSON, default=list)
    author_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(tbl("users.id")))

    # 上线留痕:谁上线的 + 备注(字段名沿用早期「验收」语义,当前无验收卡点)
    accepted_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    accepted_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    template: Mapped["SqlTemplate"] = relationship(
        back_populates="versions", foreign_keys=[template_id]
    )


class TemplateEnumValues(Base, TimestampMixin):
    """值列表变量的共享候选值 —— 可变、任务内共享、best-effort 缓存。

    刻意**不**放进 TemplateVersion.params:那是不可变版本快照,而候选值是任何使用者都能
    点按钮刷新的可变状态。一个任务的一个变量恒定一行,谁更新的全任务共用。
    注:这里会静态存下业务维度值原文(候选值),与 params 里只存 SQL 不同。

    过期判定不看时间,看「产出这批值的前提是否还成立」:enum_sql 指纹或数据源变了即视为过期
    (见 enum_cache_service._is_stale)。
    """

    __tablename__ = tbl("template_enum_values")
    __table_args__ = (
        UniqueConstraint("template_id", "variable", name="uq_template_enum_variable"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(ForeignKey(tbl("sql_templates.id")), index=True)
    variable: Mapped[str] = mapped_column(String(128))
    # 列名不能叫 values:VALUES 是 MySQL 保留字,而 migrate 的 SQLite 重建路径拼列名时不加引号
    enum_values: Mapped[list] = mapped_column(JSON, default=list)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 上次真实获取耗时
    # 产出这批值的数据源;任务换了数据源,同一段 SQL 的结果也会变,故一并参与过期判定
    datasource_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    enum_sql_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # 谁触发的这次更新(展示用)。刻意不做 FK:用户行被清理掉也不该拖累候选值
    updated_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

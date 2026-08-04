"""SQL 模板与版本 —— 产品 UI 里称「任务」。SQL 全程只存在平台元数据库,不进 Git。"""
from __future__ import annotations
from typing import Optional
from sqlalchemy import BigInteger, ForeignKey, Integer, JSON, String, Text
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

    # 该任务的查询超时(秒);None=按数据源引擎默认(Hive 用 HIVE_QUERY_TIMEOUT_SECONDS,其余用 QUERY_TIMEOUT_SECONDS)
    timeout_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

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

    # 便于列表展示创建人 / 数据源(引擎)
    author = relationship("User", foreign_keys=[author_id], lazy="joined")
    datasource = relationship("DataSource", lazy="joined")

    @property
    def author_name(self) -> str | None:
        return self.author.name if self.author else None

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

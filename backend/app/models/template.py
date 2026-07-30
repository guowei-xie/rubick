"""SQL 模板与版本。SQL 全程只存在平台元数据库,不进 Git。"""
from __future__ import annotations
from typing import Optional
from sqlalchemy import BigInteger, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, tbl
from app.models.mixins import TimestampMixin

# 模板生命周期
STATUS_DRAFT = "draft"
STATUS_TESTING = "testing"
STATUS_PENDING_ACCEPT = "pending_accept"
STATUS_PUBLISHED = "published"
STATUS_ARCHIVED = "archived"


class SqlTemplate(Base, TimestampMixin):
    """模板主体:一条取数需求对应的可复用 SQL,含元信息与当前状态。"""

    __tablename__ = tbl("sql_templates")

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # 业务域
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)

    datasource_id: Mapped[int] = mapped_column(ForeignKey(tbl("data_sources.id")))
    dialect: Mapped[str] = mapped_column(String(32))  # hive / mysql,与数据源一致

    status: Mapped[str] = mapped_column(String(32), default=STATUS_DRAFT)
    author_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(tbl("users.id")))

    # 指向当前"已发布"的版本;未发布时为 None
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
    """模板的一个不可变版本:SQL 原文快照 + 参数定义 + 验收记录。"""

    __tablename__ = tbl("template_versions")

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey(tbl("sql_templates.id")), index=True
    )
    version_no: Mapped[int] = mapped_column(Integer)  # 模板内自增
    sql_text: Mapped[str] = mapped_column(Text)
    # 参数定义:[{name,type,required,default,label,options}]
    params: Mapped[list] = mapped_column(JSON, default=list)
    author_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(tbl("users.id")))

    # 验收留痕
    accepted_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    accepted_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    template: Mapped["SqlTemplate"] = relationship(
        back_populates="versions", foreign_keys=[template_id]
    )

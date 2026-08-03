"""模板生命周期(v2):创建草稿 → 发布 → 更新回草稿 → 下线。"""
import pytest

from app.core.exceptions import RubicError
from app.models.datasource import DataSource
from app.models.template import STATUS_DRAFT, STATUS_PUBLISHED, STATUS_ARCHIVED
from app.models.user import ROLE_ADMIN, User
from app.schemas.common import ParamDef
from app.schemas.template import TemplateCreateIn, TemplateUpdateIn
from app.services import template_service


@pytest.fixture
def admin(db):
    u = db.get(User, 901)
    if u is None:
        # BigInteger 主键在 SQLite 下需显式赋值
        u = User(id=901, feishu_open_id="ou_flow_admin", name="流程管理员", role=ROLE_ADMIN)
        db.add(u)
        db.commit()
    return u


@pytest.fixture
def ds(db):
    from sqlalchemy import select

    d = db.scalar(select(DataSource).where(DataSource.name == "flow-mysql"))
    if d is None:
        d = DataSource(
            name="flow-mysql", engine="mysql", host="localhost", port=3306,
            database="demo", username="u", password="p", extra={},
        )
        db.add(d)
        db.commit()
    return d


def test_lifecycle_draft_publish_archive(db, admin, ds):
    tmpl = template_service.create_template(
        db, admin,
        TemplateCreateIn(
            name="流程测试", datasource_id=ds.id,
            sql_text="SELECT * FROM o WHERE d = :d AND c IN (:cs)",
            params=[
                ParamDef(name="d", kind="single", label="日期"),
                ParamDef(name="cs", kind="list", list_mode="in"),
            ],
        ),
    )
    assert tmpl.status == STATUS_DRAFT
    assert tmpl.dialect == "mysql"  # 方言自动跟随数据源

    # 发布:一步到位,写发布留痕
    template_service.publish(db, tmpl, admin, note="上线")
    assert tmpl.status == STATUS_PUBLISHED
    ver = db.get(type(tmpl).versions.prop.mapper.class_, tmpl.published_version_id)
    assert ver.accepted_by == admin.id
    assert ver.params[0] == {
        "name": "d", "kind": "single", "label": "日期",
        "description": None, "enum_sql": None, "list_mode": None,
    }

    # 更新 = 新草稿版本,状态回 draft
    v2 = template_service.add_version(
        db, admin, tmpl, TemplateUpdateIn(sql_text="SELECT * FROM o WHERE d = :d", params=[ParamDef(name="d")])
    )
    assert tmpl.status == STATUS_DRAFT and v2.version_no == 2

    # 下线清空已发布版本
    template_service.publish(db, tmpl, admin, note=None)
    template_service.archive(db, tmpl)
    assert tmpl.status == STATUS_ARCHIVED and tmpl.published_version_id is None


def test_publish_without_version_raises(db, admin, ds):
    from app.models.template import SqlTemplate

    tmpl = SqlTemplate(name="空模板", datasource_id=ds.id, dialect="mysql", author_id=admin.id)
    db.add(tmpl)
    db.commit()
    with pytest.raises(RubicError):
        template_service.publish(db, tmpl, admin, None)

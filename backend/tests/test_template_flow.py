"""模板生命周期:创建待上线 → 上线 → 编辑保持上线 → 下线。"""
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


def test_lifecycle_pending_publish_archive(db, admin, ds):
    tmpl = template_service.create_template(
        db, admin,
        TemplateCreateIn(
            name="流程测试", datasource_id=ds.id,
            sql_text="SELECT * FROM o WHERE d = :d AND c IN (:cs)",
            params=[
                ParamDef(name="d", kind="single", label="日期"),
                ParamDef(name="cs", kind="list"),
            ],
        ),
    )
    assert tmpl.status == STATUS_DRAFT  # 新建 = 待上线
    assert tmpl.dialect == "mysql"  # 方言自动跟随数据源

    # 上线:一步到位,写发布留痕
    template_service.publish(db, tmpl, admin, note="上线")
    assert tmpl.status == STATUS_PUBLISHED
    ver = db.get(type(tmpl).versions.prop.mapper.class_, tmpl.published_version_id)
    assert ver.accepted_by == admin.id
    assert ver.params[0] == {
        "name": "d", "kind": "single", "value_type": "text", "label": "日期",
        "test_value": None, "enum_sql": None, "allow_bulk_input": False,
        "enum_sql_duration_ms": None,
    }

    # 编辑已上线任务:新版本自动接替上线,状态保持 published
    v2 = template_service.add_version(
        db, admin, tmpl, TemplateUpdateIn(sql_text="SELECT * FROM o WHERE d = :d", params=[ParamDef(name="d")])
    )
    assert tmpl.status == STATUS_PUBLISHED and v2.version_no == 2
    assert tmpl.published_version_id == v2.id  # 上线指向新版本
    assert v2.accepted_by == admin.id

    # 下线清空已发布版本
    template_service.archive(db, tmpl)
    assert tmpl.status == STATUS_ARCHIVED and tmpl.published_version_id is None


def test_edit_draft_stays_draft(db, admin, ds):
    tmpl = template_service.create_template(
        db, admin,
        TemplateCreateIn(
            name="草稿编辑", datasource_id=ds.id,
            sql_text="SELECT * FROM o WHERE d = :d", params=[ParamDef(name="d")],
        ),
    )
    assert tmpl.status == STATUS_DRAFT
    template_service.add_version(
        db, admin, tmpl, TemplateUpdateIn(sql_text="SELECT * FROM o WHERE d = :d AND x = :x",
                                          params=[ParamDef(name="d"), ParamDef(name="x")])
    )
    # 待上线任务编辑后仍待上线,不会被意外上线
    assert tmpl.status == STATUS_DRAFT and tmpl.published_version_id is None


def test_publish_without_version_raises(db, admin, ds):
    from app.models.template import SqlTemplate

    tmpl = SqlTemplate(name="空模板", datasource_id=ds.id, dialect="mysql", author_id=admin.id)
    db.add(tmpl)
    db.commit()
    with pytest.raises(RubicError):
        template_service.publish(db, tmpl, admin, None)

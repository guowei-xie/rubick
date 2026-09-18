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


@pytest.fixture
def team(db, ds, team_factory, team_credential):
    """任务必属团队,且上线要求团队账号已测通 —— 故这两件事一起备好。"""
    t = team_factory("flow-team", [])
    team_credential(t, ds, username="flow_team_acct")
    return t


def test_lifecycle_pending_publish_archive(db, admin, ds, team):
    tmpl = template_service.create_template(
        db, admin,
        TemplateCreateIn(
            name="流程测试", team_id=team.id, datasource_id=ds.id,
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


def test_edit_draft_stays_draft(db, admin, ds, team):
    tmpl = template_service.create_template(
        db, admin,
        TemplateCreateIn(
            name="草稿编辑", team_id=team.id, datasource_id=ds.id,
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


def _draft(db, admin, ds, team, name: str):
    return template_service.create_template(
        db, admin,
        TemplateCreateIn(
            name=name, team_id=team.id, datasource_id=ds.id,
            sql_text="SELECT * FROM o WHERE d = :d", params=[ParamDef(name="d")],
        ),
    )


def test_draft_can_be_archived_and_unarchived(db, admin, ds, team):
    """草稿也能进回收站,并且能原样退回草稿 —— 任务不可删,废弃的草稿否则无处可去。"""
    tmpl = _draft(db, admin, ds, team, "草稿进回收站")
    template_service.archive(db, tmpl)
    assert tmpl.status == STATUS_ARCHIVED and tmpl.published_version_id is None

    template_service.unarchive(db, tmpl)
    assert tmpl.status == STATUS_DRAFT
    # 退回草稿不上线:业务侧仍不可运行,也没有已发布版本
    assert tmpl.published_version_id is None


def test_unarchive_rejects_non_archived(db, admin, ds, team):
    """草稿/已上线任务不能「退回草稿」:那是回收站专用的出口,放行会让已上线任务被悄悄下线。"""
    tmpl = _draft(db, admin, ds, team, "非回收站退回")
    with pytest.raises(RubicError):
        template_service.unarchive(db, tmpl)
    assert tmpl.status == STATUS_DRAFT

    template_service.publish(db, tmpl, admin, note="上线")
    with pytest.raises(RubicError):
        template_service.unarchive(db, tmpl)
    assert tmpl.status == STATUS_PUBLISHED


def test_archived_draft_can_still_be_published(db, admin, ds, team):
    """回收站的另一个出口对草稿同样成立:创建时就有 version_no=1,不会撞上「没有可上线的版本」。"""
    tmpl = _draft(db, admin, ds, team, "草稿回收站直接上线")
    template_service.archive(db, tmpl)
    template_service.publish(db, tmpl, admin, note="回收站重新上线")
    assert tmpl.status == STATUS_PUBLISHED and tmpl.published_version_id is not None


def test_publish_without_version_raises(db, admin, ds, team):
    from app.models.template import SqlTemplate

    tmpl = SqlTemplate(
        name="空模板", team_id=team.id, datasource_id=ds.id,
        dialect="mysql", author_id=admin.id,
    )
    db.add(tmpl)
    db.commit()
    with pytest.raises(RubicError):
        template_service.publish(db, tmpl, admin, None)

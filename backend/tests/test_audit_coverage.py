"""审计覆盖回归网:新增写接口必须「要么审计、要么写明豁免理由」。

分两层:
- 清单门禁(_write_routes vs AUDITED/EXEMPT):纯静态,新加一个 POST/PUT/PATCH/DELETE
  而不动本文件,测试立刻失败并提示该怎么登记。这是防「悄悄漏审计」的核心护栏。
- 行为断言:直接调用路由函数(与仓库既有测试同风格,不引入 TestClient),
  验证动作码、资源、变更前后值确实落库,以及密码永不进审计。
"""
import json

import pytest
from sqlalchemy import func, select

from app.api.routes.admin import RoleIn, set_role
from app.api.routes.datasources import (
    create_datasource,
    delete_datasource,
    update_datasource,
)
from app.api.routes.permissions import grant as grant_route
from app.api.routes.permissions import revoke as revoke_route
from app.api.routes.templates import archive, create_template, publish, update_template
from app.main import app
from app.models import audit as A
from app.models.audit import AuditLog
from app.models.datasource import DataSource
from app.models.template import STATUS_ARCHIVED, STATUS_PUBLISHED, SqlTemplate
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER, User
from app.schemas.datasource import DataSourceIn, DataSourceUpdateIn
from app.schemas.permission import GrantIn
from app.schemas.template import PublishIn, TemplateCreateIn, TemplateUpdateIn

# ---------------------------------------------------------------- 清单门禁

# 写接口 → 该接口发出的审计动作码
AUDITED: dict[str, frozenset[str]] = {
    "POST /api/auth/mock-login": frozenset({A.ACTION_LOGIN}),
    "POST /api/auth/feishu/callback": frozenset({A.ACTION_LOGIN}),
    "POST /api/run": frozenset({A.ACTION_SUBMIT_QUERY}),
    "POST /api/templates": frozenset({A.ACTION_TASK_CREATE}),
    "PUT /api/templates/{template_id}": frozenset({A.ACTION_TASK_UPDATE}),
    # 上线 / 从回收站恢复共用一个端点,按原状态分派两个动作码
    "POST /api/templates/{template_id}/publish": frozenset(
        {A.ACTION_TASK_PUBLISH, A.ACTION_TASK_RESTORE}
    ),
    "POST /api/templates/{template_id}/archive": frozenset({A.ACTION_TASK_ARCHIVE}),
    # 业务用户手动更新共享枚举候选值:会改动该任务所有人看到的候选,值本身不进 detail
    "POST /api/tasks/{template_id}/enum-values/refresh": frozenset(
        {A.ACTION_TASK_ENUM_REFRESH}
    ),
    "POST /api/permissions": frozenset({A.ACTION_PERMISSION_GRANT}),
    "DELETE /api/permissions/{perm_id}": frozenset({A.ACTION_PERMISSION_REVOKE}),
    # 指定任务的编辑权:团队内点对点授权,与业务侧 view/run/download 分开记
    "POST /api/tasks/{template_id}/editors": frozenset({A.ACTION_TASK_EDIT_GRANT}),
    "DELETE /api/tasks/{template_id}/editors/{user_id}": frozenset(
        {A.ACTION_TASK_EDIT_REVOKE}
    ),
    # 转移任务所属团队:同时改变可见范围与取数身份,治理上是大事
    "PUT /api/tasks/{template_id}/team": frozenset({A.ACTION_TASK_TEAM_TRANSFER}),
    # 任务订阅:订阅/退订都留痕(自动清退的 task_auto_unsubscribe 由 worker 侧写,
    # 不对应任何写接口,见 subscription_service.settle_on_success)
    "PUT /api/tasks/{template_id}/subscription": frozenset({A.ACTION_TASK_SUBSCRIBE}),
    "DELETE /api/tasks/{template_id}/subscription": frozenset({A.ACTION_TASK_UNSUBSCRIBE}),
    "POST /api/admin/users/{user_id}/role": frozenset({A.ACTION_USER_ROLE_CHANGE}),
    "POST /api/datasources": frozenset({A.ACTION_DATASOURCE_CREATE}),
    "PUT /api/datasources/{ds_id}": frozenset({A.ACTION_DATASOURCE_UPDATE}),
    "DELETE /api/datasources/{ds_id}": frozenset({A.ACTION_DATASOURCE_DELETE}),
    # 团队治理:成员变更等于一次数据授权(团队账号是共享的),必须留痕
    "POST /api/teams": frozenset({A.ACTION_TEAM_CREATE}),
    "PUT /api/teams/{team_id}": frozenset({A.ACTION_TEAM_UPDATE}),
    "DELETE /api/teams/{team_id}": frozenset({A.ACTION_TEAM_DELETE}),
    "POST /api/teams/{team_id}/members": frozenset({A.ACTION_TEAM_MEMBER_ADD}),
    "DELETE /api/teams/{team_id}/members/{user_id}": frozenset(
        {A.ACTION_TEAM_MEMBER_REMOVE}
    ),
    "POST /api/teams/{team_id}/members/{user_id}/admin": frozenset(
        {A.ACTION_TEAM_ADMIN_GRANT}
    ),
    "DELETE /api/teams/{team_id}/members/{user_id}/admin": frozenset(
        {A.ACTION_TEAM_ADMIN_REVOKE}
    ),
    # 团队取数账号:凭证决定该团队能取到哪些数据,增改删与连通性测试全部留痕。
    # 测试也审计(不豁免):它是上线卡点的凭据,「一直测不通」本身就是要能查的事实。
    "PUT /api/credentials/teams/{team_id}/{ds_id}": frozenset({A.ACTION_CREDENTIAL_UPSERT}),
    "POST /api/credentials/teams/{team_id}/{ds_id}/test": frozenset(
        {A.ACTION_CREDENTIAL_VERIFY}
    ),
    "DELETE /api/credentials/teams/{team_id}/{ds_id}": frozenset(
        {A.ACTION_CREDENTIAL_DELETE}
    ),
}

# 免审计的写接口 → 必须写明豁免理由(空理由视为未表态)
EXEMPT: dict[str, str] = {
    "POST /api/notifications/read-all": "仅本人站内消息已读状态,无数据访问、无权限变更,且调用量最高,记了会淹没治理事件",
    "POST /api/notifications/{note_id}/read": "同 read-all",
    "POST /api/templates/preview-sql": "纯字符串渲染,不连数据源、不落库,无数据访问",
    "POST /api/templates/enum-sql": "取参数候选值(只读 SELECT,上限 1000 行),编辑器高频触发,按产品决策不纳入",
    "POST /api/templates/test-run": "作者试跑,按产品决策不纳入;关联任务时已有 source=test 的运行记录可查",
    "POST /api/datasources/{ds_id}/test": "仅测连通性、不改状态,按产品决策不纳入",
}

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _write_routes() -> set[str]:
    out = set()
    for r in app.routes:
        path = getattr(r, "path", "")
        if not path.startswith("/api"):
            continue
        for m in (getattr(r, "methods", None) or set()) & _WRITE_METHODS:
            out.add(f"{m} {path}")
    return out


def test_every_write_route_is_audited_or_explicitly_exempt():
    unclassified = _write_routes() - set(AUDITED) - set(EXEMPT)
    assert not unclassified, (
        "新增写接口必须补审计埋点并登记到 AUDITED,或确认无需审计后登记到 EXEMPT 并写明理由:"
        f"{sorted(unclassified)}"
    )


def test_no_stale_registry_entries():
    """接口删了或改了路径,登记表要跟着清,否则 AUDITED 会变成一份骗人的清单。"""
    stale = (set(AUDITED) | set(EXEMPT)) - _write_routes()
    assert not stale, f"登记表里有已不存在的路由,请清理:{sorted(stale)}"


def test_audited_actions_are_registered_with_labels():
    missing = sorted({c for codes in AUDITED.values() for c in codes} - set(A.ACTION_META))
    assert not missing, f"动作码未在 models.audit.ACTION_META 登记(管理端筛选下拉会漏):{missing}"


def test_exemptions_have_reasons():
    blank = sorted(k for k, v in EXEMPT.items() if not v.strip())
    assert not blank, f"豁免必须写明理由:{blank}"


def test_action_meta_labels_are_chinese_and_grouped():
    for code, (label, group) in A.ACTION_META.items():
        assert any("一" <= ch <= "鿿" for ch in label), f"{code} 缺中文标签"
        assert group in A.GROUPS, f"{code} 的分组 {group} 不在 GROUPS 内"


# ---------------------------------------------------------------- 行为断言


def _max_audit_id(db) -> int:
    return db.scalar(select(func.max(AuditLog.id))) or 0


def _new_rows(db, since_id: int) -> list[AuditLog]:
    db.expire_all()
    return list(db.scalars(select(AuditLog).where(AuditLog.id > since_id).order_by(AuditLog.id)))


def _one_new_row(db, since_id: int) -> AuditLog:
    rows = _new_rows(db, since_id)
    assert len(rows) == 1, f"期望恰好 1 条审计记录,实际 {len(rows)} 条:{[r.action for r in rows]}"
    return rows[0]


@pytest.fixture
def admin(db):
    u = db.get(User, 8001)
    if u is None:
        # BigInteger 主键在 SQLite 下需显式赋值(同 test_template_flow.py)
        u = User(id=8001, feishu_open_id="ou_audit_admin", name="审计管理员", role=ROLE_ADMIN)
        db.add(u)
        db.commit()
    return u


@pytest.fixture
def ds(db):
    d = db.scalar(select(DataSource).where(DataSource.name == "audit-mysql"))
    if d is None:
        d = DataSource(
            name="audit-mysql", engine="mysql", host="localhost", port=3306,
            database="demo", username="u", password="p", extra={},
        )
        db.add(d)
        db.commit()
    return d


@pytest.fixture
def team(db, ds, team_factory, team_credential):
    """任务必属团队;上线卡点要求团队账号已测通,故一并备好。"""
    t = team_factory("audit-team", [])
    team_credential(t, ds, username="audit_team_acct")
    return t


def _make_task(db, admin, ds, team, name="审计用任务") -> SqlTemplate:
    return create_template(
        TemplateCreateIn(
            name=name, description="d", tags=[], team_id=team.id, datasource_id=ds.id,
            sql_text="SELECT 1", params=[],
        ),
        db, admin, ip=None,
    )


def test_task_create_is_audited(db, admin, ds, team):
    since = _max_audit_id(db)
    tmpl = _make_task(db, admin, ds, team, name="新建审计任务")
    row = _one_new_row(db, since)
    assert row.action == A.ACTION_TASK_CREATE
    assert row.resource_type == A.RESOURCE_TEMPLATE and row.resource_id == str(tmpl.id)
    assert row.resource_name == "新建审计任务"
    assert row.user_id == admin.id
    assert row.detail["version_no"] == 1
    assert row.detail["sql_text"] == "SELECT 1"


def test_task_update_records_before_and_after(db, admin, ds, team):
    tmpl = _make_task(db, admin, ds, team, name="改名前")
    since = _max_audit_id(db)
    update_template(
        tmpl.id,
        TemplateUpdateIn(name="改名后", sql_text="SELECT 2", params=[], datasource_id=ds.id),
        db, admin, ip=None,
    )
    row = _one_new_row(db, since)
    assert row.action == A.ACTION_TASK_UPDATE
    assert row.detail["changes"]["name"] == {"from": "改名前", "to": "改名后"}
    assert row.detail["sql_changed"] is True
    assert row.detail["version_no"] == 2


def test_publish_then_archive_then_restore_are_distinct_actions(db, admin, ds, team):
    tmpl = _make_task(db, admin, ds, team, name="生命周期任务")

    since = _max_audit_id(db)
    publish(tmpl.id, PublishIn(note="首次上线"), db, admin, ip=None)
    row = _one_new_row(db, since)
    assert row.action == A.ACTION_TASK_PUBLISH
    assert row.detail["from_status"] != STATUS_PUBLISHED
    assert row.detail["to_status"] == STATUS_PUBLISHED

    since = _max_audit_id(db)
    archive(tmpl.id, db, admin, ip=None)
    row = _one_new_row(db, since)
    assert row.action == A.ACTION_TASK_ARCHIVE
    assert row.detail["from_status"] == STATUS_PUBLISHED

    # 回收站「重新上线」走同一个 publish 端点,但必须记成 task_restore
    since = _max_audit_id(db)
    publish(tmpl.id, PublishIn(note="回收站恢复"), db, admin, ip=None)
    row = _one_new_row(db, since)
    assert row.action == A.ACTION_TASK_RESTORE
    assert row.detail["from_status"] == STATUS_ARCHIVED


def test_permission_grant_and_revoke_are_audited(db, admin, ds, team):
    tmpl = _make_task(db, admin, ds, team, name="授权用任务")
    grantee = db.get(User, 8050)
    if grantee is None:
        grantee = User(id=8050, feishu_open_id="ou_audit_grantee", name="被授权人", role=ROLE_USER)
        db.add(grantee)
        db.commit()

    since = _max_audit_id(db)
    perms = grant_route(
        GrantIn(subject_id=str(grantee.id), resource_id=str(tmpl.id), actions=["view", "run"]),
        db, admin, ip=None,
    )
    row = _one_new_row(db, since)
    assert row.action == A.ACTION_PERMISSION_GRANT
    # 资源记的是被授权的那个任务,便于「任务 X 上发生过什么」一并筛出
    assert row.resource_type == A.RESOURCE_TEMPLATE and row.resource_id == str(tmpl.id)
    assert row.detail["subject_name"] == "被授权人"
    assert row.detail["actions"] == ["view", "run"]
    assert sorted(row.detail["actions_created"]) == ["run", "view"]

    # 撤销:授权行被删掉后,日志里仍要看得出「撤了谁的什么权限」
    since = _max_audit_id(db)
    revoke_route(perms[0].id, db, admin, ip=None)
    row = _one_new_row(db, since)
    assert row.action == A.ACTION_PERMISSION_REVOKE
    assert row.detail["subject_name"] == "被授权人"
    assert row.detail["action"] in ("view", "run")
    assert row.detail["subject_id"] == str(grantee.id)


def test_revoke_missing_permission_writes_no_log(db, admin):
    """授权行本就不存在时什么都没发生,不该留下误导性的「撤销」记录。"""
    since = _max_audit_id(db)
    revoke_route(99999999, db, admin, ip=None)
    assert _new_rows(db, since) == []


def test_role_change_records_from_and_to(db, admin):
    target = db.get(User, 8060)
    if target is None:
        target = User(id=8060, feishu_open_id="ou_audit_role", name="待提权", role=ROLE_USER)
        db.add(target)
        db.commit()

    since = _max_audit_id(db)
    set_role(target.id, RoleIn(role=ROLE_DEVELOPER), db, admin, ip=None)
    row = _one_new_row(db, since)
    assert row.action == A.ACTION_USER_ROLE_CHANGE
    assert row.resource_type == A.RESOURCE_USER and row.resource_id == str(target.id)
    assert row.detail["role"] == {"from": ROLE_USER, "to": ROLE_DEVELOPER}
    assert row.detail["target_user_name"] == "待提权"


def test_datasource_lifecycle_is_audited_without_leaking_password(db, admin):
    secret = "sup3r-s3cret-pw"
    since = _max_audit_id(db)
    created = create_datasource(
        DataSourceIn(
            name="审计数据源", engine="mysql", host="db-old", port=3306,
            database="demo", username="u", password=secret, extra={},
        ),
        db, admin, ip=None,
    )
    row = _one_new_row(db, since)
    assert row.action == A.ACTION_DATASOURCE_CREATE
    assert row.detail["host"] == "db-old"
    assert row.detail["has_password"] is True
    assert "password" not in row.detail
    assert secret not in json.dumps(row.detail, ensure_ascii=False)

    # 改地址 + 改密码:地址要有前后值,密码只留一个布尔
    since = _max_audit_id(db)
    new_secret = "another-s3cret"
    update_datasource(
        created.id, DataSourceUpdateIn(host="db-new", password=new_secret), db, admin, ip=None
    )
    row = _one_new_row(db, since)
    assert row.action == A.ACTION_DATASOURCE_UPDATE
    assert row.detail["changes"]["host"] == {"from": "db-old", "to": "db-new"}
    assert row.detail["password_changed"] is True
    assert "password" not in row.detail["changes"]
    assert new_secret not in json.dumps(row.detail, ensure_ascii=False)

    # 删除:资源名要能在记录被删后仍然读出来
    since = _max_audit_id(db)
    delete_datasource(created.id, db, admin, ip=None)
    row = _one_new_row(db, since)
    assert row.action == A.ACTION_DATASOURCE_DELETE
    assert row.resource_name == "审计数据源"
    assert row.detail["host"] == "db-new"


def test_redaction_backstop_applies_even_if_a_caller_passes_a_secret(db, admin):
    """兜底:log() 内部无条件脱敏,任何调用点(包括以后新加的)都无法把凭证写进审计表。

    这是 DataSource.password 那类字段的最后一道防线 —— 它是 EncryptedText,读属性即明文,
    而 detail 是未加密 JSON 且可导出 CSV。嵌套 dict 与 list 里的凭证同样要被盖掉。
    """
    from app.services import audit_service

    entry = audit_service.log(
        db, user=admin, action=A.ACTION_DATASOURCE_UPDATE,
        detail={
            "password": "leak-me",
            "nested": {"token": "leak-me-too"},
            "items": [{"feishu_token": "leak-in-list"}],
            "changes": {"password": {"from": "old-pw", "to": "new-pw"}},
            "host": "ok",
        },
    )
    assert entry.detail["password"] == "***"
    assert entry.detail["nested"]["token"] == "***"
    assert entry.detail["items"][0]["feishu_token"] == "***"
    assert entry.detail["changes"]["password"] == "***"
    assert entry.detail["host"] == "ok"
    # 整体再扫一遍,确保没有任何一处漏网
    blob = json.dumps(entry.detail, ensure_ascii=False)
    for secret in ("leak-me", "leak-me-too", "leak-in-list", "old-pw", "new-pw"):
        assert secret not in blob, secret

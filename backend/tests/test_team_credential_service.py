"""团队取数账号(服务层):身份解析、就绪判定、密码加密、脱敏,以及「不许有开关」的护栏。

粒度是**团队 × 数据源** —— 文件名把这件事写进目录。
与仓库既有测试同风格:直接调服务/路由函数,不起 TestClient;所有取数经 spy_connector。
"""
import inspect
import json

import pytest
from sqlalchemy import func, select, text

from app.connectors import get_connector
from app.core.config import settings
from app.core.exceptions import (
    CredentialRequiredError,
    NotFoundError,
    PermissionDeniedError,
    RubicError,
)
from app.models.credential import TeamDataSourceCredential
from app.models.datasource import DataSource
from app.models.template import STATUS_PUBLISHED, SqlTemplate
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER
from app.services import credential_service

pytestmark = pytest.mark.usefixtures("clean_credentials")  # 每个用例从「无凭证」起步

SECRET = "s3cret-team-pw"

# ID 段 8630–8635
ADMIN, T_ADMIN, MEMBER, OUTSIDER = 8630, 8631, 8632, 8633





@pytest.fixture
def ds(db, datasource_factory):
    return datasource_factory("tcred-mysql")


@pytest.fixture
def admin(db, user_factory):
    return user_factory(ADMIN, ROLE_ADMIN, "平台管理员", prefix="tcred")


@pytest.fixture
def t_admin(db, user_factory):
    return user_factory(T_ADMIN, ROLE_DEVELOPER, "团队管理员", prefix="tcred")


@pytest.fixture
def member(db, user_factory):
    return user_factory(MEMBER, ROLE_DEVELOPER, "团队成员", prefix="tcred")


@pytest.fixture
def outsider(db, user_factory):
    return user_factory(OUTSIDER, ROLE_DEVELOPER, "外队开发者", prefix="tcred")




@pytest.fixture
def team(db, t_admin, member, team_factory):
    return team_factory("tcred-team", [(t_admin, True), (member, False)])


@pytest.fixture
def other_team(db, outsider, team_factory):
    return team_factory("tcred-other", [(outsider, True)])


def _tmpl(db, ds, team, *, name="tcred-任务") -> SqlTemplate:
    t = db.scalar(select(SqlTemplate).where(SqlTemplate.name == name))
    if t is None:
        t = SqlTemplate(
            name=name, datasource_id=ds.id, dialect="mysql", status=STATUS_PUBLISHED,
            author_id=MEMBER, team_id=team.id if team else None,
        )
        db.add(t)
        db.commit()
        db.refresh(t)
    else:
        t.team_id = team.id if team else None
        db.commit()
    return t


# ---------------------------------------------------------------- upsert / 就绪


def test_upsert_is_one_row_per_team_and_datasource(db, team, ds):
    credential_service.upsert(
        db, team_id=team.id, datasource_id=ds.id,
        username="acct1", password=SECRET, updated_by=T_ADMIN,
    )
    credential_service.upsert(
        db, team_id=team.id, datasource_id=ds.id,
        username="acct2", password=None, updated_by=T_ADMIN,
    )
    rows = db.scalars(
        select(TeamDataSourceCredential).where(TeamDataSourceCredential.team_id == team.id)
    ).all()
    assert len(rows) == 1
    assert rows[0].username == "acct2"
    assert rows[0].password == SECRET  # 空密码 = 保留原密码


def test_blank_username_rejected(db, team, ds):
    with pytest.raises(RubicError, match="用户名"):
        credential_service.upsert(
            db, team_id=team.id, datasource_id=ds.id,
            username="  ", password=SECRET, updated_by=T_ADMIN,
        )


def test_changing_credential_clears_verified(db, team, ds, team_credential):
    cred = team_credential(team, ds, username="acct", password=SECRET)
    assert cred.verified is True

    # 同值重复提交:不该打掉已测通
    credential_service.upsert(
        db, team_id=team.id, datasource_id=ds.id,
        username="acct", password=SECRET, updated_by=T_ADMIN,
    )
    db.refresh(cred)
    assert cred.verified is True, "同值提交不该清空测通状态"

    # 改用户名 ⇒ 必须重新测通,否则换了错凭证也能顶着旧记录过上线卡点
    credential_service.upsert(
        db, team_id=team.id, datasource_id=ds.id,
        username="acct-new", password=None, updated_by=T_ADMIN,
    )
    db.refresh(cred)
    assert cred.verified is False and cred.last_verified_at is None


def test_password_is_encrypted_at_rest(db, team, ds):
    credential_service.upsert(
        db, team_id=team.id, datasource_id=ds.id,
        username="acct", password=SECRET, updated_by=T_ADMIN,
    )
    # 绕过 TypeDecorator 直接读裸值
    raw = db.execute(
        text(
            "SELECT password FROM rubick_team_datasource_credentials "
            "WHERE team_id = :t AND datasource_id = :d"
        ),
        {"t": team.id, "d": ds.id},
    ).scalar()
    assert raw.startswith("enc::v1::")
    assert SECRET not in raw


def test_verify_uses_the_team_account_and_records_state(db, team, ds, spy_connector):
    seen = spy_connector()
    credential_service.upsert(
        db, team_id=team.id, datasource_id=ds.id,
        username="team_acct", password=SECRET, updated_by=T_ADMIN,
    )
    cred = credential_service.get(db, team.id, ds.id)
    credential_service.verify(db, cred)
    assert [c.username for c in seen] == ["team_acct"]  # 不是公共账号
    assert cred.verified is True and cred.last_verify_error is None


def test_verify_failure_clears_verified_and_records_reason(db, team, ds, spy_connector):
    spy_connector()
    credential_service.upsert(
        db, team_id=team.id, datasource_id=ds.id,
        username="team_acct", password=SECRET, updated_by=T_ADMIN,
    )
    cred = credential_service.get(db, team.id, ds.id)
    credential_service.verify(db, cred)
    assert cred.verified is True

    spy_connector(fail="Access denied for user 'team_acct'")
    with pytest.raises(RubicError):
        credential_service.verify(db, cred)
    # 一套连不上的凭证不该继续以「已测通」的身份通过上线卡点
    assert cred.verified is False
    assert "Access denied" in cred.last_verify_error


# ---------------------------------------------------------------- 身份解析


def test_for_template_resolves_the_tasks_team_account(db, team, ds, team_credential):
    team_credential(team, ds, username="team_acct", password=SECRET)
    tmpl = _tmpl(db, ds, team)
    cred = credential_service.for_template(db, tmpl)
    assert cred.username == "team_acct"
    assert cred.owner_team_id == team.id and cred.is_team_account is True


def test_for_template_errors_name_the_team_and_datasource(db, team, ds):
    tmpl = _tmpl(db, ds, team)
    with pytest.raises(CredentialRequiredError) as e:
        credential_service.for_template(db, tmpl)
    msg = str(e.value)
    assert team.name in msg and ds.name in msg and "尚未配置" in msg
    assert "团队管理员" in msg, "报错要指路:业务用户得知道找谁"


def test_for_template_rejects_unverified(db, team, ds):
    credential_service.upsert(
        db, team_id=team.id, datasource_id=ds.id,
        username="acct", password=SECRET, updated_by=T_ADMIN,
    )
    tmpl = _tmpl(db, ds, team)
    with pytest.raises(CredentialRequiredError, match="尚未通过连接测试"):
        credential_service.for_template(db, tmpl)


def test_orphan_task_never_falls_back_to_public_account(db, ds, team_credential, team):
    """任务没有所属团队时明确失败 —— **绝不回退公共账号**。

    那种回退是一条静默的越权路径:公共账号能看到该库的全部数据,而且不会有任何测试变红。
    """
    team_credential(team, ds)
    orphan = _tmpl(db, ds, None, name="tcred-无主任务")
    with pytest.raises(CredentialRequiredError, match="无法确定"):
        credential_service.for_template(db, orphan)


def test_for_team_requires_membership(db, team, ds, member, outsider, admin, team_credential):
    """试跑的成员校验长在服务层:team_id 来自客户端,少一处校验就等于借别队账号跑任意 SQL。"""
    team_credential(team, ds, username="team_acct")

    cred = credential_service.for_team(
        db, team_id=team.id, datasource_id=ds.id, actor=member
    )
    assert cred.username == "team_acct"

    with pytest.raises(PermissionDeniedError, match="不是团队"):
        credential_service.for_team(
            db, team_id=team.id, datasource_id=ds.id, actor=outsider
        )
    # 平台管理员不受团队约束
    assert credential_service.for_team(
        db, team_id=team.id, datasource_id=ds.id, actor=admin
    ).username == "team_acct"


def test_for_team_without_actor_is_refused(db, team, ds, team_credential):
    team_credential(team, ds)
    with pytest.raises(PermissionDeniedError, match="无法确定操作人"):
        credential_service.for_team(db, team_id=team.id, datasource_id=ds.id, actor=None)


def test_require_ready_wraps_message_for_publish(db, team, ds):
    tmpl = _tmpl(db, ds, team)
    with pytest.raises(CredentialRequiredError, match="任务上线被拦下"):
        credential_service.require_ready(db, tmpl)


# ---------------------------------------------------------------- 视图


def test_list_for_team_lists_every_datasource_and_hides_username(db, team, ds, team_credential):
    team_credential(team, ds, username="team_acct")
    revealed = credential_service.list_for_team(db, team.id, reveal_username=True)
    hidden = credential_service.list_for_team(db, team.id, reveal_username=False)

    # 未配置的数据源也要出现 —— 「还差哪个源」正是配置页要回答的问题
    assert len(revealed) == db.scalar(select(func.count()).select_from(DataSource))
    mine = next(r for r in revealed if r["datasource_id"] == ds.id)
    assert mine["username"] == "team_acct" and mine["verified"] is True
    assert all("password" not in r for r in revealed)

    mine_hidden = next(r for r in hidden if r["datasource_id"] == ds.id)
    assert mine_hidden["username"] is None, "普通成员不该看到库用户名"
    assert mine_hidden["configured"] is True and mine_hidden["verified"] is True


def test_my_teams_status_never_reveals_username(db, team, ds, member, team_credential):
    team_credential(team, ds, username="team_acct")
    rows = credential_service.my_teams_status(db, member)
    assert rows and all(r["username"] is None for r in rows)
    blob = json.dumps(rows, ensure_ascii=False, default=str)
    assert "team_acct" not in blob


def test_ready_template_ids_pairs_team_and_datasource(db, ds, team, other_team, team_credential):
    """同一个数据源:甲队已测通、乙队未配置 ⇒ 两个任务分别在集合内外。"""
    team_credential(team, ds, username="ready_acct")
    ready = _tmpl(db, ds, team, name="tcred-就绪任务")
    not_ready = _tmpl(db, ds, other_team, name="tcred-未就绪任务")
    ids = credential_service.ready_template_ids(db, [ready, not_ready])
    assert ready.id in ids and not_ready.id not in ids


def test_orphan_task_is_never_ready(db, ds, team, team_credential):
    team_credential(team, ds)
    orphan = _tmpl(db, ds, None, name="tcred-无主任务2")
    assert credential_service.ready_template_ids(db, [orphan]) == set()


def test_delete_for_team_and_for_datasource(db, ds, team, other_team, team_credential):
    team_credential(team, ds)
    team_credential(other_team, ds)
    assert credential_service.delete_for_team(db, team.id) == 1
    db.commit()
    assert credential_service.delete_for_datasource(db, ds.id) == 1
    db.commit()


def test_overview_has_no_enforced_flag(db, ds, team, team_credential):
    """开关字段彻底消失:enforced 曾是「切开关前的体检表」的一部分,现在没有开关了。"""
    team_credential(team, ds, username="ov_acct")
    out = credential_service.overview(db)
    assert "enforced" not in out
    assert any(t["team_id"] == team.id for t in out["teams"])


def test_overview_lists_not_ready_published_tasks(db, ds, other_team):
    tmpl = _tmpl(db, ds, other_team, name="tcred-未配账号的已上线任务")
    out = credential_service.overview(db)
    hit = next(r for r in out["not_ready_templates"] if r["template_id"] == tmpl.id)
    assert hit["reason"] == "未配置"
    assert hit["team_name"] == other_team.name


# ---------------------------------------------------------------- 脱敏


def test_redact_removes_username_from_user_facing_text(db, team, ds, team_credential):
    """引擎鉴权报错会带上库账号名,而 job.error 是要给业务用户看的 ——
    Hive auth=NONE 下用户名就是完整凭证,泄一个等于泄掉整个团队的数据权限。"""
    team_credential(team, ds, username="team_acct")
    tmpl = _tmpl(db, ds, team)
    cred = credential_service.for_template(db, tmpl)
    raw = "Access denied for user 'team_acct'@'10.0.0.5' (using password: YES)"
    out = credential_service.redact(raw, cred)
    assert "team_acct" not in out and "***" in out


def test_redact_is_a_noop_without_credential(db):
    assert credential_service.redact("boom", None) == "boom"
    assert credential_service.redact(None, None) == ""


def test_redact_leaves_very_short_usernames_alone(db, team, ds, team_credential):
    """用户名短于 3 字符时不替换,免得在正文里到处打洞。"""
    from app.connectors import Credential

    out = credential_service.redact("a bad thing happened", Credential("ab", None, 1))
    assert out == "a bad thing happened"


# ---------------------------------------------------------------- 反向护栏
# 这几条防的是「悄悄把开关加回来 / 悄悄给公共账号留后门」——那种回归不会让别的断言变红。


def test_no_rollout_switch_exists():
    assert not hasattr(settings, "REQUIRE_OWNER_CREDENTIAL"), (
        "过渡开关已删除:团队取数账号是唯一路径。它的每一条 false 分支都是一次静默回退到"
        "能看全库的公共账号。"
    )
    assert not hasattr(credential_service, "enforced")
    assert not hasattr(credential_service, "for_user"), "for_user 已被 for_team 取代"


def test_get_connector_credential_has_no_default():
    """把 factory docstring 里那句口头承诺变成测试:credential 必填且无默认值。

    给它一个「默认公共账号」的缺省值,等于让任何新增的取数入口只要忘了传参就静默拿到
    能看全库的账号 —— 而那种疏漏不会有任何测试失败。
    """
    param = inspect.signature(get_connector).parameters["credential"]
    assert param.default is inspect.Parameter.empty

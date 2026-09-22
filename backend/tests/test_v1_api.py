"""开放 API v1 端点:任务列表口径、运行闸(开/关)、轮询与可见性、下载直出、429 限流。

与仓库既有测试同风格:直接调路由函数,不起 TestClient。
鉴权依赖(get_api_user)单独在 test_api_token.py 覆盖;这里一律显式传 user,
只有限流守卫 api_user 直接调来测 429。
"""
import pytest
from sqlalchemy import func, select

from app.api.routes import v1 as v1_routes
from app.api.routes.templates import update_template
from app.core import rate_limit, timewindow
from app.core.config import settings
from app.core.exceptions import NotFoundError, PermissionDeniedError, RubicError
from app.models import audit as A
from app.models.audit import DownloadEvent
from app.models.permission import RESOURCE_TEMPLATE, SUBJECT_USER
from app.models.query_job import JOB_SUCCESS, SOURCE_API, SOURCE_RUN, QueryJob
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER
from app.schemas.common import ParamDef
from app.schemas.template import TemplateCreateIn, TemplateUpdateIn
from app.schemas.v1 import V1RunIn
from app.services import analytics_service, api_token_service, audit_service, permission_service, query_service, template_service
from tests.conftest import max_audit_id, new_audit_rows, one_audit_row

# ID 段 9410–9413
AUTHOR, VIEWER, OUTSIDER, ADMIN = 9410, 9411, 9412, 9413


@pytest.fixture
def author(user_factory):
    return user_factory(AUTHOR, ROLE_DEVELOPER, "v1作者", prefix="v1")


@pytest.fixture
def viewer(user_factory):
    return user_factory(VIEWER, ROLE_USER, "v1业务", prefix="v1")


@pytest.fixture
def outsider(user_factory):
    return user_factory(OUTSIDER, ROLE_USER, "v1路人", prefix="v1")


@pytest.fixture
def admin(user_factory):
    return user_factory(ADMIN, ROLE_ADMIN, "v1管理员", prefix="v1")


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("v1-mysql")


@pytest.fixture
def team(db, ds, author, team_factory, team_credential):
    t = team_factory("v1-team", [(author, True)])
    team_credential(t, ds, username="v1_team_acct")
    return t


def _make_task(db, author, ds, team, name, *, allow_api: bool):
    """建一个带一个单值参数、已上线的任务;allow_api 由用例显式指定(运行闸是被测对象)。"""
    tmpl = template_service.create_template(
        db, author,
        TemplateCreateIn(
            name=name, team_id=team.id, datasource_id=ds.id,
            sql_text="SELECT :d", params=[ParamDef(name="d", kind="single", label="日期")],
            allow_api=allow_api,
        ),
    )
    template_service.publish(db, tmpl, author, None)
    db.refresh(tmpl)
    return tmpl


@pytest.fixture
def task_api(db, author, ds, team, viewer):
    """已开「允许 API 调用」的任务;viewer 被授 view+run(不含 download)。"""
    tmpl = _make_task(db, author, ds, team, "v1-开放任务", allow_api=True)
    permission_service.grant(
        db, subject_type=SUBJECT_USER, resource_type=RESOURCE_TEMPLATE,
        resource_id=str(tmpl.id), actions=["view", "run"],
        granted_by=author.id, subject_id=str(viewer.id),
    )
    return tmpl


@pytest.fixture
def task_web(db, author, ds, team, viewer):
    """未开「允许 API 调用」的任务(默认关);viewer 同样有 view+run —— 权限够,闸没开。"""
    tmpl = _make_task(db, author, ds, team, "v1-网页任务", allow_api=False)
    permission_service.grant(
        db, subject_type=SUBJECT_USER, resource_type=RESOURCE_TEMPLATE,
        resource_id=str(tmpl.id), actions=["view", "run"],
        granted_by=author.id, subject_id=str(viewer.id),
    )
    return tmpl


def test_tasks_list_visibility_and_shape(db, task_api, viewer, outsider):
    out = v1_routes.list_tasks(db, viewer)
    row = next((t for t in out if t.id == task_api.id), None)
    assert row is not None
    assert row.allow_api is True
    assert row.can_run is True            # 被授予 run
    assert row.can_download is False      # 没授 download —— 能力位必须如实分开
    assert row.status == "published" and row.team_name == "v1-team"
    # 参数定义取自已上线版本,调用方照它填 values
    assert [p.name for p in row.params] == ["d"]

    # 无关路人:什么都看不到(可见口径与界面任务列表同源)
    assert v1_routes.list_tasks(db, outsider) == []


def test_run_gate_closed_is_denied_and_audited(db, task_web, viewer):
    """权限够、但任务没开「允许 API 调用」:fail-closed 拒绝,且留下 api_run_denied 审计。"""
    since = max_audit_id(db)
    with pytest.raises(PermissionDeniedError, match="未开放 API 调用"):
        v1_routes.run_task(task_web.id, V1RunIn(values={"d": "2026-09-21"}), db, viewer, ip=None)
    row = one_audit_row(db, since)
    assert row.action == A.ACTION_API_RUN_DENIED
    assert row.resource_type == A.RESOURCE_TEMPLATE and row.resource_id == str(task_web.id)
    assert row.detail["reason"] == "allow_api_off"
    # 拒绝发生在入队之前:不该有任何运行记录留下
    assert not db.scalar(
        select(func.count(QueryJob.id)).where(
            QueryJob.template_id == task_web.id, QueryJob.source == SOURCE_API
        )
    )


def test_run_gate_open_enqueues_with_api_source(db, task_api, viewer):
    since = max_audit_id(db)
    out = v1_routes.run_task(task_api.id, V1RunIn(values={"d": "2026-09-21"}), db, viewer, ip=None)
    assert out.status == "queued"
    assert out.source == SOURCE_API
    assert out.queue_ahead is not None  # 入队即带位次,轮询方据此知道在排队

    row = one_audit_row(db, since)
    assert row.action == A.ACTION_SUBMIT_QUERY
    assert row.detail["via"] == "api" and row.detail["job_id"] == out.id


def test_run_permission_checked_before_gate(db, task_api, outsider):
    """没运行权限的人撞上的是「无权运行」,而不是运行闸 ——
    闸的开关状态不该泄露给无权运行的人。"""
    since = max_audit_id(db)
    with pytest.raises(PermissionDeniedError, match="无权运行"):
        v1_routes.run_task(task_api.id, V1RunIn(values={"d": "2026-09-21"}), db, outsider, ip=None)
    assert [r.action for r in new_audit_rows(db, since)] == []


def test_poll_and_runs_list_visibility(db, task_api, viewer, outsider):
    job = query_service.enqueue(db, viewer, task_api.id, {"d": "2026-09-21"}, via="api")

    out = v1_routes.get_run(job.id, db, viewer)
    assert out.id == job.id and out.status == "queued"

    # 不可见 = 404(与界面口径一致:不暴露「存在但无权」)
    with pytest.raises(NotFoundError):
        v1_routes.get_run(job.id, db, outsider)

    ids = [j.id for j in v1_routes.list_runs(db, viewer)]
    assert job.id in ids
    assert job.id not in [j.id for j in v1_routes.list_runs(db, outsider)]


def test_preview_and_download_direct(db, task_api, viewer, spy_connector):
    """跑通(假连接器)→ 预览 JSON → Bearer 直出 CSV;下载按 via=api 留痕。"""
    spy_connector(rows=[("2026-09-21",), ("2026-09-22",)])
    job = query_service.enqueue(db, viewer, task_api.id, {"d": "2026-09-21"}, via="api")
    query_service.execute_job(job.id)
    db.refresh(job)
    assert job.status == JOB_SUCCESS

    preview = v1_routes.preview_run(job.id, db, viewer)
    assert preview["columns"] == ["c"]
    assert preview["rows"] == [["2026-09-21"], ["2026-09-22"]]
    assert preview["row_count"] == 2

    since = max_audit_id(db)
    resp = v1_routes.download_result(job.id, db, viewer, ip=None)
    # FileResponse 直出:不给签名 URL,文件路径指向结果目录里的真实 CSV
    assert resp.media_type == "text/csv; charset=utf-8"
    assert str(resp.path).endswith(job.result_object_key)

    row = one_audit_row(db, since)
    assert row.action == A.ACTION_DOWNLOAD
    assert row.detail["via"] == "api"
    ev = db.scalar(
        select(DownloadEvent).where(DownloadEvent.job_id == job.id).order_by(DownloadEvent.id.desc())
    )
    assert ev.via == "api" and ev.user_id == viewer.id

    # 对照:界面下载(签名 URL 通道)记 via=web
    query_service.get_download_url(db, viewer, job)
    ev_web = db.scalar(
        select(DownloadEvent).where(DownloadEvent.job_id == job.id).order_by(DownloadEvent.id.desc())
    )
    assert ev_web.via == "web"


def test_download_of_unfinished_run_is_rejected(db, task_api, viewer):
    job = query_service.enqueue(db, viewer, task_api.id, {"d": "2026-09-21"}, via="api")
    with pytest.raises(RubicError, match="无可下载结果"):
        v1_routes.download_result(job.id, db, viewer, ip=None)
    with pytest.raises(RubicError, match="无可预览结果"):
        v1_routes.preview_run(job.id, db, viewer)


def test_rate_limit_429(db, viewer, monkeypatch):
    """限流守卫:窗口内第 limit+1 次起 429;<=0 关闭。直接调依赖函数(与仓库同风格)。"""
    rate_limit.reset()
    monkeypatch.setattr(settings, "API_RATE_LIMIT_PER_MINUTE", 3)
    try:
        for _ in range(3):
            assert v1_routes.api_user(viewer) is viewer
        with pytest.raises(RubicError) as ei:
            v1_routes.api_user(viewer)
        assert ei.value.status_code == 429

        monkeypatch.setattr(settings, "API_RATE_LIMIT_PER_MINUTE", 0)
        assert v1_routes.api_user(viewer) is viewer  # 关闭限流后恒放行
    finally:
        rate_limit.reset()


def test_allow_api_toggle_lands_in_update_audit(db, task_web, author):
    """开关的每次变动都要在任务编辑审计的 diff 里看得见(_TMPL_AUDIT_FIELDS 含 allow_api)。"""
    since = max_audit_id(db)
    update_template(task_web.id, TemplateUpdateIn(allow_api=True), db, author, ip=None)
    row = one_audit_row(db, since)
    assert row.action == A.ACTION_TASK_UPDATE
    assert row.detail["changes"]["allow_api"] == {"from": False, "to": True}
    db.refresh(task_web)
    assert task_web.allow_api is True


def test_analytics_api_block(db, admin, author, team, task_api, viewer, clean_jobs):
    """API 板块:token 发放/活跃、API 运行数与占比、API 下载数、Top 任务。"""
    token, _ = api_token_service.issue(db, viewer)
    api_token_service.authenticate(db, token)  # 盖上「最近使用」→ 近 7 天活跃

    api_job = query_service.enqueue(db, viewer, task_api.id, {"d": "2026-09-21"}, via="api")
    audit_service.log_download(
        db, user=viewer, job_id=api_job.id, filename="a.csv", row_count=1, ip=None, via="api"
    )
    # 一条对照组:界面来源的运行,占比的分母
    query_service.enqueue(db, viewer, task_api.id, {"d": "2026-09-22"})

    scope = analytics_service.resolve_scope(db, admin)
    res = analytics_service.api_usage(db, scope, timewindow.resolve_window(days=7))
    assert res["scope"]["level"] == "platform"
    assert res["tokens_issued"]["value"] >= 1
    assert res["tokens_issued"]["windowed"] is False
    assert res["tokens_active_7d"]["value"] >= 1
    assert res["api_runs"]["value"] == 1
    assert res["api_run_share"]["value"] == 0.5
    assert res["api_downloads"]["value"] == 1
    assert res["top_tasks"][0]["template_id"] == task_api.id
    assert res["top_tasks"][0]["run_count"] == 1

    # 团队视角:token 与运行都收窄到本团队成员/本团队任务
    team_scope_ = analytics_service.resolve_scope(db, author, team_id=team.id)
    res_team = analytics_service.api_usage(db, team_scope_, timewindow.resolve_window(days=7))
    assert res_team["api_runs"]["value"] == 1
    assert res_team["tokens_issued"]["value"] == 0  # viewer 不在团队里,token 不计入

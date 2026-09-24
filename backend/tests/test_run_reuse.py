"""运行提交的「复用优先,重跑始终可用」(query_service.submit_run)与队列治理。

- 复用时效内的同参结果:跨用户、另建一条 success 记录共用结果文件、不执行;
- 接上同参在途运行:API 看得见就接,界面只接本人的;
- fresh=true 一定新跑;
- API 每用户在途上限、取消排队中的运行;
- worker 先认领非 API 的运行,queue_ahead 与之同序。

用例直接调用路由函数(同 test_v1_api 的写法),不起 TestClient。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app import worker
from app.api.routes import query as query_routes
from app.api.routes import v1 as v1_routes
from app.core.config import settings
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    RubicError,
)
from app.models import audit as A
from app.models.query_job import (
    JOB_CANCELLED,
    JOB_QUEUED,
    JOB_RUNNING,
    JOB_SUCCESS,
    SOURCE_API,
    QueryJob,
)
from app.models.permission import RESOURCE_TEMPLATE, SUBJECT_USER
from app.models.user import ROLE_DEVELOPER, ROLE_USER
from app.schemas.common import ParamDef
from app.schemas.query import RunIn
from app.schemas.template import TemplateCreateIn
from app.schemas.v1 import V1RunIn
from app.core import timewindow
from app.services import (
    analytics_service,
    permission_service,
    query_service,
    result_service,
    template_service,
)
from tests.conftest import db_now, max_audit_id, new_audit_rows

# ID 段 9490–9493
AUTHOR, VIEWER, OTHER, NOGRANT = 9490, 9491, 9492, 9493

D = {"d": "2026-09-21"}


@pytest.fixture(autouse=True)
def _empty_queue(clean_jobs):
    """在途上限与认领顺序都是全表口径,上一个用例留下的 queued 行会串进下一个用例。"""


@pytest.fixture
def author(user_factory):
    return user_factory(AUTHOR, ROLE_DEVELOPER, "复用作者", prefix="reuse")


@pytest.fixture
def viewer(user_factory):
    return user_factory(VIEWER, ROLE_USER, "复用业务甲", prefix="reuse")


@pytest.fixture
def other(user_factory):
    return user_factory(OTHER, ROLE_USER, "复用业务乙", prefix="reuse")


@pytest.fixture
def nogrant(user_factory):
    return user_factory(NOGRANT, ROLE_USER, "复用只读", prefix="reuse")


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("reuse-mysql")


@pytest.fixture
def hive_ds(datasource_factory):
    return datasource_factory("reuse-hive", engine="hive", port=10000)


def _team(team_factory, team_credential, author, *dss):
    t = team_factory("reuse-team", [(author, True)])
    for d in dss:
        team_credential(t, d, username="reuse_team_acct")
    return t


def _grant(db, author, tmpl, user, actions=("view", "run")):
    permission_service.grant(
        db, subject_type=SUBJECT_USER, resource_type=RESOURCE_TEMPLATE,
        resource_id=str(tmpl.id), actions=list(actions),
        granted_by=author.id, subject_id=str(user.id),
    )


def _make_task(
    db, author, datasource, team, users, *, params=None, sql_text="SELECT :d", **extra
):
    tmpl = template_service.create_template(
        db, author,
        TemplateCreateIn(
            name="复用任务", team_id=team.id, datasource_id=datasource.id,
            sql_text=sql_text,
            params=params or [ParamDef(name="d", kind="single", label="日期")],
            **extra,
        ),
    )
    template_service.publish(db, tmpl, author, None)
    db.refresh(tmpl)
    for u in users:
        _grant(db, author, tmpl, u)
    return tmpl


@pytest.fixture
def task(db, author, ds, viewer, other, team_factory, team_credential):
    return _make_task(db, author, ds, _team(team_factory, team_credential, author, ds),
                      [viewer, other])


@pytest.fixture
def hive_task(db, author, hive_ds, viewer, other, team_factory, team_credential):
    return _make_task(db, author, hive_ds,
                      _team(team_factory, team_credential, author, hive_ds), [viewer, other])


def _api(db, user, tmpl, values=D, *, fresh=False):
    return v1_routes.run_task(tmpl.id, V1RunIn(values=values, fresh=fresh), db, user, ip=None)


def _web(db, user, tmpl, values=D, *, fresh=False):
    class _Req:  # routes.query.run_query 只从 request 上取 client ip
        headers: dict = {}
        client = None

    return query_routes.run_query(
        RunIn(template_id=tmpl.id, values=values, fresh=fresh), _Req(), db, user
    )


def _finish(db, job_id):
    query_service.execute_job(job_id)
    job = db.get(QueryJob, job_id)
    db.refresh(job)
    assert job.status == JOB_SUCCESS
    return job


# ---- 复用时效内的同参结果 ----

def test_reuses_another_users_result_without_executing(db, task, viewer, other, spy_connector):
    """乙以同参(值换了写法也行)提交 → 立即拿到一条**自己名下**的 success,共用甲那份文件,
    连接器没被调用;审计记下复用来源。"""
    spy_connector(rows=[("2026-09-21",)])
    src = _finish(db, _api(db, viewer, task).id)

    calls = spy_connector(rows=[("x",)])
    floor = max_audit_id(db)
    out = _api(db, other, task)

    assert out.reuse_kind == "result"
    assert out.status == JOB_SUCCESS and out.id != src.id
    assert out.reused_from_at is not None
    copy = db.get(QueryJob, out.id)
    assert copy.user_id == other.id and copy.source == SOURCE_API
    assert copy.reused_from_job_id == src.id
    assert copy.result_object_key == src.result_object_key
    assert copy.started_at is None and copy.duration_ms is None
    assert calls == []  # 没有执行
    (row,) = [r for r in new_audit_rows(db, floor) if r.action == A.ACTION_SUBMIT_QUERY]
    assert row.detail["reuse_kind"] == "result"
    assert row.detail["reused_from_job_id"] == src.id


def test_reused_copy_is_downloadable_only_with_own_grant(db, task, viewer, other, spy_connector):
    """复用不放宽下载:乙只有 view+run,拿到复用记录也取不走完整文件。"""
    spy_connector()
    _finish(db, _api(db, viewer, task).id)
    copy = db.get(QueryJob, _api(db, other, task).id)
    with pytest.raises(PermissionDeniedError):
        query_service.assert_downloadable(db, other, copy)


def test_list_params_reuse_ignores_order(
    db, author, ds, viewer, other, team_factory, team_credential, spy_connector
):
    tmpl = _make_task(
        db, author, ds, _team(team_factory, team_credential, author, ds), [viewer, other],
        sql_text="SELECT c FROM t WHERE region IN (:d)",  # 值列表只在 IN (...) 里成立
        params=[ParamDef(name="d", kind="list", label="地区")],
    )
    spy_connector()
    src = _finish(db, _api(db, viewer, tmpl, {"d": ["华东", "华南"]}).id)
    out = _api(db, other, tmpl, {"d": ["华南", "华东"]})
    assert out.reuse_kind == "result"
    assert db.get(QueryJob, out.id).reused_from_job_id == src.id


def test_reused_copy_is_never_itself_a_source(db, task, viewer, other, spy_connector):
    """复用链不会拉长:第三次提交指向的仍是原始那条。"""
    spy_connector()
    src = _finish(db, _api(db, viewer, task).id)
    _api(db, other, task)
    third = db.get(QueryJob, _web(db, viewer, task).id)
    assert third.reused_from_job_id == src.id


@pytest.mark.parametrize(
    "spoil",
    ["fresh", "task_off", "global_off", "other_params", "stale", "file_gone", "mysql_off"],
)
def test_no_reuse_when_not_eligible(db, task, viewer, other, spy_connector, monkeypatch, spoil):
    spy_connector()
    src = _finish(db, _api(db, viewer, task).id)
    values, fresh = D, False
    if spoil == "fresh":
        fresh = True
    elif spoil == "task_off":
        task.allow_result_reuse = False
        db.commit()
    elif spoil == "global_off":
        monkeypatch.setattr(settings, "RESULT_REUSE_ENABLED", False)
    elif spoil == "other_params":
        values = {"d": "2026-09-22"}
    elif spoil == "stale":  # MySQL:超出最近 N 分钟
        src.started_at = src.created_at = db_now(db) - timedelta(
            minutes=settings.RESULT_REUSE_MYSQL_MINUTES + 600
        )
        db.commit()
    elif spoil == "file_gone":
        result_service.local_path(src.result_object_key).unlink()
    elif spoil == "mysql_off":
        monkeypatch.setattr(settings, "RESULT_REUSE_MYSQL_MINUTES", 0)
    out = _api(db, other, task, values, fresh=fresh)
    assert out.reuse_kind is None and out.status == JOB_QUEUED


def test_hive_reuses_within_the_day_only(db, hive_task, viewer, other, spy_connector):
    """Hive 以当天为界:几小时前的结果照样复用,昨天的不复用。"""
    spy_connector()
    src = _finish(db, _api(db, viewer, hive_task).id)
    # 当天界线是应用时钟的零点(start_of_today),取「今天零点后一秒」与「库时钟五小时前」里晚的那个
    src.started_at = max(
        datetime.now().replace(hour=0, minute=0, second=1), db_now(db) - timedelta(hours=5)
    )
    db.commit()
    assert _api(db, other, hive_task).reuse_kind == "result"

    src.started_at = src.created_at = db_now(db) - timedelta(days=1)
    db.commit()
    # 前一次复用出来的记录不当来源,所以这次只能新跑
    assert _api(db, viewer, hive_task).reuse_kind is None


def test_gates_still_apply_before_reuse(
    db, task, viewer, nogrant, author, spy_connector
):
    """没有运行权 / 任务关了 API,就算有现成结果也照样被拒 —— 不能借复用拿到一条运行。"""
    spy_connector()
    _finish(db, _api(db, viewer, task).id)
    _grant(db, author, task, nogrant, actions=("view",))
    with pytest.raises(PermissionDeniedError):
        _api(db, nogrant, task)
    task.allow_api = False
    db.commit()
    with pytest.raises(PermissionDeniedError):
        _api(db, viewer, task)


# ---- 接上在途运行 ----

def test_api_attaches_to_any_visible_inflight_run(db, task, viewer, other):
    first = _api(db, viewer, task)
    again = _api(db, viewer, task)
    by_other = _api(db, other, task)
    assert again.id == by_other.id == first.id
    assert again.reuse_kind == by_other.reuse_kind == "inflight"
    assert db.scalar(select(QueryJob.id).where(QueryJob.id > first.id)) is None


def test_web_attaches_only_to_own_inflight_run(db, task, viewer, other):
    """界面只接本人的:完成通知只发给发起人,接上别人的那条就等不到「跑完了」。"""
    mine = _web(db, viewer, task)
    assert _web(db, viewer, task).id == mine.id
    theirs = _web(db, other, task)
    assert theirs.id != mine.id and theirs.reuse_kind is None


def test_fresh_always_runs_even_with_inflight_or_result(db, task, viewer, spy_connector):
    """复用只是优先:fresh=true 无论网页还是 API 都新建一条并真的执行。"""
    inflight = _api(db, viewer, task)
    forced = _api(db, viewer, task, fresh=True)
    assert forced.id != inflight.id and forced.reuse_kind is None

    calls = spy_connector()
    _finish(db, inflight.id)
    _finish(db, forced.id)
    web_forced = _web(db, viewer, task, fresh=True)
    assert web_forced.reuse_kind is None and web_forced.status == JOB_QUEUED
    _finish(db, web_forced.id)
    assert len(calls) == 3


# ---- API 在途上限 ----

def test_inflight_cap_rejects_with_429_and_audit(db, task, viewer, monkeypatch):
    monkeypatch.setattr(settings, "API_MAX_INFLIGHT_PER_USER", 2)
    _api(db, viewer, task, {"d": "1"})
    _api(db, viewer, task, {"d": "2"})
    # 同参重复提交不占名额:接上在途那条,而不是 429
    assert _api(db, viewer, task, {"d": "1"}).reuse_kind == "inflight"

    floor = max_audit_id(db)
    with pytest.raises(RubicError) as ei:
        _api(db, viewer, task, {"d": "3"})
    assert ei.value.status_code == 429
    (row,) = new_audit_rows(db, floor)
    assert row.action == A.ACTION_API_RUN_DENIED
    assert row.detail["reason"] == "inflight_cap"
    # fresh 也受上限约束(它限的是占几个队列位)
    with pytest.raises(RubicError):
        _api(db, viewer, task, {"d": "1"}, fresh=True)


def test_inflight_cap_frees_after_cancel_and_ignores_web(db, task, viewer, monkeypatch):
    monkeypatch.setattr(settings, "API_MAX_INFLIGHT_PER_USER", 1)
    first = _api(db, viewer, task, {"d": "1"})
    _web(db, viewer, task, {"d": "9"})  # 界面运行不受 API 上限约束,也不占 API 名额
    with pytest.raises(RubicError):
        _api(db, viewer, task, {"d": "2"})
    v1_routes.cancel_run(first.id, db, viewer, ip=None)
    assert _api(db, viewer, task, {"d": "2"}).status == JOB_QUEUED


def test_inflight_cap_zero_means_unlimited(db, task, viewer, monkeypatch):
    monkeypatch.setattr(settings, "API_MAX_INFLIGHT_PER_USER", 0)
    for i in range(5):
        _api(db, viewer, task, {"d": str(i)})


# ---- 取消 ----

def test_cancel_own_queued_run(db, task, viewer, spy_connector):
    job = _api(db, viewer, task)
    floor = max_audit_id(db)
    out = v1_routes.cancel_run(job.id, db, viewer, ip=None)
    assert out.status == JOB_CANCELLED
    (row,) = new_audit_rows(db, floor)
    assert row.action == A.ACTION_QUERY_CANCEL and row.detail["job_id"] == job.id

    calls = spy_connector()
    query_service.execute_job(job.id)  # 取消的不会再被执行
    assert calls == [] and db.get(QueryJob, job.id).status == JOB_CANCELLED
    assert worker._claim_next_job_id() is None
    with pytest.raises(ConflictError):
        v1_routes.cancel_run(job.id, db, viewer, ip=None)


def test_cancel_rejects_others_and_started_runs(db, task, viewer, other):
    job = _api(db, viewer, task)
    with pytest.raises(NotFoundError):  # 看得见也不能替别人取消
        v1_routes.cancel_run(job.id, db, other, ip=None)
    db.get(QueryJob, job.id).status = JOB_RUNNING
    db.commit()
    with pytest.raises(ConflictError):
        v1_routes.cancel_run(job.id, db, viewer, ip=None)


# ---- 认领顺序:非 API 优先 ----

def test_worker_claims_non_api_first_and_queue_ahead_agrees(db, task, viewer):
    api1 = _api(db, viewer, task, {"d": "a1"})
    api2 = _api(db, viewer, task, {"d": "a2"})
    web1 = _web(db, viewer, task, {"d": "w1"})

    ahead = {j.id: query_service.queue_ahead(db, db.get(QueryJob, j.id)) for j in (api1, api2, web1)}
    assert ahead == {web1.id: 0, api1.id: 1, api2.id: 2}

    order = [worker._claim_next_job_id() for _ in range(3)]
    assert order == [web1.id, api1.id, api2.id]


# ---- 运营分析:复用记录算「有人用」,不算「执行」----

def test_health_excludes_reused_copies_and_counts_hits(db, task, author, viewer, other, spy_connector):
    spy_connector()
    _finish(db, _web(db, viewer, task).id)
    assert _web(db, other, task).reuse_kind == "result"  # source=run 的复用记录
    cancelled = _api(db, other, task, {"d": "x"})
    v1_routes.cancel_run(cancelled.id, db, other, ip=None)

    scope = analytics_service.resolve_scope(db, author)
    res = analytics_service.health(db, scope, timewindow.resolve_window(days=7))
    assert res["reuse_hits"]["value"] == 1
    # 正式取数的成功率只数真正执行过的那一条;取消的既不算成也不算败
    assert res["by_source"]["run"] == {
        "total": 1, "success": 1, "failed": 0, "success_rate": 1.0,
    }
    assert res["in_flight"] == {"queued": 0, "running": 0}


def test_editor_toggles_result_reuse(db, task, author):
    from app.api.routes.templates import update_template
    from app.schemas.template import TemplateUpdateIn

    assert task.allow_result_reuse is True  # 新任务默认开
    update_template(task.id, TemplateUpdateIn(allow_result_reuse=False), db, author, ip=None)
    db.refresh(task)
    assert task.allow_result_reuse is False
    # 未携带 = 不动
    update_template(task.id, TemplateUpdateIn(name="改个名"), db, author, ip=None)
    db.refresh(task)
    assert task.allow_result_reuse is False

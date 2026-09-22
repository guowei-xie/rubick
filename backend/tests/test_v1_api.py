"""开放 API v1 端点:任务列表口径、运行闸(开/关)、轮询与可见性、下载直出、429 限流。

与仓库既有测试同风格:直接调路由函数,不起 TestClient。
鉴权依赖(get_api_user)单独在 test_api_token.py 覆盖;这里一律显式传 user,
只有限流守卫 api_user 直接调来测 429。
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.api.routes import v1 as v1_routes
from app.api.routes.templates import update_template
from app.core import rate_limit, timewindow
from app.core.config import settings
from app.core.exceptions import (
    NotFoundError,
    PermissionDeniedError,
    ResultExpiredError,
    RubicError,
)
from app.models import audit as A
from app.models.audit import DownloadEvent
from app.models.permission import RESOURCE_TEMPLATE, SUBJECT_USER
from app.models.query_job import (
    JOB_FAILED,
    JOB_SUCCESS,
    SOURCE_API,
    SOURCE_RUN,
    SOURCE_SUBSCRIBE,
    QueryJob,
)
from app.models.subscription import TaskSubscription
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER
from app.schemas.common import ParamDef
from app.schemas.query import JobOut
from app.schemas.template import TemplateCreateIn, TemplateUpdateIn, ValueListOut
from app.schemas.v1 import V1JobOut, V1RunIn
from app.services import (
    analytics_service,
    audit_service,
    enum_cache_service,
    permission_service,
    query_service,
    result_service,
    template_service,
)
from tests.conftest import (
    max_audit_id,
    new_audit_rows,
    new_notifications,
    note_floor,
    one_audit_row,
)

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


def _grant(db, author, tmpl, user, actions=("view", "run")):
    """给某人授某任务的业务权限。同其它测试文件的 _grant* 局部 helper(如
    test_enum_cache._grant),把 6 行 kwargs 样板从用例正文里挪开。"""
    permission_service.grant(
        db, subject_type=SUBJECT_USER, resource_type=RESOURCE_TEMPLATE,
        resource_id=str(tmpl.id), actions=list(actions),
        granted_by=author.id, subject_id=str(user.id),
    )


def _make_task(
    db, author, ds, team, viewer, name, *, allow_api: bool,
    sql_text: str = "SELECT :d",
    params=(ParamDef(name="d", kind="single", label="日期"),),
):
    """建一个已上线任务,并给 viewer 授 view+run(**不含 download** —— 能力位必须分得开)。

    allow_api 由用例显式指定:运行闸正是被测对象。sql_text / params 默认是一个单值参数的
    最小任务;要测参数投影与候选值的用例传自己的(见 task_enum)。
    """
    tmpl = template_service.create_template(
        db, author,
        TemplateCreateIn(
            name=name, team_id=team.id, datasource_id=ds.id,
            sql_text=sql_text, params=list(params), allow_api=allow_api,
        ),
    )
    template_service.publish(db, tmpl, author, None)
    db.refresh(tmpl)
    _grant(db, author, tmpl, viewer)
    return tmpl


def _succeed_a_run(db, spy_connector, task, user, rows=(("2026-09-21",),)):
    """跑出一条成功的运行记录(假连接器),返回 job。"""
    spy_connector(rows=list(rows))
    job = query_service.enqueue(db, user, task.id, {"d": "2026-09-21"}, source="api")
    query_service.execute_job(job.id)
    db.refresh(job)
    assert job.status == JOB_SUCCESS
    return job


def _row_of(db, user, template_id):
    return next(t for t in v1_routes.list_tasks(db, user) if t.id == template_id)


@pytest.fixture
def task_api(db, author, ds, team, viewer):
    """已开「允许 API 调用」的任务。"""
    return _make_task(db, author, ds, team, viewer, "v1-开放任务", allow_api=True)


@pytest.fixture
def task_web(db, author, ds, team, viewer):
    """未开「允许 API 调用」的任务(默认关):权限够,闸没开。"""
    return _make_task(db, author, ds, team, viewer, "v1-网页任务", allow_api=False)


ENUM_SQL = "SELECT DISTINCT region FROM orders"


@pytest.fixture
def task_enum(db, author, ds, team, viewer):
    """带一个**配了枚举 SQL 的值列表变量**的已上线任务,并已采集过一批候选值。

    v1 的参数投影与候选值都要它:单值参数那条路上 enum_sql 恒为 None,测不出泄漏。
    """
    tmpl = _make_task(
        db, author, ds, team, viewer, "v1-枚举任务", allow_api=True,
        sql_text="SELECT c FROM t WHERE region IN (:regions)",
        params=[ParamDef(
            name="regions", kind="list", label="大区(可多选)",
            enum_sql=ENUM_SQL, allow_bulk_input=True, test_value=["华东"],
        )],
    )
    enum_cache_service.upsert(
        db, template_id=tmpl.id, variable="regions", datasource_id=ds.id, enum_sql=ENUM_SQL,
        result=ValueListOut(values=["华东", "华南"], truncated=False, duration_ms=3),
        user_id=author.id,
    )
    db.commit()
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


def test_api_run_sends_no_notification(db, task_api, viewer, spy_connector):
    """API 触发的运行**不推任何通知** —— 成功与失败一视同仁。

    通知是给「在界面上等结果的人」用的;API 的调用方是脚本或 Agent,它本来就在轮询
    GET /runs/{id},status 与 error 都在轮询响应里,再推一条是纯重复信息。而 API 调用
    天然高频,不静音的话几百次调用就是几百条飞书卡片,把 token 主人真正在界面上跑的那
    几条通知一起淹掉。
    """
    floor = note_floor(db)
    _succeed_a_run(db, spy_connector, task_api, viewer)
    assert new_notifications(db, floor) == [], "成功不推:调用方轮询就拿到了 status 与行数"

    spy_connector(fail="目标库连接超时")
    failed = query_service.enqueue(
        db, viewer, task_api.id, {"d": "2026-09-21"}, source=SOURCE_API
    )
    query_service.execute_job(failed.id)
    db.refresh(failed)
    assert failed.status == JOB_FAILED and failed.error, "失败原因照旧落在运行记录上"
    assert new_notifications(db, floor) == [], "失败也不推:原因就在轮询响应的 error 里"


def test_web_run_on_the_same_task_still_notifies(db, task_api, viewer, spy_connector):
    """对照组:同一个任务走界面路径(source=run)照常推通知。

    静音的判据是**来源**而不是任务 —— 开了「允许 API 调用」的任务,业务同学在界面上跑
    它时仍然有人在等结果。没有这条,「API 不推」被重构成「谁都不推」不会让任何测试变红。
    """
    spy_connector(rows=[("2026-09-21",)])
    floor = note_floor(db)
    job = query_service.enqueue(db, viewer, task_api.id, {"d": "2026-09-21"})
    assert job.source == SOURCE_RUN
    query_service.execute_job(job.id)

    assert [(n.user_id, n.title) for n in new_notifications(db, floor)] == [
        (viewer.id, "取数完成")
    ]


def test_run_on_invisible_task_is_404(db, task_api, outsider):
    """连看都看不见的任务:404,不是 403。

    「不可见一律 404,不暴露存在但无权」是全平台口径(单条运行记录一直如此,见
    test_poll_and_runs_list_visibility),此前只有这条入队路径是例外 —— 于是拿一枚 token
    逐个试编号,就能从 403 与 404 的差别里问出平台上有哪些任务。
    """
    since = max_audit_id(db)
    with pytest.raises(NotFoundError):
        v1_routes.run_task(task_api.id, V1RunIn(values={"d": "2026-09-21"}), db, outsider, ip=None)
    assert [r.action for r in new_audit_rows(db, since)] == []


def test_run_permission_checked_before_gate(db, task_api, author, outsider):
    """看得见、但没被授权运行的人撞上的是「无权运行」,而不是运行闸 ——
    闸的开关状态不该泄露给无权运行的人(它是任务编辑者的配置,不是给外人的信息)。"""
    _grant(db, author, task_api, outsider, actions=["view"])
    since = max_audit_id(db)
    with pytest.raises(PermissionDeniedError, match="无权运行"):
        v1_routes.run_task(task_api.id, V1RunIn(values={"d": "2026-09-21"}), db, outsider, ip=None)
    assert [r.action for r in new_audit_rows(db, since)] == []


def test_poll_and_runs_list_visibility(db, task_api, viewer, outsider):
    job = query_service.enqueue(db, viewer, task_api.id, {"d": "2026-09-21"}, source="api")

    out = v1_routes.get_run(job.id, db, viewer)
    assert out.id == job.id and out.status == "queued"

    # 不可见 = 404(与界面口径一致:不暴露「存在但无权」)
    with pytest.raises(NotFoundError):
        v1_routes.get_run(job.id, db, outsider)

    ids = [j.id for j in v1_routes.list_runs(db, viewer)]
    assert job.id in ids
    assert job.id not in [j.id for j in v1_routes.list_runs(db, outsider)]


def test_preview_and_download_direct(db, task_api, author, viewer, spy_connector):
    """跑通(假连接器)→ 预览 JSON → Bearer 直出 CSV;下载按 via=api 留痕。

    本用例测的是下载通道的留痕,不是下载闸(闸单独两条)。所以在用例内显式补一条 download
    授权,而**不改 task_api fixture** —— 它的「只有 view+run」正是闸那两条用例的前提,
    也被 test_tasks_list_visibility_and_shape 的 can_download is False 依赖着。
    顺带这也覆盖了闸的放行分支之一:团队外、被显式授予 download 的业务使用者。
    """
    _grant(db, author, task_api, viewer, actions=["download"])
    job = _succeed_a_run(
        db, spy_connector, task_api, viewer, rows=[("2026-09-21",), ("2026-09-22",)]
    )

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


def test_download_of_unfinished_run_is_rejected(db, task_api, author, viewer):
    """状态这一层的回归。

    下载半边必须用**有下载权**的人(author 是团队成员)来撞 —— 闸在状态之前,用 viewer
    撞到的会是 403,这条用例就测不到它本来要守的东西了。预览半边刻意留给没有下载权的
    viewer:它顺带断言了「预览不叠下载闸」。
    """
    job = query_service.enqueue(db, viewer, task_api.id, {"d": "2026-09-21"}, source="api")
    with pytest.raises(RubicError, match="无可下载结果"):
        v1_routes.download_result(job.id, db, author, ip=None)
    with pytest.raises(RubicError, match="无可预览结果"):
        v1_routes.preview_run(job.id, db, viewer)


def test_task_params_expose_the_contract_and_nothing_else(db, task_enum, viewer):
    """参数投影只给文档 3.1 承诺的五个字段。

    此前 v1 直接外抛内部 ParamDef,把 **enum_sql(取候选值的 SQL 原文)** 连同 test_value、
    allow_bulk_input、enum_sql_duration_ms 一起发给了纯业务身份的 token —— 而业务用户
    在界面上是看不到任何 SQL 的。这条断言点名 enum_sql,将来有人把它加回来时一眼能读懂。
    """
    p = _row_of(db, viewer, task_enum.id).params[0]
    assert set(p.model_dump()) == {"name", "kind", "value_type", "label", "enum"}
    assert "enum_sql" not in p.model_dump()
    assert (p.name, p.kind, p.value_type, p.label) == (
        "regions", "list", "text", "大区(可多选)"
    )


def test_task_params_carry_shared_enum_candidates(db, task_enum, viewer, author):
    """文档 3.1 承诺的 enum:有候选就给出来,Agent 据此把选项列给用户而不是让他盲猜。

    作者改掉枚举 SQL 后旧候选作废 —— 给空数组,**不回旧值**(与界面同一条产品决策:
    旧 SQL 的结果不能冒充新 SQL 的候选)。
    """
    assert _row_of(db, viewer, task_enum.id).params[0].enum == ["华东", "华南"]

    update_template(
        task_enum.id,
        TemplateUpdateIn(params=[ParamDef(
            name="regions", kind="list", label="大区(可多选)",
            enum_sql="SELECT DISTINCT region FROM orders WHERE dt > '2026-01-01'",
        )]),
        db, author, ip=None,
    )
    assert _row_of(db, viewer, task_enum.id).params[0].enum == []


def test_enum_candidates_only_for_tasks_i_can_run(db, task_enum, outsider, admin):
    """候选值只给「我能跑的」任务带 —— 跑不了的任务,候选对调用方没用,而载荷是
    任务数 × 变量数 × 每变量上千个值,压在 Agent 每次开场的第一枪上。"""
    # 平台管理员看得见、也能跑 → 带候选
    assert _row_of(db, admin, task_enum.id).params[0].enum == ["华东", "华南"]
    # 路人压根看不见这个任务(可见口径与界面同源)
    assert task_enum.id not in [t.id for t in v1_routes.list_tasks(db, outsider)]


def test_v1_job_out_is_a_projection_of_the_internal_shape(db, task_api, viewer, spy_connector):
    """对外的 job 形状锁死在文档 4.1 那几个字段上,且内部字段不得漏出去。

    第二条断言是**内部改名探测器**:投影靠属性名读值,JobOut 里某个字段改了名,这里只会
    静默变 null 而不会报错 —— 只有这条 ⊆ 断言会红。
    """
    expected = {
        "id", "template_id", "status", "row_count", "duration_ms", "error",
        "queue_ahead", "source", "created_at", "started_at", "result_expired",
    }
    assert set(V1JobOut.model_fields) == expected
    assert set(V1JobOut.model_fields) <= set(JobOut.model_fields)

    job = _succeed_a_run(db, spy_connector, task_api, viewer)
    dumped = v1_routes.get_run(job.id, db, viewer).model_dump()
    assert set(dumped) == expected
    # 点名三个绝不能进对外契约的:SQL 原文、他人入参原文、他人姓名
    for leaked in ("executed_sql", "params", "user_name"):
        assert leaked not in dumped


def test_download_without_grant_is_denied_and_leaves_no_trace(
    db, task_api, viewer, spy_connector
):
    """被授 view+run 但没授 download 的人:能跑、能预览,**取不走完整 CSV**。

    三份手册都承诺了这道闸,而它此前只存在于文档里 —— 界面靠不渲染下载按钮遮住,
    开放 API 上一条 curl 就绕过去了。

    还断言「被拒的请求不留痕」:闸必须在 log_download 之前,否则运营分析的下载量会
    把拒绝也算成一次下载。
    """
    job = _succeed_a_run(db, spy_connector, task_api, viewer)
    since = max_audit_id(db)

    with pytest.raises(PermissionDeniedError) as ei:
        v1_routes.download_result(job.id, db, viewer, ip=None)
    assert ei.value.status_code == 403
    # 报错要能让人走下一步:说清是哪个任务、少的是哪项授权、该找谁
    assert task_api.name in ei.value.message and "「下载」授权" in ei.value.message

    assert new_audit_rows(db, since) == []
    assert db.scalar(
        select(func.count(DownloadEvent.id)).where(DownloadEvent.job_id == job.id)
    ) == 0

    # 同一个人预览照常:能跑就能看前 50 行,这是与界面一致的口径
    assert v1_routes.preview_run(job.id, db, viewer)["row_count"] == 1


def test_web_download_url_is_gated_by_the_same_rule(db, task_api, viewer, spy_connector):
    """界面那条签名 URL 通道叠的是同一道闸 —— 否则补了 API、网页仍是个洞。"""
    job = _succeed_a_run(db, spy_connector, task_api, viewer)
    with pytest.raises(PermissionDeniedError, match="「下载」授权"):
        query_service.get_download_url(db, viewer, job)


def test_can_download_job_truth_table(db, task_api, viewer, spy_connector):
    """下载闸的三种答案,一次看全。

    **核心不变式在第二条**:发起人身份不构成下载资格。can_access_job 有
    `job.user_id == user.id` 的短路(发起人当然看得见自己跑的记录),而这道闸刻意不继承它
    —— 继承了的话,任何有 run 权限的人自己跑一次就绕过去,闸直接架空。谁要是「顺手把两个
    函数对齐」,这条会红。

    第三条钉住订阅那个放行分支的边界:它只对**订阅跑出来的那些期**成立,同任务下他人
    (这里是他自己)手动跑的结果仍要 download 授权,否则订阅会变成一条绕开授权的旁路。
    (「团队外被显式授予 download」那一格由 test_preview_and_download_direct 覆盖。)
    """
    manual = _succeed_a_run(db, spy_connector, task_api, viewer)
    subscribed = _succeed_a_run(db, spy_connector, task_api, viewer)
    subscribed.source = SOURCE_SUBSCRIBE
    db.add(TaskSubscription(template_id=task_api.id, user_id=viewer.id))
    db.commit()

    assert manual.user_id == viewer.id
    assert permission_service.can_access_job(db, viewer, manual) is True     # 看得见
    assert permission_service.can_download_job(db, viewer, manual) is False  # 仍取不走
    assert permission_service.can_download_job(db, viewer, subscribed) is True


def test_expired_result_is_404_on_both_download_and_preview(
    db, task_api, author, viewer, spy_connector
):
    """结果过了保留期:下载与预览都给 **404**,不是 400。

    Agent 按状态码分流(404 → 去重跑,400 → 去改参数),这两条在 rubick-skill.md 里是
    写死的行为;给 400 会让它反复改参数重发一个永远不会再有的结果。
    """
    job = _succeed_a_run(db, spy_connector, task_api, viewer)
    job.created_at = datetime.now() - timedelta(days=settings.RESULT_RETENTION_DAYS + 1)
    db.commit()

    # 下载半边用 author(有下载权):闸在保留期之前,viewer 撞到的会是 403 而非过期
    for call in (
        lambda: v1_routes.download_result(job.id, db, author, ip=None),
        lambda: v1_routes.preview_run(job.id, db, viewer),
    ):
        with pytest.raises(ResultExpiredError) as ei:
            call()
        assert ei.value.status_code == 404
        assert "保留期" in ei.value.message


def test_preview_says_so_when_result_file_is_gone(db, task_api, viewer, spy_connector):
    """文件被手工清掉(没过保留期):预览也要说「取不到」,而不是回一张空表。

    此前预览只判保留期、不判文件在不在盘上,于是 read_csv_preview 读不到行就回
    columns=[] —— 业务方分不清「结果没了」和「这次真的一行都没查到」。
    """
    job = _succeed_a_run(db, spy_connector, task_api, viewer)
    result_service.local_path(job.result_object_key).unlink()

    with pytest.raises(ResultExpiredError):
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


def test_analytics_api_block(db, admin, task_api, viewer, clean_jobs):
    """**链路**:v1 真的跑出来的运行与下载,接得上运营分析的开放 API 板块。

    口径断言(固定 7 天、占比分母、Top 排序、团队收窄)在 tests/test_analytics_api.py ——
    那边用 job_factory 直接落行,造得出历史分布。这里刻意走**真实的 enqueue / log_download**:
    它钉的是「来源常量与通道标记确实被写进了库」,而那正是另一个文件假设成立、却验不到的一段。
    哪天 enqueue 把 source 改了名,只有这条会红。
    """
    api_job = query_service.enqueue(db, viewer, task_api.id, {"d": "2026-09-21"}, source="api")
    audit_service.log_download(
        db, user=viewer, job_id=api_job.id, filename="a.csv", row_count=1, ip=None, via="api"
    )

    scope = analytics_service.resolve_scope(db, admin)
    res = analytics_service.api_usage(db, scope, timewindow.resolve_window(days=7))
    assert res["api_runs"]["value"] == 1
    assert res["api_downloads"]["value"] == 1

"""运营分析:管理员与团队管理员看平台被用得怎么样。

全部是**只读聚合**,因此刻意不记审计、不新增动作码 —— `export_audit` 之所以要留痕,
是因为导出把数据带离了平台;而看一眼汇总数字没有这个性质。把它记下来只会把
audit_logs 撑大、淹没真正的治理动作。将来若加 CSV 导出,那时再登记 `analytics_export`。

各板块的响应刻意**不挂 response_model**,理由见 schemas/analytics.py 的模块 docstring
(平台专属指标要求「键不存在」,而 Metric 的 `value: null` 又必须留住,两者在
response_model 下不可兼得)。

权限判定只发生一次,在 analytics_service.resolve_scope 里。这里每个路由第一行都一样。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import timewindow
from app.core.database import get_db
from app.models.user import User
from app.schemas.analytics import AnalyticsMetaOut
from app.services import analytics_service

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _scoped(db: Session, user: User, team_id: int | None, start, end, days: int | None):
    """把「你能看哪一片」与「看哪一段」一次解算好。

    刻意不做成 Depends:本仓库的测试直接调路由函数,守卫写在函数体里才测得到 403
    (与 routes/credentials.py 同一个理由)。
    """
    scope = analytics_service.resolve_scope(db, user, team_id)
    window = timewindow.resolve_window(start, end, days)
    return scope, window


@router.get("/meta", response_model=AnalyticsMetaOut)
def analytics_meta(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """页面初始化:能选的范围、时间预设、每条指标的中文口径、平台开张了没有。

    口径说明由服务端下发而不是前端自己写死 —— 否则口径改了文案不改,页面上那句
    「什么算活跃」会变成一句错话,而且没人会发现。
    """
    return analytics_service.meta(db, user)


@router.get("/adoption")
def analytics_adoption(
    team_id: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    days: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """板块①:平台有没有人用、用得深不深,含开放 API 的调用与 token 活跃度。

    注意 `tokens_*` 两项是**此刻**的快照(且活跃窗口固定 7 天),不吃时间范围。
    """
    scope, window = _scoped(db, user, team_id, start, end, days)
    return analytics_service.adoption(db, scope, window)


@router.get("/health")
def analytics_health(
    team_id: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    days: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """板块②:跑得顺不顺 —— 成功率、失败归因、耗时与排队。"""
    scope, window = _scoped(db, user, team_id, start, end, days)
    return analytics_service.health(db, scope, window)


@router.get("/live")
def analytics_live(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """实时负载:槽位占用、worker 死活、在跑/排队明细、今天逐小时的压力、未来 24 小时的定时排布。

    **只对平台视角开放**(理由见 analytics_service.live)。不吃时间范围,也不收 team_id ——
    前端每 15 秒轮询一次,参数越少越不会配错。
    """
    scope = analytics_service.resolve_scope(db, user)
    return analytics_service.live(db, scope)


def run_filters(
    status: list[str] = Query(default=[]),
    source: list[str] = Query(default=[]),
    datasource_id: int | None = None,
    template_id: int | None = None,
    template_kw: str | None = None,
    user_kw: str | None = None,
    min_duration_s: int | None = None,
    min_queue_s: int | None = None,
    error_kw: str | None = None,
    bucket: str | None = None,
    executed_only: bool = False,
) -> analytics_service.RunFilters:
    """运行明细的筛选参数。status / source 可多选(`?status=failed&status=queued`)。
    做成依赖只为让每个筛选项只列一次;它不是守卫,守卫仍在路由函数体里(见 _scoped)。"""
    return analytics_service.RunFilters(
        status=tuple(status), source=tuple(source), datasource_id=datasource_id,
        template_id=template_id, template_kw=template_kw, user_kw=user_kw,
        min_duration_s=min_duration_s, min_queue_s=min_queue_s, error_kw=error_kw,
        bucket=bucket, executed_only=executed_only,
    )


@router.get("/runs")
def analytics_runs(
    team_id: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    days: int | None = None,
    filters: analytics_service.RunFilters = Depends(run_filters),
    sort: str = "created",
    page: int = 1,
    page_size: int = analytics_service.RUNS_PAGE_DEFAULT,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """运行明细:时间窗内每一次运行的流水,可筛选、分页,带错误摘要。

    排序:created(默认,新的在前)/ duration(耗时最长在前)/ queue(排队最久在前)。
    """
    scope, window = _scoped(db, user, team_id, start, end, days)
    return analytics_service.runs(db, scope, window, filters,
                                  sort=sort, page=page, page_size=page_size)


@router.get("/runs/{job_id}")
def analytics_run_detail(
    job_id: int,
    team_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """一次运行的完整错误、执行 SQL 与参数。范围外的 id 一律 404。"""
    scope = analytics_service.resolve_scope(db, user, team_id)
    return analytics_service.run_detail(db, scope, job_id)


@router.get("/assets")
def analytics_assets(
    team_id: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    days: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """板块③:任务资产在被复用还是在腐烂。

    注意响应里 `as_of` 是**此刻**的快照(任务总数、闲置数),切时间范围纹丝不动;
    只有排行吃时间窗。不在数据上分开,用户第一次切范围就会当成 bug。
    """
    scope, window = _scoped(db, user, team_id, start, end, days)
    return analytics_service.assets(db, scope, window)


@router.get("/governance")
def analytics_governance(
    team_id: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    days: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """板块④:权限膨胀与配置缺口。

    「授权了但从没跑过」是**全期**口径,不吃时间窗 —— 授权是存量事实,套时间窗会把
    三个月前用过的人误报成僵尸。
    """
    scope, window = _scoped(db, user, team_id, start, end, days)
    return analytics_service.governance(db, scope, window)


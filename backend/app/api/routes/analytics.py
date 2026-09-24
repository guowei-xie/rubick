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

from fastapi import APIRouter, Depends
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


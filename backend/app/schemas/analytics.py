"""运营分析的响应模型。

**只有静态形状的接口用 response_model**(meta / scope 选项)。四个板块刻意返回裸 dict,
原因是两条互相冲突的需求:

  · 平台专属指标(角色分布、跨团队人头…)在团队视角下必须**键直接不存在** —— 渲染成 0
    或「无权限」会让人去猜后面藏了什么,反而更想知道全平台是多少;
  · 每个指标的 `value: null` 是**有意义的值**(「这个口径算不出来」,与 0 是两回事)。

`response_model` 满足不了它们:声明成 Optional 会把缺席变成 `null`(第一条破了),
而 `response_model_exclude_none=True` 会把 Metric 里那些有意义的 null 一起抹掉(第二条破了)。
形状的真相因此放在 analytics_service 的返回值与它的 docstring 里,由 tests/test_analytics_*
逐条钉住。
"""
from __future__ import annotations

from pydantic import BaseModel


class ScopeOptionOut(BaseModel):
    """范围切换器的一个选项。`team_id=None` 是「全平台」,只会出现在平台管理员的列表里。"""

    team_id: int | None = None
    name: str


class MetricNoteOut(BaseModel):
    """一条指标的口径说明。**单一真源** —— 前端不自己维护一份中文解释。

    `windowed=False` 的指标是「此刻」的快照,切时间范围不会变;前端据此在卡片上挂
    「此刻」标记,免得用户把它当成 bug。
    """

    key: str
    label: str
    windowed: bool
    note: str


class AnalyticsMetaOut(BaseModel):
    scope_options: list[ScopeOptionOut]
    # 进来默认看哪个范围。与 scope_options 同源,**前端不自己从 /auth/me 的团队顺序里挑** ——
    # 两边挑出不同的队时,界面显示的范围与数据的范围会静默对不上
    default_team_id: int | None = None
    # 时间范围的快捷档(天)。前端据此渲染那几个按钮,不再硬编码一份
    presets: list[int]
    metric_notes: list[MetricNoteOut]
    # 平台还没被用起来时,整页换成一句「还没有运营数据」+ 下一步指引,
    # 而不是铺四屏全是「—」的骨架卡
    bootstrapped: bool
    counts: dict[str, int]

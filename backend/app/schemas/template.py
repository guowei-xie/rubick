from __future__ import annotations
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, model_validator

from app.schemas.common import ParamDef
from app.schemas.permission import SubjectIn

_AT_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class SubscriptionScheduleIn(BaseModel):
    """任务的订阅计划,随任务保存提交(TemplateCreateIn/UpdateIn.subscription)。

    不单独开配置端点:「已开订阅的任务不能有变量」是计划与参数**同一次保存内**的事务性
    约束(卡点在 template_service.add_version),拆成两个端点就会出现中间态。
    enabled=False 时其余字段仍落库(保留上次配置,再开启时不用重填)。
    """

    enabled: bool = False
    freq: Literal["daily", "weekly", "monthly"] = "daily"
    # weekly: ISO 星期几(1=周一 … 7=周日);monthly: 几号(1-31),可多选。
    # 29/30/31 遇小月顺延到月末最后一天(见 subscription_service.latest_planned_at)
    days: list[int] = []
    at_time: str = "09:00"  # "HH:MM",服务器本地时间

    @model_validator(mode="after")
    def _check(self) -> "SubscriptionScheduleIn":
        if not _AT_TIME_RE.fullmatch(self.at_time):
            raise ValueError("运行时刻格式应为 HH:MM(24 小时制)")
        self.days = sorted(set(self.days))
        if self.enabled:
            if self.freq == "weekly" and (
                not self.days or any(d < 1 or d > 7 for d in self.days)
            ):
                raise ValueError("每周计划需在周一~周日(1~7)中至少选一天")
            if self.freq == "monthly" and (
                not self.days or any(d < 1 or d > 31 for d in self.days)
            ):
                raise ValueError("每月计划需在 1~31 号中至少选一天")
        return self


class SubscriptionScheduleOut(BaseModel):
    """订阅计划回显(任务详情;编辑器据此回填表单)。"""

    enabled: bool = False
    freq: str = "daily"
    days: list[int] = []
    at_time: str = "09:00"

    class Config:
        from_attributes = True


class SubscriberOut(BaseModel):
    """订阅者名单一行(开发者/团队管理员视角)。"""

    user_id: int
    name: str | None = None
    avatar: str | None = None
    miss_streak: int = 0  # 连续未消费的成功期数;临近阈值时前端标橙
    created_at: datetime | None = None  # 订阅时间
    # 代订阅的操作者;两项同时为空 = 本人自助订阅
    added_by: int | None = None
    added_by_name: str | None = None


class SubscribersOut(BaseModel):
    threshold: int  # 自动退订阈值(settings.SUBSCRIPTION_MISS_LIMIT),前端展示说明用
    items: list[SubscriberOut] = []


#: 一次代订阅的人数上限。比批量交接的 200 小一个量级:那边是在已经列出来的任务表里勾选,
#: 这边是一个个把人搜出来挑,50 远高于真实量级;且每个目标都要算一次 team_scope(两次查询)。
SUBSCRIBE_FOR_LIMIT = 50


class SubscribeForIn(BaseModel):
    """代订阅入参:一个任务 × 一批人。主体形状与授权共用 SubjectIn(含那条邮箱禁令)。"""

    subjects: list[SubjectIn]


class SubscribeForOut(BaseModel):
    """代订阅结果。三份 id 清单而不是一份名单:前端拿到后重拉 /subscribers 刷新表格,
    这里只回答「这次发生了什么」,免得名单的行形状要在两个地方各维护一份。"""

    created: list[int] = []        # 本次真新建的订阅
    skipped: list[int] = []        # 本来就在名单里,原样不动(幂等,不算失败)
    granted_view: list[int] = []   # 本次顺带补了查看权的人


class SubscriptionEventOut(BaseModel):
    """订阅/退订留痕一行。action_label 由后端按 SUB_EVENT_META 译好,前端不维护枚举。"""

    id: int
    user_id: int
    user_name: str | None = None
    action: str
    action_label: str
    operator_id: int | None = None
    operator_name: str | None = None
    detail: dict | None = None
    created_at: datetime | None = None


class ValueListOut(BaseModel):
    values: list[str]
    truncated: bool = False
    duration_ms: int | None = None  # 获取枚举耗时(毫秒),作者测试时捕获,供前端参考


class EnumSampleIn(ValueListOut):
    """作者在编辑器里测出来的一批候选值,随任务保存落库、成为业务侧共享候选。

    source_sql 是「测这批值时用的那段 SQL」:作者可能测完又改了 enum_sql 才保存,
    后端只在它与真正落库的 enum_sql 一致时才采纳(见 enum_cache_service.sync_params)。
    """

    values: list[str] = []  # 收窄:作者可能没测过,允许缺省为空
    source_sql: str = ""


class SharedEnumValuesOut(ValueListOut):
    """业务侧读到的共享候选值。字段是 ValueListOut 的严格超集。"""

    cached: bool = False  # 是否有可用的共享候选(过期视为无)
    stale: bool = False  # 作者改了 enum_sql / 数据源,旧候选已作废,需要重新获取
    reused: bool = False  # 刚刚有人更新过,本次直接复用,没有真跑 SQL
    updated_at: datetime | None = None
    updated_by: int | None = None
    updated_by_name: str | None = None


class EnumRefreshIn(BaseModel):
    variable: str


class TemplateCreateIn(BaseModel):
    name: str
    description: str | None = None
    tags: list[str] = []
    # 所属团队,**必填**:它决定任务的可见范围与取数身份(团队账号)。
    # 开发者只能选自己所属的团队;平台管理员可选任意团队(见 permission_service
    # .require_can_create_in_team)。「开发者必须先有团队才能建任务」就落在那里。
    team_id: int
    datasource_id: int
    sql_text: str
    params: list[ParamDef] = []
    timeout_seconds: int | None = None  # 查询超时(秒);留空=按引擎默认
    # 允许开放 API 触发该任务(运行闸;默认关,见 models/template.py 的 allow_api 注释)
    allow_api: bool = False
    # 作者测出来的候选值,按变量名归集。**缺省 ≠ 清空**:编辑器每次开窗都清空测试结果,
    # 所以「只改任务名、没重测」发来的就是空 dict,此时必须保留已有的共享候选。
    enum_samples: dict[str, EnumSampleIn] = {}
    # 订阅计划;None = 本次保存未携带订阅配置,维持库里现状(与 enum_samples 同一「缺省 ≠ 清空」约定)
    subscription: SubscriptionScheduleIn | None = None


class TemplateUpdateIn(BaseModel):
    """更新会生成一个新版本。原任务已上线时新版本自动接替上线(见
    template_service.add_version);草稿/已下线则维持原状态。

    刻意**不接受 team_id**:转移团队会同时改变可见范围与取数身份,是一次跨组织的治理动作,
    只有平台管理员能做,走独立端点 PUT /tasks/{id}/team(独立审计码 + 连带撤销编辑权)。
    """

    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    datasource_id: int | None = None
    sql_text: str | None = None
    params: list[ParamDef] | None = None
    timeout_seconds: int | None = None  # 查询超时(秒);留空=按引擎默认
    # None = 本次保存未动这个开关(与 name 等字段同一约定);显式 true/false 才改
    allow_api: bool | None = None
    # 同 TemplateCreateIn.enum_samples:缺省/空 dict 表示「本次没有新测的候选」,不是「清空」
    enum_samples: dict[str, EnumSampleIn] | None = None
    # 订阅计划;None = 维持现状。显式 enabled=False 视为「关闭订阅」(清退订阅者并通知)
    subscription: SubscriptionScheduleIn | None = None


class TestRunIn(BaseModel):
    """作者自检试跑:直接给 SQL + 参数,返回样例行。

    带 template_id(试跑已保存的任务)时会落一条 source=test 的运行记录并存结果文件,
    以便在「运行记录」里预览/导出;不带则只返回样例行、不留痕。两种情况都不发通知。
    """

    # 用哪个团队的取数账号试跑。**必填**:少了它就只能猜,而「猜」等于让人借任意团队的
    # 账号跑任意 SQL。操作者必须是该团队成员(credential_service.for_team 会校验)。
    team_id: int
    datasource_id: int
    sql_text: str
    params: list[ParamDef] = []
    values: dict[str, Any] = {}
    # 试跑取样行数。默认与前端编辑器实际发的值、以及两份手册写的「最多取 50 行」对齐 ——
    # 这里曾默认 100,全靠前端显式传 50 才没走样。配了 MAX_RESULT_ROWS 时不越过它
    # (默认不限,那就按这里的数取;见 template_service.test_run)。
    limit: int = 50
    # 关联到某个已存在任务时,试跑会落一条 source=test 的运行记录;新建未保存任务时为空,不留痕
    template_id: int | None = None


class PreviewSqlIn(BaseModel):
    """SQL 预览:只渲染不执行,代入当前填的测试值,未填变量原样保留 :x。"""

    sql_text: str
    params: list[ParamDef] = []
    values: dict[str, Any] = {}


class PreviewSqlOut(BaseModel):
    rendered_sql: str


class EnumSqlIn(BaseModel):
    """作者在任务编辑器里测试「枚举值获取 SQL」。"""

    team_id: int  # 同 TestRunIn.team_id:用哪个团队的账号跑,必填且要校验成员资格
    datasource_id: int
    sql: str


class PublishIn(BaseModel):
    note: str | None = None


class TemplateVersionOut(BaseModel):
    id: int
    version_no: int
    sql_text: str
    params: list[ParamDef]
    author_id: int

    class Config:
        from_attributes = True


class TemplateOut(BaseModel):
    id: int
    name: str
    description: str | None
    tags: list[str]
    datasource_id: int
    team_id: int | None = None
    team_name: str | None = None
    status: str
    author_id: int
    published_version_id: int | None
    timeout_seconds: int | None = None
    # 是否允许开放 API 触发(运行闸)。前端任务列表据此透出「允许 API」标记
    allow_api: bool = False

    class Config:
        from_attributes = True


class TemplateDetailOut(TemplateOut):
    published_version: TemplateVersionOut | None = None
    latest_version: TemplateVersionOut | None = None
    # 订阅计划回显与在册订阅人数(编辑器回填表单 + 「有订阅者」预警)
    subscription: SubscriptionScheduleOut | None = None
    subscriber_count: int = 0

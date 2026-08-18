from __future__ import annotations
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.schemas.common import ParamDef


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
    # 作者测出来的候选值,按变量名归集。**缺省 ≠ 清空**:编辑器每次开窗都清空测试结果,
    # 所以「只改任务名、没重测」发来的就是空 dict,此时必须保留已有的共享候选。
    enum_samples: dict[str, EnumSampleIn] = {}


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
    # 同 TemplateCreateIn.enum_samples:缺省/空 dict 表示「本次没有新测的候选」,不是「清空」
    enum_samples: dict[str, EnumSampleIn] | None = None


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
    limit: int = 100
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

    class Config:
        from_attributes = True


class TemplateDetailOut(TemplateOut):
    published_version: TemplateVersionOut | None = None
    latest_version: TemplateVersionOut | None = None

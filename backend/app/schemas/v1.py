"""开放 API v1 的对外模型。

v1 是**稳定对外契约**(调用方是脚本与 AI Agent,不在本仓库里):字段只增、不改、不删;
要变形状就升 v2,别在 v1 上做「顺手重构」。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from app.schemas.common import ParamDef


class V1RunIn(BaseModel):
    """触发运行的入参。template_id 在路径里,这里只带参数值(键 = 变量名)。"""

    values: dict[str, Any] = {}


class V1ParamOut(BaseModel):
    """对外的参数定义 —— 内部 ParamDef 的**投影**,字段以 docs/open-api.md 3.1 为准。

    **不复用 ParamDef**:那是编辑器与执行器的内部模型,会随 SQL 写法判定、批量输入控件、
    耗时采样这些内部需求长字段;直接外抛等于让每一次内部加字段都成为一次对外契约变更。
    代价已经付过一次 —— 它把 enum_sql(取候选值的 SQL 原文,属团队内部信息)、test_value、
    allow_bulk_input、enum_sql_duration_ms 一并发给了纯业务身份的 token。
    这四个字段从未出现在任何文档里,是**未文档化的泄漏而非契约字段**,故这次删掉它们
    不违反本模块开头「只增不删」那条约定。

    投影必须吃**已归一的 ParamDef** 而不是落库的 dict:旧 shape(type=multi_enum)的归一
    住在 ParamDef._from_legacy,直接读 dict 会把历史任务的 kind 读成 single。
    """

    name: str
    kind: Literal["single", "list"] = "single"
    value_type: Literal["text", "number"] = "text"
    label: str | None = None
    #: 共享候选值(只有配了枚举 SQL 的 list 参数才有)。未采集过、或作者改了 SQL / 换了
    #: 数据源使旧候选作废时,一律空数组 —— 对调用方是同一个下一步:帮不了你选,去问用户。
    #: 候选之外的值本来就允许提交,所以空候选不等于「没有合法值」。
    enum: list[str] = []

    @classmethod
    def of(cls, p: ParamDef, enum: list[str]) -> "V1ParamOut":
        return cls(
            name=p.name, kind=p.kind, value_type=p.value_type, label=p.label, enum=enum
        )


class V1JobOut(BaseModel):
    """对外的运行记录 —— 内部 JobOut 的**投影**,字段以 docs/open-api.md 4.1 为准。

    内部 JobOut(schemas/query.py)带着十多个字段,其中三个绝不能进对外契约:
      executed_sql —— 最终发给数据库的 SQL 原文,团队内部信息;
      params       —— 本次运行的入参原文(客户名 / 手机号这类业务维度值),而
                      GET /runs 会把团队内**他人**发起的运行一并列给一枚 token;
      user_name    —— 他人姓名。
    另外剔除的是有第二真相源或纯内部观测的:template_name(可重名,定位一律用
    template_id)、result_filename(下载响应的 Content-Disposition 已经带了)、
    queue_ms(内部排队观测;duration_ms 的说明里已交代「不含排队」)。

    **投影靠属性名读值**:内部字段改名不会报错,只会让这里静默变 null。
    test_v1_api 里有一条 `set(V1JobOut.model_fields) <= set(JobOut.model_fields)`
    的断言专门兜这个。
    """

    id: int
    template_id: int
    status: str
    row_count: int | None = None
    duration_ms: int | None = None
    error: str | None = None
    #: 排在前面还有几个;**仅 status=queued 时有值**(口径同 JobOut,由 routes.query.job_out 现算)
    queue_ahead: int | None = None
    source: str = "run"
    created_at: datetime | None = None
    started_at: datetime | None = None
    #: 结果是否已过保留期。文档 4.1 之外多给的一项:没有它,调用方从 /runs 里挑一条
    #: 历史运行去下载,只能先吃一个 404 才知道结果已经被清理了。
    result_expired: bool = False

    class Config:
        from_attributes = True


class V1ReusableOut(BaseModel):
    """「今天有没有同参的现成结果」的回答:有就是那条运行,没有为 null。

    包一层而不是直接回 job 或 404:「没有」是正常答案、不是错误,而 404 在 v1 里
    已经表示「任务不可见」与「结果过期」,再叠一层含义调用方就分不清了。
    """

    job: V1JobOut | None = None


class V1TaskOut(BaseModel):
    """任务列表的一行:身份与描述 + 填参所需的参数定义 + 当前 token 主人的能力位。

    参数定义取自**当前已上线版本**(业务调用方填参的依据);未上线的任务 params 为空,
    它们会出现在列表里只是因为调用者对团队可见(内部人看得到草稿),can_run 为 False。
    """

    id: int
    name: str
    description: str | None = None
    status: str
    team_id: int | None = None
    team_name: str | None = None
    # 「允许 API 调用」开关(运行闸):False 时 POST /tasks/{id}/runs 一律 403
    allow_api: bool = False
    # 当前 token 的主人能否运行 / 能否下载结果(口径 = permission_service,与界面一致)
    can_run: bool = False
    can_download: bool = False
    params: list[V1ParamOut] = []

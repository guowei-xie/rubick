from __future__ import annotations
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class RunIn(BaseModel):
    template_id: int
    values: dict[str, Any] = {}


class JobOut(BaseModel):
    id: int
    template_id: int
    template_name: str | None = None
    user_name: str | None = None
    params: dict[str, Any] = {}
    status: str
    source: str = "run"  # run=正式取数,test=试跑,subscribe=订阅定时运行
    row_count: int | None = None
    duration_ms: int | None = None
    error: str | None = None
    result_filename: str | None = None
    result_expired: bool = False
    executed_sql: str | None = None  # 实际发给数据库的最终 SQL
    # 排在前面还有几个 queued 任务。没有它,前端就只能对「在排队」和「在跑」说同一句话,
    # 长等待读起来像卡死。
    # 不是模型上的列,由路由现算(见 routes/query._job_out),所以**只有 POST /run 与
    # GET /jobs/{id} 的响应里有值,且仅当 status=queued**;列表接口 GET /jobs 直接把 ORM
    # 行喂给 response_model,这一项恒为 null —— 别在运行记录列表里指望它。
    queue_ahead: int | None = None
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class AuthorizedUserOut(BaseModel):
    """被授权运行某任务的用户(用于任务卡片的参与者头像)。"""

    id: int
    name: str
    avatar: str | None = None


class TaskOut(BaseModel):
    """统一任务列表的一行(项目/模板 + 展示与能力标记)。"""

    id: int
    name: str
    description: str | None = None
    status: str
    datasource_id: int
    datasource_name: str | None = None
    engine: str | None = None
    author_id: int
    author_name: str | None = None
    # 所属团队:任务的可见性边界与取数身份来源。存量迁移后恒有值,但 DB 侧可空,故这里也可空
    team_id: int | None = None
    team_name: str | None = None
    published_version_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None  # 最后编辑时间
    last_run_at: datetime | None = None  # 最后一次运行时间(含试跑);无则为空
    # ---- 闲置(长期没人运行,可以考虑下线)----
    # 距今多少天没运行过(口径同 last_run_at,含试跑与订阅定时运行)。从未运行过的按
    # created_at 起算 —— 「上线至今没人跑过」正是最该被看见的一种,给它 null 等于把它藏起来。
    # **非已上线任务恒为 None**:草稿/已下线不参与判定(见 template_service.idle_days)。
    idle_days: int | None = None
    # 是否已超过阈值。阈值比较**在服务端做一次**,与 can_run / credential_ready 同一约定;
    # 否则「算不算闲置」会在卡片、列表、排序、顶栏筛选片四处各算一遍,改阈值时漏一处就对不上数。
    is_idle: bool = False
    # 判定用的阈值(settings.TASK_IDLE_DAYS;0 = 这项提示关着)。只供前端拼一句
    # 「超过 90 天没有运行记录」的悬停解释 —— 被标出来的人得能看到「为什么是我」。
    # 每行重复同一个数确实冗余(不同于 SubscribersOut.threshold —— 那是包在一个对象里
    # 只出现一次,本接口返回的是裸 list),但另两条路更贵:为一句悬停文案多开一个配置接口,
    # 或让前端硬编码 90(那就成了「前端自己推导规则」,正是本 schema 一直在避免的)。
    idle_threshold_days: int = 0
    timeout_seconds: int | None = None  # 该任务查询超时(秒);None=按引擎默认
    # 可编辑/授权/下线。四条口径见 permission_service.can_edit(平台管理员 / 该团队的团队管理员 /
    # 仍在团队内的作者 / 被授予该任务编辑权的成员)。前端只消费这个布尔,**不要自己算团队规则**
    can_manage: bool = False
    # 能不能**只读查看**任务详情(SQL 原文 / 变量配置 / 订阅计划)。口径 = permission_service.is_insider:
    # 平台管理员或该任务所属团队的成员 —— 与 GET /templates/{id} 只对 insider 返回 latest_version
    # 的分级同源,故前端据它给出「查看」入口不会看到接口不肯给的东西。业务使用者不给:
    # 他们只该看到已上线的那一面。
    can_view_detail: bool = False
    # 「我开发的」:我建的,或被授予该任务编辑权的 —— 见 permission_service.is_author_or_grantee
    # (不含管理员的治理权限,故它 ≠ can_manage)
    developed_by_me: bool = False
    # 能不能把这个任务的作者转给别人(离职交接)。⊆ can_manage:口径 = can_manage **减去**
    # 「被授予该任务编辑权」那一条(见 permission_service.can_transfer_author)。
    # 前端只消费这个布尔,**不要**用 `can_manage && ...` 自己推 —— 那正好会把被授予
    # edit 的人放进来,而处分归属恰恰是不该给他们的那一项
    can_transfer_author: bool = False
    # 可填参取数(已发布且有运行权限)。注意它只表达「授权够不够」,
    # 跑得起来还要 credential_ready —— 那是别人的配置,不属于本人的权限
    can_run: bool = False
    # 任务所属**团队**在该任务数据源上**登记过取数账号吗**。false ⇒ 连都没得连,该任务跑不动,
    # 得团队管理员去登记。不看「测通没」——测试连接是非必选项(见 services/credential_service)。
    # 只给 can_manage 的人展示告警(业务用户看了也修不了)。
    # 默认 False:漏算时宁可多一个告警,也不要静默宣称「就绪」
    credential_ready: bool = False
    authorized_users: list[AuthorizedUserOut] = []  # 显式授权可运行的用户(卡片参与者头像)
    # ---- 订阅(定时自动运行)----
    subscribe_enabled: bool = False  # 该任务开启了订阅计划
    # 计划的中文描述(如「每周一、四 09:00」),后端拼好(subscription_service.describe_schedule),
    # 前端直接展示 —— 频次语义只表述一次
    schedule_desc: str | None = None
    subscribed: bool = False  # 当前用户已订阅
    subscriber_count: int = 0
    # 当前用户能不能订阅(can_view + 已上线 + 计划开启)。与 can_manage 同一约定:
    # 前端只消费布尔位,不自己算规则
    can_subscribe: bool = False

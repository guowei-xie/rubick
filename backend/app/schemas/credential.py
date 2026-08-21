"""团队取数账号的 I/O 模型。

铁律一:**没有任何 Out 模型带 password**。凭证只写不读,与 DataSourceOut 同一约定
(见 schemas/datasource.py)。平台管理员也只看得到状态,拿不到密码。

铁律二:`username` 是**半机密**,不是普通展示字段。Hive 在 auth=NONE 下用户名本身就是
完整凭证 —— 拿到它就能用本地客户端直连、绕过平台的审计与行数上限。故它只对该团队的
团队管理员、平台管理员可见;普通团队成员拿到的是 None(由服务层的 reveal_username 控制,
见 credential_service._cell)。任务编辑器走的 /credentials/my-teams 恒不含它。
"""
from __future__ import annotations
from datetime import datetime

from pydantic import BaseModel

from app.schemas.team import TeamMemberOut


class CredentialIn(BaseModel):
    """登记/修改某团队在某数据源上的取数账号。password 留空表示保留原密码。"""

    username: str
    password: str | None = None


class CredentialCellOut(BaseModel):
    """某团队在某数据源上的**状态**(不含数据源本身的信息)。"""

    datasource_id: int
    configured: bool
    username: str | None = None  # 仅团队管理员 / 平台管理员可见,否则恒为 None
    verified: bool = False  # 最近一次连接测试是否通过。纯提示,不影响能否上线/运行
    last_verified_at: datetime | None = None
    last_verify_error: str | None = None
    # 上次是谁改的。团队账号是共享的,而团队管理员读不到审计日志(/audit 是 require_admin),
    # 所以这条治理事实必须由业务接口给出
    updated_by: int | None = None
    updated_by_name: str | None = None
    updated_at: datetime | None = None


class TeamCredentialStatusOut(CredentialCellOut):
    """团队取数账号页的一行:状态 + 数据源信息(该页要显示 host:port/db)。

    平台管理员总览不用这个而用瘦的 CredentialCellOut:那里数据源信息已经在
    overview.datasources 里给过一份,每格再抄一遍纯属浪费带宽。
    """

    team_id: int
    team_name: str | None = None
    datasource_name: str
    engine: str
    host: str | None = None
    port: int | None = None
    database: str | None = None


class CredentialVerifyOut(TeamCredentialStatusOut):
    """「测试连接」的响应:状态行 + 这个账号能访问的库。

    单独一个类而不是给状态行加个字段:库列表不落库,只有这一个端点产得出
    (见 services/credential_service.probe),挂在共享的状态行上会让另外两个列表端点
    背一个恒为空的字段。
    """

    databases: list[str] = []


class DataSourceBriefOut(BaseModel):
    id: int
    name: str
    engine: str


class TeamCredentialsOut(BaseModel):
    """平台管理员总览里的一行:某个团队在各数据源上的配置情况。"""

    team_id: int
    team_name: str
    member_count: int = 0
    admins: list[TeamMemberOut] = []
    credentials: list[CredentialCellOut]


class NotReadyTemplateOut(BaseModel):
    """已上线但**压根没有取数账号**的任务 —— 此刻就跑不动,需要该团队去登记账号。

    「已配置但未测通」不在此列:测试连接是非必选项,那种账号照样能跑
    (见 services/credential_service 模块 docstring)。"""

    template_id: int
    template_name: str
    team_id: int | None = None
    team_name: str | None = None
    author_id: int
    author_name: str | None = None
    datasource_id: int
    datasource_name: str | None = None
    reason: str  # 未配置 / 无所属团队


class CredentialOverviewOut(BaseModel):
    """平台管理员的常态健康看板。

    刻意**不再有 enforced 字段**:强制使用团队账号已是唯一路径,过渡开关
    REQUIRE_OWNER_CREDENTIAL 已删除。not_ready_templates 非空即意味着那些已上线任务
    此刻跑不动 —— 只因为**没有账号**,不因为「没点过测试连接」。
    """

    datasources: list[DataSourceBriefOut]
    teams: list[TeamCredentialsOut]
    not_ready_templates: list[NotReadyTemplateOut]

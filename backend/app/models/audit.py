"""审计日志(append-only)与下载事件。核心合规能力。

本模块同时是**动作码字典的单一事实来源**:新增可审计动作必须在 ACTION_META 登记,
否则 tests/test_audit_coverage.py 会失败,且管理端筛选下拉会漏掉该动作。
"""
from __future__ import annotations
from typing import Optional
from sqlalchemy import BigInteger, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, tbl
from app.core.db_types import BigIntPk
from app.models.mixins import TimestampMixin
from app.models.permission import RESOURCE_TEMPLATE

# ---- 可审计动作码 ----
# 存量动作码(勿改名:历史行会失去可筛选性,导出 CSV 也会对不上)
ACTION_LOGIN = "login"
ACTION_SUBMIT_QUERY = "submit_query"
ACTION_RUN_QUERY = "run_query"
ACTION_RUN_QUERY_FAILED = "run_query_failed"
ACTION_DOWNLOAD = "download"
ACTION_EXPORT_AUDIT = "export_audit"

# 新增动作码统一 <域>_<动词>
# 任务生命周期(「任务」= SqlTemplate;回收站 = status archived)
ACTION_TASK_CREATE = "task_create"
ACTION_TASK_UPDATE = "task_update"
ACTION_TASK_PUBLISH = "task_publish"
ACTION_TASK_ARCHIVE = "task_archive"
ACTION_TASK_RESTORE = "task_restore"
# 业务用户手动更新某个值列表变量的共享候选值(会改动该任务所有人看到的候选)
ACTION_TASK_ENUM_REFRESH = "task_enum_refresh"

# 任务转移团队:改的是任务的可见范围与取数身份,治理上是大事,单独一个码
ACTION_TASK_TEAM_TRANSFER = "task_team_transfer"

# 任务授权(业务侧 view/run/download)
ACTION_PERMISSION_GRANT = "permission_grant"
ACTION_PERMISSION_REVOKE = "permission_revoke"
# 指定任务的编辑权(团队内点对点授权)。与业务授权分开记:受众、入口、守卫都不同
ACTION_TASK_EDIT_GRANT = "task_edit_grant"
ACTION_TASK_EDIT_REVOKE = "task_edit_revoke"

# 团队治理(平台管理员建团队/指定团队管理员;团队管理员增删成员)
ACTION_TEAM_CREATE = "team_create"
ACTION_TEAM_UPDATE = "team_update"
ACTION_TEAM_DELETE = "team_delete"
ACTION_TEAM_MEMBER_ADD = "team_member_add"
ACTION_TEAM_MEMBER_REMOVE = "team_member_remove"
ACTION_TEAM_ADMIN_GRANT = "team_admin_grant"
ACTION_TEAM_ADMIN_REVOKE = "team_admin_revoke"

# 平台角色授权
ACTION_USER_ROLE_CHANGE = "user_role_change"

# 数据源配置
ACTION_DATASOURCE_CREATE = "datasource_create"
ACTION_DATASOURCE_UPDATE = "datasource_update"
ACTION_DATASOURCE_DELETE = "datasource_delete"

# 团队取数账号(某团队在某数据源上的库身份)。动作码沿用早期名字(语义未变,只是主体
# 从人变成了团队),密码永不进 detail,只记「哪个团队在哪个源上做了什么」
ACTION_CREDENTIAL_UPSERT = "credential_upsert"
ACTION_CREDENTIAL_VERIFY = "credential_verify"
ACTION_CREDENTIAL_DELETE = "credential_delete"

# 动作分组:仅用于前端着色与「详情」渲染分支,前端不再自己维护一份动作枚举
GROUP_AUTH = "auth"
GROUP_QUERY = "query"
GROUP_TASK = "task"
GROUP_PERMISSION = "permission"
GROUP_TEAM = "team"
GROUP_ADMIN = "admin"
GROUP_AUDIT = "audit"

GROUPS = {
    GROUP_AUTH, GROUP_QUERY, GROUP_TASK, GROUP_PERMISSION,
    GROUP_TEAM, GROUP_ADMIN, GROUP_AUDIT,
}

# 动作码 → (中文标签, 分组)。顺序即管理端下拉的展示顺序(按域归拢,不按字母)
ACTION_META: dict[str, tuple[str, str]] = {
    ACTION_LOGIN: ("登录", GROUP_AUTH),
    ACTION_SUBMIT_QUERY: ("提交取数", GROUP_QUERY),
    ACTION_RUN_QUERY: ("运行取数", GROUP_QUERY),
    ACTION_RUN_QUERY_FAILED: ("取数失败", GROUP_QUERY),
    ACTION_DOWNLOAD: ("下载结果", GROUP_QUERY),
    ACTION_TASK_CREATE: ("新建任务", GROUP_TASK),
    ACTION_TASK_UPDATE: ("编辑任务", GROUP_TASK),
    ACTION_TASK_PUBLISH: ("任务上线", GROUP_TASK),
    ACTION_TASK_ARCHIVE: ("任务下线(进回收站)", GROUP_TASK),
    ACTION_TASK_RESTORE: ("任务从回收站恢复", GROUP_TASK),
    ACTION_TASK_ENUM_REFRESH: ("更新枚举候选值", GROUP_TASK),
    ACTION_TASK_TEAM_TRANSFER: ("转移任务所属团队", GROUP_TASK),
    ACTION_PERMISSION_GRANT: ("授予任务权限", GROUP_PERMISSION),
    ACTION_PERMISSION_REVOKE: ("撤销任务权限", GROUP_PERMISSION),
    ACTION_TASK_EDIT_GRANT: ("授予任务编辑权", GROUP_PERMISSION),
    ACTION_TASK_EDIT_REVOKE: ("撤销任务编辑权", GROUP_PERMISSION),
    ACTION_TEAM_CREATE: ("新建团队", GROUP_TEAM),
    ACTION_TEAM_UPDATE: ("编辑团队", GROUP_TEAM),
    ACTION_TEAM_DELETE: ("删除团队", GROUP_TEAM),
    ACTION_TEAM_MEMBER_ADD: ("添加团队成员", GROUP_TEAM),
    ACTION_TEAM_MEMBER_REMOVE: ("移除团队成员", GROUP_TEAM),
    ACTION_TEAM_ADMIN_GRANT: ("指定团队管理员", GROUP_TEAM),
    ACTION_TEAM_ADMIN_REVOKE: ("取消团队管理员", GROUP_TEAM),
    ACTION_USER_ROLE_CHANGE: ("修改平台角色", GROUP_ADMIN),
    ACTION_DATASOURCE_CREATE: ("新建数据源", GROUP_ADMIN),
    ACTION_DATASOURCE_UPDATE: ("编辑数据源", GROUP_ADMIN),
    ACTION_DATASOURCE_DELETE: ("删除数据源", GROUP_ADMIN),
    ACTION_CREDENTIAL_UPSERT: ("配置团队取数账号", GROUP_TEAM),
    ACTION_CREDENTIAL_VERIFY: ("测试团队取数账号", GROUP_TEAM),
    ACTION_CREDENTIAL_DELETE: ("删除团队取数账号", GROUP_TEAM),
    ACTION_EXPORT_AUDIT: ("导出审计日志", GROUP_AUDIT),
}

# ---- 资源类型 ----
# template 复用 models.permission.RESOURCE_TEMPLATE,此处只补审计特有的几种
RESOURCE_JOB = "job"
RESOURCE_DATASOURCE = "datasource"
RESOURCE_USER = "user"
RESOURCE_AUDIT_LOG = "audit_log"
RESOURCE_TEAM = "team"
# 团队取数账号:资源 id 记**数据源** id(凭证行 id 对治理无意义,「哪个库」才是要筛的维度);
# 「哪个团队」放 detail —— resource_id 只有一个格子,而这里有两个维度
RESOURCE_CREDENTIAL = "credential"

RESOURCE_META: dict[str, str] = {
    RESOURCE_TEMPLATE: "任务",
    RESOURCE_JOB: "运行记录",
    RESOURCE_DATASOURCE: "数据源",
    RESOURCE_USER: "用户",
    RESOURCE_TEAM: "团队",
    RESOURCE_CREDENTIAL: "团队取数账号",
    RESOURCE_AUDIT_LOG: "审计日志",
}


def action_label(code: str) -> str:
    """动作码 → 中文标签。未登记的历史码退化成原码,展示/导出都不至于空白。"""
    return ACTION_META.get(code, (code, ""))[0]


class AuditLog(Base, TimestampMixin):
    """一次可审计动作:谁、何时、干了什么、命中哪个资源。应用层仅追加写(不更新不删除);
    DB 层未强制不可篡改。"""

    __tablename__ = tbl("audit_logs")
    # 管理端按时间范围检索 + 分页 COUNT 都打在 created_at 上;这是全库写入量最大的表,
    # 没有索引会退化成全表扫描。TimestampMixin 是共享的,故只在本表单独建索引。
    __table_args__ = (Index(f"ix_{tbl('audit_logs')}_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True, nullable=True)
    user_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)  # 取值见 ACTION_META
    resource_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    resource_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # 资源名称快照:任务改名或数据源删除后,日志仍然可读(仅展示用,不参与筛选)
    resource_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # 详情:模板版本、参数、行数、数据源等
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class DownloadEvent(Base, TimestampMixin):
    """结果下载事件,单列以便高频检索与配额统计。"""

    __tablename__ = tbl("download_events")

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    job_id: Mapped[int] = mapped_column(BigInteger, index=True)
    filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    row_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

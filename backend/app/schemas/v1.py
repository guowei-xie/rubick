"""开放 API v1 的对外模型。

v1 是**稳定对外契约**(调用方是脚本与 AI Agent,不在本仓库里):字段只增、不改、不删;
要变形状就升 v2,别在 v1 上做「顺手重构」。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.schemas.common import ParamDef


class V1RunIn(BaseModel):
    """触发运行的入参。template_id 在路径里,这里只带参数值(键 = 变量名)。"""

    values: dict[str, Any] = {}


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
    params: list[ParamDef] = []

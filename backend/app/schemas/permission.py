from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, model_validator


class GrantIn(BaseModel):
    subject_type: Literal["user"] = "user"  # 仅支持对个人授权
    # 授权对象二选一:已知平台用户传 subject_id;通讯录搜出的新人传 subject_open_id + 展示资料,
    # 由服务端在「真正授权」这一刻才按 open_id 落库(壳用户不再在搜索时生成,避免用户表膨胀)。
    subject_id: str | None = None
    subject_open_id: str | None = None
    subject_name: str | None = None
    subject_email: str | None = None
    subject_avatar: str | None = None
    resource_type: str = "template"
    resource_id: str
    # **必须是枚举而非 list[str]**:自由字符串会让任何能对某任务授权的人塞一个 "edit" 进来,
    # 给自己或别人开出编辑权 —— 那是一条提权后门。任务编辑权只走 /api/tasks/{id}/editors
    # (团队管理员守卫 + 专用审计码),见 models/permission.py 的 BUSINESS_ACTIONS。
    actions: list[Literal["view", "run", "download"]] = ["view", "run", "download"]

    @model_validator(mode="after")
    def _require_subject(self) -> "GrantIn":
        if not self.subject_id and not self.subject_open_id:
            raise ValueError("subject_id 或 subject_open_id 至少提供一个")
        return self


class PermissionOut(BaseModel):
    id: int
    subject_type: str
    subject_id: str
    subject_name: str | None = None
    resource_type: str
    resource_id: str
    action: str

    class Config:
        from_attributes = True

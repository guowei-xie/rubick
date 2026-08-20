"""统一业务异常。由 main.py 注册全局处理器转成 JSON。"""
from __future__ import annotations


class RubicError(Exception):
    status_code = 400

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code


class NotFoundError(RubicError):
    status_code = 404


class PermissionDeniedError(RubicError):
    status_code = 403


class UnauthorizedError(RubicError):
    status_code = 401


class CredentialRequiredError(RubicError):
    """任务所属团队还没登记取数账号(见 services/credential_service)。

    409 而非 403:请求者本身有权限,是被依赖的一项配置还没就绪,补齐后重试即可成功。
    「你不是这个团队的成员」那种是真的没权限,走 PermissionDeniedError(403)。
    """

    status_code = 409


class SqlSafetyError(RubicError):
    """SQL 安全网关拒绝。"""

    status_code = 422

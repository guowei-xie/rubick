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


class ResultExpiredError(NotFoundError):
    """这次运行的结果文件已经取不到了:过了保留期被自动清理,或已不在盘上。

    **404 而非 400**:对调用方来说这就是「这份结果不存在了」。400 的读法是「你请求写错了」,
    而这里请求完全正确,只是东西没了 —— Agent 据 400 会去改参数重发,据 404 才会去重跑取数
    (docs/open-api.md 第 5 章的错误码表一直写的就是 404)。

    文案在这里写一次,下载与预览共用 —— 两边的下一步本来就都是「重跑」,而把整句抄两遍的
    代价是改保留期文案时漏掉一处,漏掉的那处正是对外契约。也**不说死是过期**:
    判据(result_service.is_gone)还含「文件不在盘上」,第 1 天被误删的结果收到一句
    「超过 7 天」是假话。
    """

    def __init__(self):
        # 延迟导入:本模块是无依赖的叶子模块,不能在导入期把 config 拽进来
        from app.core.config import settings

        super().__init__(
            f"结果文件已不存在(超过保留期 {settings.RESULT_RETENTION_DAYS} 天会被自动清理),"
            "请重新运行取数"
        )


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


class ConflictError(RubicError):
    """请求本身没问题,但与资源的当前状态冲突(如正在运行、已经做过)。"""

    status_code = 409


class SqlSafetyError(RubicError):
    """SQL 安全网关拒绝。"""

    status_code = 422


class BatchRejectedError(RubicError):
    """批量操作「全成功才生效」的整批拒绝:一条都没做,并逐条说明为什么。

    **刻意不扩展错误信封**:前端只读 detail 字符串(frontend/src/api.ts 的 errMsg),
    多加一个字段今天没有任何消费者,却要让**所有**错误响应改形状(登录、取数、订阅……)。
    结构化明细留在异常对象上(rejections)供测试与日志断言;线上载荷仍然只有 detail,
    办法是把每条拒绝**写成自成一句的整行**,前端不必解析也读得懂。
    将来真要给前端结构化数据,给本类加 payload、在 main.py 的 handler 里展开即可,
    信封是向前兼容的。

    状态码一律 400,**即使整批都是权限类拒绝**:一次响应只有一个码,而一批里天然混着
    403 类(无权 / 编辑权不含处分权)与 400 类(不在团队 / 已停用 / 已经是作者),
    挑一个主导码等于让同一个操作的状态码随数据抖动。
    """

    #: 明细最多列几条。载荷是一个字符串,200 条会变成一堵墙;前端已经把选中的任务
    #: 逐行列成了表,这里只需给出足够定位问题的头几条
    LISTED = 10

    def __init__(self, summary: str, rejections: list[dict]):
        """每条拒绝必须自带 `label`(这一条说的是谁/什么)与 `message`(为什么)。

        **被拒的「单位」由调用方定**:批量交接拒的是任务(`《任务名》`),代订阅拒的是人
        (姓名)。抬头的拼装留在调用方,本类不认识任何业务字段 —— 否则每加一种批量场景,
        这里就要多认一个键、多一条回落链。
        """
        self.rejections = rejections
        lines = [f"- {r['label']}:{r['message']}" for r in rejections[: self.LISTED]]
        if len(rejections) > self.LISTED:
            lines.append(f"…… 仅列前 {self.LISTED} 条,共 {len(rejections)} 条")
        super().__init__("\n".join([summary, *lines]))

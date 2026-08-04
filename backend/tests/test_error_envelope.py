"""未捕获异常的响应体必须带 detail。

Starlette 默认回纯文本 "Internal Server Error",前端 errMsg 取不到 detail 就只剩
兜底文案(线上 modes 死列那次,业务看到的只有一句「取数失败」)。
"""
import asyncio
import json

from starlette.requests import Request

from app.main import unhandled_error_handler


def _handle(exc: Exception) -> tuple[int, dict]:
    req = Request({"type": "http", "method": "POST", "path": "/api/run", "headers": [], "query_string": b""})
    resp = asyncio.run(unhandled_error_handler(req, exc))
    return resp.status_code, json.loads(resp.body)


def test_unhandled_exception_exposes_first_line():
    status, body = _handle(RuntimeError("(1364, \"Field 'modes' doesn't have a default value\")\nSQL: INSERT ..."))
    assert status == 500
    assert "RuntimeError" in body["detail"]
    assert "modes" in body["detail"]
    assert "SQL: INSERT" not in body["detail"]  # 只取首行,不外泄整段堆栈/语句


def test_exception_without_message_still_has_detail():
    status, body = _handle(ValueError())
    assert status == 500
    assert "ValueError" in body["detail"]

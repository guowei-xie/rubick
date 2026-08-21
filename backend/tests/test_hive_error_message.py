"""Hive 报错提炼:引擎错误必须变成一句人话,而不是 Thrift repr。

线上曾出现「试跑失败:TExecuteStatementResp(status=TStatus(statusCode=3, infoMessages=['*」——
真正的原因(库权限不足)被吞掉了:HiveAccessControlException 只把消息放在 infoMessages,
errorMessage 是空的,而旧代码拿不到就退回 `str(exc).split("org.apache.")[0]`,那一刀正好
切在 `infoMessages=['*` 之后。

建连期的报错(库级鉴权发生在 pyhive 的 `USE <db>`)见 test_connect_db_fallback.py。
"""
import pytest

from app.connectors.hive import _hive_error_message
from tests.conftest import HIVE_PERM_DENIED, hive_thrift_error

pytest.importorskip("pyhive")


def test_reads_error_message_when_present():
    exc = hive_thrift_error(
        error_message="Error while compiling statement: FAILED: ParseException line 1:7 xx"
    )
    assert _hive_error_message(exc) == (
        "Error while compiling statement: FAILED: ParseException line 1:7 xx"
    )


def test_falls_back_to_info_messages_and_hints_at_grant():
    """鉴权报错:errorMessage 为空,消息只在 infoMessages,且要附上「找数仓授权」的出路。"""
    msg = _hive_error_message(hive_thrift_error(info_messages=[
        HIVE_PERM_DENIED,
        "org.apache.hive.service.cli.operation.Operation:toSQLException:Operation.java:335",
    ]))
    assert "TExecuteStatementResp" not in msg  # 不再泄 Thrift repr
    assert "does not have [USE] privilege on [business_analysis]" in msg
    assert "数仓管理员" in msg
    assert "team_acct" in msg  # 账号名由上层 credential_service.redact 抹掉,连接器不越权处理


def test_plain_exception_passes_through():
    assert _hive_error_message(RuntimeError("Could not connect to host:10000")) == (
        "Could not connect to host:10000"
    )

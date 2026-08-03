"""SQL 安全网关:只读单语句放行,写/DDL/多语句拒绝。"""
import pytest

from app.core.exceptions import SqlSafetyError
from app.core.sql_gateway import validate_readonly


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "SELECT * FROM t WHERE dt = :d",
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
    "select id, name from users where id in (:ids)",
])
def test_allows_readonly(sql):
    validate_readonly(sql, "mysql")  # 不抛异常即通过


@pytest.mark.parametrize("sql", [
    "INSERT INTO t VALUES (1)",
    "UPDATE t SET a = 1",
    "DELETE FROM t",
    "DROP TABLE t",
    "TRUNCATE TABLE t",
    "ALTER TABLE t ADD COLUMN c INT",
    "CREATE TABLE t (a int)",
])
def test_rejects_writes(sql):
    with pytest.raises(SqlSafetyError):
        validate_readonly(sql, "mysql")


def test_rejects_multi_statement():
    with pytest.raises(SqlSafetyError):
        validate_readonly("SELECT 1; DROP TABLE t", "mysql")


def test_rejects_empty():
    with pytest.raises(SqlSafetyError):
        validate_readonly("   ", "mysql")


def test_trailing_semicolon_ok():
    validate_readonly("SELECT 1;", "mysql")

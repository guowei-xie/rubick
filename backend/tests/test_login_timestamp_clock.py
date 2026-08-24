"""护栏:`last_login_at` 必须与其余展示用时间列同一个时钟。

守的坑:它曾用 datetime.now(timezone.utc) 写入,而 DATETIME 列不带时区、pymysql 直接丢掉
tzinfo,于是库里存的是 UTC 墙钟。前端 format.ts 明写「后端返回的是朴素本地时间」、只做
字符串切片不转时区(audit.py::_naive 也是按这个契约写的),结果「用户管理」里的
「最近登录」比真实时间早 8 小时 —— 而同一张表里走 DB 时钟的「加入时间」是对的,两列自相矛盾。
"""
from datetime import datetime, timedelta

import pytest

from app.models.user import ROLE_USER
from app.services import auth_service

# ID 段 9110
LOGIN_USER = 9110


def test_mock_login_stamps_local_wall_clock(db, monkeypatch, user_factory):
    """登录时间戳与本机墙钟的差距应当在秒级,而不是整小时的时区偏移。"""
    user_factory(LOGIN_USER, ROLE_USER, "登录时间用户", prefix="clk")
    monkeypatch.setattr(auth_service.settings, "MOCK_AUTH", True)

    before = datetime.now()
    _token, user = auth_service.mock_login(db, f"ou_clk_{LOGIN_USER}")
    after = datetime.now()

    stamped = user.last_login_at
    assert stamped.tzinfo is None, "落库的是不带时区的 DATETIME 列,写进去就该是朴素时间"
    assert before - timedelta(seconds=5) <= stamped <= after + timedelta(seconds=5), (
        f"last_login_at={stamped} 与本机时钟 {before}~{after} 差了不止秒级 —— "
        "多半又写成了 UTC,页面上会差一个时区"
    )

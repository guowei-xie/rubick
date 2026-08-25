"""mock 登录的「只许连本地库」护栏。

背景:mock 登录按前端传来的 open_id JIT 建号。本地开发若把 DATABASE_URL 指向线上库,
点一下 mock 登录就往正式库塞一个假账号 —— bitest 就是这么被塞进 ou_admin / ou_analyst /
ou_dev / ou_viewer 的。护栏有两层:config 启动即拒(第一层)、mock_login 运行期再拒
(第二层,挡运行期改开关)。两层都要有测试,否则删掉任何一层都不会有人发现。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.core.exceptions import UnauthorizedError
from app.services import auth_service

REMOTE_DB = "mysql+pymysql://u:p@10.72.96.33:3306/bitest"


@pytest.mark.parametrize(
    "url, is_local",
    [
        ("sqlite:///./local.db", True),
        ("sqlite:////tmp/abs.db", True),
        ("mysql+pymysql://u:p@localhost:3306/rubick", True),
        ("mysql+pymysql://u:p@127.0.0.1:3306/rubick", True),
        (REMOTE_DB, False),
        ("mysql+pymysql://u:p@db.internal:3306/rubick", False),
    ],
)
def test_database_is_local(url: str, is_local: bool):
    # ALLOW_REMOTE_DB 只为让远端 URL 能构造出来(见 test_remote_db_guard):
    # 这里要验的是 DATABASE_IS_LOCAL 这条判据本身
    assert Settings(DATABASE_URL=url, ALLOW_REMOTE_DB=True).DATABASE_IS_LOCAL is is_local


def test_remote_db_with_mock_auth_refuses_to_start():
    with pytest.raises(ValidationError) as e:
        Settings(DATABASE_URL=REMOTE_DB, MOCK_AUTH=True)
    assert "MOCK_AUTH" in str(e.value)


def test_local_db_with_mock_auth_is_fine():
    s = Settings(DATABASE_URL="sqlite:///./local.db", MOCK_AUTH=True)
    assert s.MOCK_AUTH is True


def test_remote_db_without_mock_auth_is_fine():
    """线上就是这个形态:远端库 + 飞书登录 + 显式获准连远端库。
    两道护栏都不能挡住它 —— 构造不抛异常即为通过。"""
    Settings(DATABASE_URL=REMOTE_DB, MOCK_AUTH=False, ALLOW_REMOTE_DB=True)


def test_database_display_hides_password():
    """报错信息会带上库地址,密码必须打码(它会进日志)。"""
    shown = Settings(DATABASE_URL=REMOTE_DB, ALLOW_REMOTE_DB=True).database_display
    assert "***" in shown and ":p@" not in shown
    assert "10.72.96.33:3306/bitest" in shown


def test_mock_login_refuses_remote_db(db, monkeypatch):
    """第二层:运行期把 MOCK_AUTH 拨成 true 也不行 —— 库是远端的就拒。"""
    monkeypatch.setattr(settings, "MOCK_AUTH", True)
    monkeypatch.setattr(settings, "DATABASE_URL", REMOTE_DB)
    with pytest.raises(UnauthorizedError):
        auth_service.mock_login(db, "ou_viewer")


def test_mock_login_works_on_local_db(db, monkeypatch):
    monkeypatch.setattr(settings, "MOCK_AUTH", True)
    token, user = auth_service.mock_login(db, "ou_guard_probe")
    assert token and user.feishu_open_id == "ou_guard_probe"

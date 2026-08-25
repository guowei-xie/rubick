"""「不许连线上库」的护栏:开发机与测试一律用本地库。

两件真实发生过的事(2026-08-25)促成了它:

① 一台开发机的 config.ini 直连线上库,一个从前一晚就开着的本机 worker **替线上认领了任务**。
   认领是原子的 —— 抢到就是抢到:那次订阅定时运行真的跑了、真的成功了(631 行),结果 CSV
   落在开发者的笔记本上,线上只剩一条「成功 631 行」却下载不到的运行记录;同一天一个业务
   用户 15 分钟的长取数也被同一个进程接走。
② 同一份 config.ini 意味着本机随手跑的脚本、以及任何忘了隔离的测试,都在直接读写线上库。

护栏拦在 **Settings 构造期**,所以 API / worker / migrate / 临时脚本一视同仁,没有哪个入口
能绕过、也不必逐个入口去记得加判断。判据是「库在不在本机」而不是「我是不是线上」——
后者没有可靠的自证方式(主机名、路径、环境变量都能被复制)。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

REMOTE_DB = "mysql+pymysql://u:p@10.72.96.33:3306/bitest"  # 就是线上那台
LOCAL_SQLITE = "sqlite:///./rubick.db"


def test_local_sqlite_needs_no_extra_config():
    """本地开发与测试的标准形态:sqlite,不必为护栏多配一行。"""
    s = Settings(DATABASE_URL=LOCAL_SQLITE)
    assert s.DATABASE_ALLOWED is True and s.ALLOW_REMOTE_DB is False


@pytest.mark.parametrize("url", [LOCAL_SQLITE, "mysql+pymysql://u:p@localhost:3306/rubick"])
def test_local_db_starts(url: str):
    Settings(DATABASE_URL=url)


def test_remote_db_refuses_to_start():
    """核心断言:指向 bitest 线上库的进程,压根起不来。"""
    with pytest.raises(ValidationError) as e:
        Settings(DATABASE_URL=REMOTE_DB)
    assert "ALLOW_REMOTE_DB" in str(e.value)


def test_remote_db_starts_only_when_explicitly_allowed():
    """线上就是这个形态:远端库 + config.ini 里显式打开。护栏不能挡住它。"""
    assert Settings(DATABASE_URL=REMOTE_DB, ALLOW_REMOTE_DB=True).DATABASE_ALLOWED is True


def test_guard_covers_every_entrypoint_not_just_the_worker():
    """拦在构造期 = 谁都拦。这条用例钉住的是「护栏的层次」:
    如果哪天有人把它挪进 worker.main,API 与临时脚本就又能连线上库了。"""
    for kwargs in ({}, {"RUN_INLINE": True}, {"WORKER_CONCURRENCY": 8}):
        with pytest.raises(ValidationError):
            Settings(DATABASE_URL=REMOTE_DB, **kwargs)


def test_worker_refuses_to_start_on_remote_db(monkeypatch):
    """第二层:挡运行期被改掉的开关(同 mock 登录的两层写法)。
    worker 是危害最大的入口 —— 它替线上**认领**任务。"""
    from app import worker

    monkeypatch.setattr(
        worker, "settings", Settings(DATABASE_URL=REMOTE_DB, ALLOW_REMOTE_DB=True)
    )
    monkeypatch.setattr(worker.settings, "ALLOW_REMOTE_DB", False)  # 运行期被拨回去
    with pytest.raises(SystemExit) as e:
        worker.main()
    assert "ALLOW_REMOTE_DB" in str(e.value)


def test_refusal_message_hides_password_and_says_how_to_fix():
    """这句话会进日志、也常是运维唯一能看到的线索:不能泄密码,也不能只说「拒绝」。"""
    msg = Settings(DATABASE_URL=REMOTE_DB, ALLOW_REMOTE_DB=True).remote_db_refusal()
    assert ":p@" not in msg and "***" in msg
    assert "ALLOW_REMOTE_DB" in msg and "sqlite" in msg


def test_this_very_test_run_is_on_a_temp_sqlite():
    """把「测试统一用本地 sqlite」这条从**套件内部**也钉一遍。

    tests/conftest.py 里的闸门是在用例跑起来之前拦(那才来得及 —— 第一个动作 create_all
    就已经在建表了);这条用例是它的对照物:闸门若被删掉,这里会红。
    """
    import tempfile

    from app.core.database import engine

    assert engine.url.get_backend_name() == "sqlite"
    assert str(engine.url.database).startswith(tempfile.gettempdir())

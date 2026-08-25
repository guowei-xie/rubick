"""「开发机不许执行线上取数」的护栏。

背景(2026-08-25 实测):开发机的 config.ini 直连线上库,一个从前一晚就开着的本机 worker
替线上认领了一次订阅定时运行。认领是原子的 —— 抢到就是抢到:那次取数真的跑了、真的成功了,
结果 CSV 落在开发者的笔记本上,线上只剩一条「成功 631 行」却下载不到的运行记录;同一天
一个业务用户的 15 分钟长取数也被同一个进程接走。

护栏落在「库在不在本机」上:唯一危险的组合是**开发机 + 远端库**,而它与全部安全组合
(开发机 + 本地库、线上机 + 远端库且显式获准)恰好被这一条判据分开。
执行者有两种形态,两种都要拦:worker 进程,以及 RUN_INLINE=true 时在请求内跑取数的 API。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

REMOTE_DB = "mysql+pymysql://u:p@10.72.96.33:3306/bitest"
LOCAL_DB = "sqlite:///./local.db"


def test_local_db_may_execute_without_any_config():
    """本地开发不该为此多配一行:连本地库天然获准。"""
    assert Settings(DATABASE_URL=LOCAL_DB).MAY_EXECUTE_JOBS is True


def test_remote_db_may_not_execute_by_default():
    """就是 8-25 那台开发机的形态。"""
    assert Settings(DATABASE_URL=REMOTE_DB).MAY_EXECUTE_JOBS is False


def test_remote_db_may_execute_when_explicitly_allowed():
    """线上就是这个形态:远端库 + config.ini 里显式打开。"""
    assert Settings(DATABASE_URL=REMOTE_DB, WORKER_ALLOW_REMOTE_DB=True).MAY_EXECUTE_JOBS is True


def test_worker_refuses_to_start_on_remote_db(monkeypatch):
    """worker 的第一行就是这道闸:拦在认领之前,不能等抢到任务才发现。"""
    from app.core import config as config_mod
    from app import worker

    monkeypatch.setattr(worker, "settings", Settings(DATABASE_URL=REMOTE_DB))
    with pytest.raises(SystemExit) as e:
        worker.main()
    assert "WORKER_ALLOW_REMOTE_DB" in str(e.value)
    assert config_mod  # 仅为说明护栏文案来自 config,不在 worker 里另写一套


def test_inline_run_on_remote_db_refuses_to_start():
    """RUN_INLINE 让取数跑在 API 进程里,那它也是执行者,同受这条约束。"""
    with pytest.raises(ValidationError) as e:
        Settings(DATABASE_URL=REMOTE_DB, RUN_INLINE=True)
    assert "WORKER_ALLOW_REMOTE_DB" in str(e.value)


def test_inline_run_is_fine_on_local_db():
    """本地开发的常见形态(无需另起 worker),不能被这道闸误伤。"""
    assert Settings(DATABASE_URL=LOCAL_DB, RUN_INLINE=True).RUN_INLINE is True


def test_inline_run_on_remote_db_allowed_when_explicit():
    Settings(DATABASE_URL=REMOTE_DB, RUN_INLINE=True, WORKER_ALLOW_REMOTE_DB=True)


def test_refusal_message_hides_password_and_says_how_to_fix():
    """这句话会进日志、也会是运维唯一能看到的线索:不能泄密码,也不能只说「拒绝」。"""
    msg = Settings(DATABASE_URL=REMOTE_DB).remote_executor_refusal("worker")
    assert ":p@" not in msg and "***" in msg
    assert "WORKER_ALLOW_REMOTE_DB" in msg and "DATABASE_URL" in msg

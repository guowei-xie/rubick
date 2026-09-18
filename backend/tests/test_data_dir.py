"""数据产物的存放位置:一个可配置的根目录 DATA_DIR,布局由代码定。

为什么值得单开一个用例文件:这条链路的**失败模式是静默的**。产物落在哪不影响服务起不起来、
取数成不成功、运行记录显不显示「成功 N 行」—— 只有点下载的人才会发现文件不在那儿。
线上 2026-08-25 就出过一次「有记录、无结果」,排查成本远高于启动时当场报错。

所以这里钉三件事:①换盘只需改一处;②子目录布局不随配置漂;③旧键 RESULT_DIR 若还留在
config.ini 里,必须**当场拒绝启动**而不是被 extra="ignore" 静默丢掉。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import BACKEND_DIR, Settings, _load_ini

LOCAL_SQLITE = "sqlite:///./rubick.db"


def _settings(**kwargs) -> Settings:
    return Settings(DATABASE_URL=LOCAL_SQLITE, **kwargs)


def test_default_is_relative_to_backend_dir():
    """默认形态(本机开发):backend/data 下的 results/ 与 backups/。"""
    s = _settings()
    assert s.data_dir_path == BACKEND_DIR / "data"
    assert s.result_dir_path == BACKEND_DIR / "data" / "results"
    assert s.backup_dir_path == BACKEND_DIR / "data" / "backups"


def test_absolute_data_dir_is_used_as_is():
    """线上形态:指到独立数据盘。绝对路径不再拼 backend/ 前缀。"""
    s = _settings(DATA_DIR="/data/rubick")
    assert s.data_dir_path == Path("/data/rubick")
    assert s.result_dir_path == Path("/data/rubick/results")
    assert s.backup_dir_path == Path("/data/rubick/backups")


def test_one_knob_moves_every_data_product():
    """核心断言:换盘只改 DATA_DIR 一处,**所有**产物一起走。

    从前 backups/ 是写死在 cleanup_mock_users 里的第二个答案,换盘时它不会跟着走 ——
    这条用例就是拦「再冒出第三个答案」。
    """
    s = _settings(DATA_DIR="/mnt/elsewhere")
    for p in (s.result_dir_path, s.backup_dir_path):
        assert p.parent == Path("/mnt/elsewhere")


def test_layout_does_not_drift_with_location():
    """子目录名是代码的决定,不该随部署位置变 —— 相对/绝对两种配法下布局必须一致。"""
    rel = _settings(DATA_DIR="data")
    abs_ = _settings(DATA_DIR="/data/rubick")
    assert rel.result_dir_path.name == abs_.result_dir_path.name == "results"
    assert rel.backup_dir_path.name == abs_.backup_dir_path.name == "backups"


def test_result_service_follows_data_dir(monkeypatch, tmp_path):
    """落盘入口读的是同一个属性,不是自己拼的路径。"""
    from app.services import result_service

    monkeypatch.setattr(
        result_service, "settings", _settings(DATA_DIR=str(tmp_path))
    )
    p = result_service._abs_path("jobs/12/x.csv")
    assert p == tmp_path / "results" / "jobs" / "12" / "x.csv"


def test_same_object_key_resolves_under_either_location(monkeypatch, tmp_path):
    """「搬文件不用改库」的前提:同一个 object_key 在旧盘和新盘上都解析得开。

    库里存的是相对 key(见 QueryJob.result_object_key),所以换盘后**存量运行记录的
    下载依旧可用** —— 前提是搬文件时保住 results/ 以下的目录结构。
    """
    from app.services import result_service

    key = "jobs/12/x.csv"
    old_disk, new_disk = tmp_path / "opt", tmp_path / "data"
    resolved = []
    for base in (old_disk, new_disk):
        monkeypatch.setattr(result_service, "settings", _settings(DATA_DIR=str(base)))
        resolved.append(result_service._abs_path(key))

    # 两处只有基目录不同,results/ 以下逐字一致 —— 库里那一行不必跟着改
    assert resolved == [old_disk / "results" / key, new_disk / "results" / key]


def test_legacy_result_dir_key_refuses_to_start(monkeypatch, tmp_path):
    """旧键必须**当场报错**。

    `extra="ignore"` 会把不认识的键静默丢掉:若不拦,线上那份写着 `RESULT_DIR = /data/...`
    的 config.ini 会被无声忽略,结果安静地落回 backend/data/results —— 服务全绿,
    只有下载 404。
    """
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        f"[rubick]\nDATABASE_URL = {LOCAL_SQLITE}\nRESULT_DIR = /data/rubick/results\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_FILE", str(cfg))
    with pytest.raises(ValueError) as e:
        _load_ini()
    msg = str(e.value)
    assert "RESULT_DIR" in msg and "DATA_DIR" in msg  # 说清拦的是哪个键、该怎么改
    assert str(cfg) in msg  # 也说清是哪份配置文件 —— 线上不止一处 config


def test_new_key_loads_from_ini(monkeypatch, tmp_path):
    """对照物:换成新键就该正常读进来(否则上一条可能只是「什么都读不了」)。"""
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        f"[rubick]\nDATABASE_URL = {LOCAL_SQLITE}\nDATA_DIR = /data/rubick\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_FILE", str(cfg))
    assert Settings(**_load_ini()).result_dir_path == Path("/data/rubick/results")

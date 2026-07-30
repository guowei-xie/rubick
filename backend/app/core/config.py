"""集中配置。

所有可调项从 **config.ini**(项目根 backend/config.ini)读取,
首次部署请 `cp config.example.ini config.ini` 后按需修改。
未在 config.ini 出现的项使用下方默认值;也可用环境变量 CONFIG_FILE 指定配置文件路径。
"""
from __future__ import annotations
import configparser
import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 目录(本文件为 backend/app/core/config.py)
BACKEND_DIR = Path(__file__).resolve().parents[2]
CONFIG_SECTION = "rubick"


class Settings(BaseSettings):
    # 不再从 .env 读取;统一走 config.ini。多余键忽略。
    model_config = SettingsConfigDict(extra="ignore")

    # ---- 平台元数据库(业务库,线上 MySQL,在 config.ini 指定)----
    DATABASE_URL: str = "mysql+pymysql://rubick:rubick@localhost:3306/rubick"

    # ---- 安全 ----
    JWT_SECRET: str = "change-me-in-prod"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 720

    # ---- 登录 ----
    # mock 登录开关,默认开启(免飞书凭证即可登录)
    MOCK_AUTH: bool = True
    # 引导管理员:逗号分隔的飞书邮箱或 open_id,登录时自动授予管理员(解决上线冷启动)
    BOOTSTRAP_ADMINS: str = ""
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""
    # 飞书 OAuth 回调地址(可配置)
    FEISHU_REDIRECT_URI: str = "http://localhost:5173/auth/callback"

    # ---- 结果落地(本地文件系统)----
    # 结果 CSV 存放根目录(相对路径以 backend/ 为基准)
    RESULT_DIR: str = "data/results"
    # 下载签名链接有效期
    DOWNLOAD_URL_EXPIRE_SECONDS: int = 3600
    # 结果文件保留天数,worker 定期清理过期文件
    RESULT_RETENTION_DAYS: int = 7

    # ---- 取数资源治理 ----
    QUERY_TIMEOUT_SECONDS: int = 120
    MAX_RESULT_ROWS: int = 100_000

    # ---- 异步取数(独立 DB 轮询 worker,无需 Redis/Celery)----
    # true=在请求内同步执行(无需 worker,便于本地开发);false=交给 worker 后台执行
    RUN_INLINE: bool = False
    # worker 轮询 queued 任务的间隔(秒)
    WORKER_POLL_INTERVAL: float = 2.0

    # ---- 访问地址 / 端口(可配置)----
    # 后端监听地址与端口(部署脚本据此启动 uvicorn)
    BACKEND_HOST: str = "0.0.0.0"
    BACKEND_PORT: int = 8000
    # 前端地址,用于通知里的下载/任务跳转链接
    APP_BASE_URL: str = "http://localhost:5173"
    # CORS 允许来源
    FRONTEND_ORIGIN: str = "http://localhost:5173"

    @property
    def result_dir_path(self) -> Path:
        p = Path(self.RESULT_DIR)
        return p if p.is_absolute() else BACKEND_DIR / p


def _load_ini() -> dict:
    """读取 config.ini 的 [rubick] 段为普通 dict(键大小写敏感,对应 Settings 字段)。"""
    path = Path(os.getenv("CONFIG_FILE") or (BACKEND_DIR / "config.ini"))
    if not path.exists():
        return {}
    parser = configparser.ConfigParser()
    parser.optionxform = str  # 保留键的大小写
    parser.read(path, encoding="utf-8")
    if not parser.has_section(CONFIG_SECTION):
        return {}
    return dict(parser.items(CONFIG_SECTION))


@lru_cache
def get_settings() -> Settings:
    return Settings(**_load_ini())


settings = get_settings()

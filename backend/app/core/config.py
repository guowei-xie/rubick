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
from urllib.parse import urlparse

from pydantic import model_validator
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
    # 库内敏感字段(数据源密码、用户飞书 token)的加密密钥;留空则由 JWT_SECRET 派生。
    # 生产建议单独配置,与 JWT_SECRET 区分:轮换 JWT 密钥时不影响已加密数据。
    SECRET_ENCRYPTION_KEY: str = ""

    # ---- 登录 ----
    # mock 登录开关,默认关闭(生产安全默认)。
    # 注意:mock 登录信任前端传入的 open_id、无凭证校验,一旦开启且该 open_id 命中
    # BOOTSTRAP_ADMINS 即可无凭证登录为管理员。仅本地无飞书想快速试跑时才显式改 true。
    MOCK_AUTH: bool = False
    # 引导管理员:逗号分隔的飞书邮箱或 open_id,登录时自动授予管理员(解决上线冷启动)
    BOOTSTRAP_ADMINS: str = ""
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""
    # 飞书 OAuth 回调地址;留空则自动派生为 {APP_BASE_URL}/auth/callback
    FEISHU_REDIRECT_URI: str = ""

    # ---- 结果落地(本地文件系统)----
    # 结果 CSV 存放根目录(相对路径以 backend/ 为基准)
    RESULT_DIR: str = "data/results"
    # 下载签名链接有效期
    DOWNLOAD_URL_EXPIRE_SECONDS: int = 3600
    # 结果文件保留天数,worker 定期清理过期文件
    RESULT_RETENTION_DAYS: int = 7

    # ---- 取数资源治理 ----
    # 默认查询超时(秒)。适用于 MySQL 等即时查询;单个任务可在模板上单独设置覆盖。
    QUERY_TIMEOUT_SECONDS: int = 120
    # Hive 批处理查询默认超时(秒),默认 1 小时——Hive 多为长耗时批处理,不套用上面的即时默认。
    HIVE_QUERY_TIMEOUT_SECONDS: int = 3600
    MAX_RESULT_ROWS: int = 100_000
    # 「枚举值获取 SQL」一次最多返回的候选数(超出截断,业务侧仍可手输未列出的值)
    ENUM_VALUE_CAP: int = 1000
    # 业务侧「更新枚举值」的复用窗口(秒):窗口内重复点击直接复用最新结果,不再查库
    ENUM_REFRESH_MIN_INTERVAL_SECONDS: int = 30

    # ---- 异步取数(独立 DB 轮询 worker,无需 Redis/Celery)----
    # true=在请求内同步执行(无需 worker,便于本地开发);false=交给 worker 后台执行
    RUN_INLINE: bool = False
    # worker 轮询 queued 任务的间隔(秒)
    WORKER_POLL_INTERVAL: float = 2.0

    # ---- 访问地址 / 端口(可配置)----
    # 后端监听地址与端口(部署脚本据此启动 uvicorn)
    BACKEND_HOST: str = "0.0.0.0"
    BACKEND_PORT: int = 8000
    # 应用对外访问的 origin(单一来源)。留空则派生为 http://localhost:{BACKEND_PORT};
    # 单端口部署下 SPA 与 /api 同源,通常只需设这一项。
    APP_BASE_URL: str = ""
    # CORS 允许来源;留空则跟随 APP_BASE_URL(单端口同源时不触发)。
    # 仅 split dev-mode(npm run dev 跑 5173)才需手动指 http://localhost:5173。
    FRONTEND_ORIGIN: str = ""

    @model_validator(mode="after")
    def _derive_urls(self) -> "Settings":
        """把易漂移的 URL 收敛到单一来源 APP_BASE_URL:未显式设置的项按单端口模型派生。

        BACKEND_HOST 是 uvicorn 绑定地址(可能是 0.0.0.0,不能作浏览器 URL),
        故派生用 localhost 而非 BACKEND_HOST。
        """
        if not self.APP_BASE_URL:
            self.APP_BASE_URL = f"http://localhost:{self.BACKEND_PORT}"
        # 去掉结尾斜杠,避免拼出 .../rubick//auth/callback 这类双斜杠地址
        self.APP_BASE_URL = self.APP_BASE_URL.rstrip("/")
        if not self.FEISHU_REDIRECT_URI:
            self.FEISHU_REDIRECT_URI = f"{self.APP_BASE_URL}/auth/callback"
        if not self.FRONTEND_ORIGIN:
            # Origin 只有 scheme://host[:port],不含路径——APP_BASE_URL 带子路径时要截掉,
            # 否则 CORS 白名单永远匹配不上浏览器发来的 Origin 头。
            u = urlparse(self.APP_BASE_URL)
            self.FRONTEND_ORIGIN = f"{u.scheme}://{u.netloc}" if u.netloc else self.APP_BASE_URL
        return self

    @property
    def BASE_PATH(self) -> str:
        """前端部署基路径,从 APP_BASE_URL 的 path 部分取,形如 "/" 或 "/rubick/"。

        独占域名/端口时为 "/";挂在网关子路径下(nginx `location /rubick/` 剥前缀转发)
        时为 "/rubick/"。构建期由 deploy.sh 作为 VITE_BASE_PATH 传给 vite,
        使静态资源前缀、路由 basename、/api 前缀与后端认定的对外地址始终一致。
        """
        path = urlparse(self.APP_BASE_URL).path.strip("/")
        return f"/{path}/" if path else "/"

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

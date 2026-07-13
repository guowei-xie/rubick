"""集中配置。所有可调项从环境变量 / .env 读取。"""
from __future__ import annotations
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 平台元数据库
    DATABASE_URL: str = "mysql+pymysql://rubic:rubic@localhost:3306/rubic_meta"

    # 安全
    JWT_SECRET: str = "change-me-in-prod"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 720

    # 飞书
    MOCK_AUTH: bool = True
    # 引导管理员:逗号分隔的飞书邮箱或 open_id,登录时自动授予管理员(解决上线冷启动)
    BOOTSTRAP_ADMINS: str = ""
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""
    FEISHU_REDIRECT_URI: str = "http://localhost:5173/auth/callback"

    # 结果对象存储
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "rubic-results"
    MINIO_SECURE: bool = False
    DOWNLOAD_URL_EXPIRE_SECONDS: int = 3600
    # 结果文件保留天数,到期由 MinIO 生命周期规则自动删除
    RESULT_RETENTION_DAYS: int = 7

    # 取数资源治理
    QUERY_TIMEOUT_SECONDS: int = 120
    MAX_RESULT_ROWS: int = 100_000

    # 异步任务(Celery + Redis)
    REDIS_URL: str = "redis://localhost:6379/0"
    # true=经 Celery 异步执行;false=在请求内联执行(无需 worker,便于测试)
    ASYNC_QUERY: bool = True
    # 前端地址,用于通知里的下载/任务跳转链接
    APP_BASE_URL: str = "http://localhost:5173"

    # CORS
    FRONTEND_ORIGIN: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

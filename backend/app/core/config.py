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
from sqlalchemy.engine import make_url

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
    # **且只在 DATABASE_URL 指向本地库时才允许开**(见 _guard_mock_auth):mock 登录会
    # JIT 建号,连着远端库开它就会把 ou_admin 这类假账号写进正式库。
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
    # 结果文件保留天数。worker 每小时清一次过期文件(启动时也先清一次)
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
    # worker 同时能跑几个**异步取数**。取数几乎全程阻塞在等目标库回包上,所以线程就够;
    # 串行的代价是一个 Hive 长任务(默认上限 1 小时)运行期间全平台的取数都排在它后面。
    # 不含编辑器里的「测试运行」—— 那些跑在 API 进程里、不受这个数约束(见 template_service
    # .test_run),所以目标库的连接数要按「这个数 + 同时可能试跑的人数」来备。
    # 另受本机内存约束(每个结果最多 MAX_RESULT_ROWS 行进内存)。
    WORKER_CONCURRENCY: int = 2

    # ---- 任务订阅(定时自动运行)----
    # 订阅者连续多少个**成功**运行期未查看结果(下载或预览)后,自动取消其订阅并通知本人。
    # 失败的期不计入(那不是订阅者的问题)。
    SUBSCRIPTION_MISS_LIMIT: int = 3
    # worker 扫描到期订阅计划的间隔(秒)。调度精度即由它决定,30 秒对「按分钟配置的
    # 运行时刻」绰绰有余;调小只是白烧轮询。
    SCHEDULE_SCAN_INTERVAL_SECONDS: int = 30

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

    @model_validator(mode="after")
    def _guard_mock_auth(self) -> "Settings":
        """护栏:mock 登录只允许连本地库。

        mock 登录会按前端传来的 open_id JIT 建号(见 auth_service.mock_login),所以
        「MOCK_AUTH=true + DATABASE_URL 指向远端库」这个组合等于往正式库里灌假账号 ——
        bitest 正是这么被灌进 ou_admin / ou_analyst / ou_dev / ou_viewer 的
        (已由 app/cleanup_mock_users.py 清除)。这里直接拒绝启动,而不是悄悄把开关关掉:
        配置写错了就该当场报出来,而不是让人以为 mock 登录坏了。
        """
        if self.MOCK_AUTH and not self.DATABASE_IS_LOCAL:
            raise ValueError(
                f"MOCK_AUTH=true 但 DATABASE_URL 指向远端库({self.database_display}):"
                "mock 登录会把假账号写进这个库。要么把 MOCK_AUTH 改回 false(走飞书登录),"
                "要么把 DATABASE_URL 换成本地库(sqlite 或 localhost 的 MySQL)。"
            )
        return self

    @property
    def DATABASE_IS_LOCAL(self) -> bool:
        """DATABASE_URL 是否指向本机库。

        判据只有一条:主机名是本机。sqlite 走文件、URL 里根本没有主机名(host 为 None),
        因此与 localhost / 回环地址一同落在同一个判断里 —— 不必为它单开分支。
        用 SQLAlchemy 的 URL 解析而非 urlparse:连接串的转义规则(密码里的 @ / : 等)
        以它为准,这里是安全边界,不该另立一套解析。
        """
        return (make_url(self.DATABASE_URL).host or "") in {"", "localhost", "127.0.0.1", "::1"}

    @property
    def database_display(self) -> str:
        """库地址的可打印形式(密码已打码,可安全进日志/报错)。

        与 initdb / migrate / renumber_users 等脚本里打印 `engine.url` 的口径一致
        (同为 SQLAlchemy 的 hide_password 渲染),不另造一套遮蔽写法。
        """
        return make_url(self.DATABASE_URL).render_as_string(hide_password=True)

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

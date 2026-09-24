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
    # 允许连**远端**平台库。默认 false —— 只有真正的线上部署才在 config.ini 里打开。
    # 开发机 / 测试一旦把 DATABASE_URL 指向线上库,进程直接起不来(见 _guard_remote_db)。
    # **线上必须显式打开**,否则 API、worker、migrate 全都起不来。
    ALLOW_REMOTE_DB: bool = False

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
    # 飞书应用的「分享给他人」链接(applink.feishu.cn/...)。没被授予这个应用的人点飞书登录
    # 会被飞书挡在外面,平台这边只看得到「他没登录」,给不了任何指引 —— 所以要有一条能发给
    # 他、也能让他自己点过去的申请入口。
    # 随飞书应用而变(换应用、换租户就换一条),所以它是配置,而不是像手册地址那样的前端常量。
    # 留空 = 界面上不出现任何申请入口(而不是给一个点了没反应的按钮)。
    FEISHU_APP_APPLY_URL: str = ""

    # ---- 数据产物落地(本地文件系统)----
    # 平台**所有**数据产物的根目录:取数结果 CSV 在 `results/`,维护脚本的行级备份在
    # `backups/`。相对路径以 backend/ 为基准;**生产应指到独立数据盘**(如 /data/rubick)
    # —— 这里的体积由业务用量决定(单份结果可上百 MB),系统盘的余量却不由它决定。
    #
    # 子目录名**刻意不可配**:位置是部署的决定,布局是代码的决定。给每类产物各开一个键,
    # 「产物到底在哪」就会有好几个答案 —— backups/ 从前就是写死在 cleanup_mock_users
    # 里的第二个答案,换盘时它不会跟着走。
    DATA_DIR: str = "data"
    # 下载签名链接有效期
    DOWNLOAD_URL_EXPIRE_SECONDS: int = 3600
    # 结果文件保留天数。worker 每小时清一次过期文件(启动时也先清一次)
    RESULT_RETENTION_DAYS: int = 7

    # ---- 取数资源治理 ----
    # 默认查询超时(秒)。适用于 MySQL 等即时查询;单个任务可在模板上单独设置覆盖。
    QUERY_TIMEOUT_SECONDS: int = 120
    # Hive 批处理查询默认超时(秒),默认 1 小时——Hive 多为长耗时批处理,不套用上面的即时默认。
    HIVE_QUERY_TIMEOUT_SECONDS: int = 3600
    # 单次取数最多返回多少行。**0 = 不限,这是默认**。
    #
    # 从前是 100000,而且是**静默**截断:超出的行被直接丢掉,界面上没有任何标记,业务用户
    # 唯一的线索是「行数比预期少」—— 一份少了行的报表比一次失败危险得多。它当初还兼着
    # 「别把进程撑爆」的活,但那份保护是假的:MySQL 的默认游标在 execute 那一刻就把整个
    # 结果集下载进了内存,截断发生在下载之后。现在取数是服务端游标 → CSV 直接落盘
    # (见 connectors 的 stream() 与 result_service.write_csv),内存占用与行数无关,
    # 所以不再需要用行数上限来保护进程,上限也就没有理由默认存在。
    #
    # 真要设个上限就填正数(如 500000):行为回到「截断 + 审计记 truncated」,
    # 而**界面依旧不会提示**,所以填之前先想清楚谁来告诉业务用户「这份不是全部」。
    MAX_RESULT_ROWS: int = 0
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
    # 内存不再随结果行数增长(取数是流式落盘),但磁盘会:并发跑的大结果同时占着
    # 结果目录(DATA_DIR/results),按「这个数 × 单份结果可能多大 × RESULT_RETENTION_DAYS」估容量。
    WORKER_CONCURRENCY: int = 2

    # ---- 任务订阅(定时自动运行)----
    # 订阅者连续多少个**成功**运行期未查看结果(下载或预览)后,自动取消其订阅并通知本人。
    # 失败的期不计入(那不是订阅者的问题)。
    SUBSCRIPTION_MISS_LIMIT: int = 10
    # worker 扫描到期订阅计划的间隔(秒)。调度精度即由它决定,30 秒对「按分钟配置的
    # 运行时刻」绰绰有余;调小只是白烧轮询。
    SCHEDULE_SCAN_INTERVAL_SECONDS: int = 30

    # ---- 任务治理(闲置提示)----
    # 已上线任务连续多少天没有任何运行记录,就在任务列表里标成「闲置」、视觉后退并排到末尾。
    # **只是提示,不改变任何能力**:闲置任务照样能跑、能订阅,下不下线是人的决定 ——
    # 自动下线会误伤季度/年度才跑一次的任务,而那种任务恰恰最没人记得重新上线。
    # 默认 90 天:比最长的常规业务周期(季度)多一点,一个季度跑一次的任务不会被误标。
    # 0 或负数 = 关掉这项提示(界面上不再出现闲置标记与筛选片)。
    TASK_IDLE_DAYS: int = 90

    # ---- 开放 API(/api/v1/*)----
    # 内存滑动窗口限流:每用户每分钟最多多少次调用(按 token 对应的用户计)。
    # Agent 轮询运行状态建议每 5s 一次(12 次/分),默认 120 余量充足;
    # 超限返回 429。0 或负数 = 关闭限流(不推荐)。
    API_RATE_LIMIT_PER_MINUTE: int = 120
    # 每用户同时在途(排队 + 执行中)的 API 运行上限,超出返回 429。限流管的是「调用多频繁」,
    # 这个管的是「一个人同时占几个队列位」—— 后者才是把队列挤满的那个量:worker 全局只有
    # WORKER_CONCURRENCY 个位子,一枚 token 塞进几十条就让所有人排在它后面。
    # 同参数的重复提交不占名额(直接接上在途那条,见 query_service.submit_run)。0 或负数 = 不限。
    API_MAX_INFLIGHT_PER_USER: int = 3

    # ---- 结果复用(query_service.submit_run)----
    # 同任务、同上线版本、同参数、时效内已有成功结果时直接复用、不再执行(跨用户:取数身份
    # 跟着任务走,谁跑结果都一样)。调用方随时可以 fresh=true 强制重跑;任务级另有
    # 「结果可复用」开关(SqlTemplate.allow_result_reuse)。false = 全平台关掉复用。
    RESULT_REUSE_ENABLED: bool = True
    # 时效:Hive 等 T+1 数仓以「当天」为界;MySQL 是实时库,只复用最近这么多分钟内的结果
    # (且不跨天)。0 = MySQL 任务一律不复用。
    RESULT_REUSE_MYSQL_MINUTES: int = 30

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

    @model_validator(mode="after")
    def _guard_remote_db(self) -> "Settings":
        """护栏:**任何进程**都不许连远端平台库,除非 config.ini 里显式获准。

        拦在 Settings 构造期,所以它对 API / worker / migrate / 临时脚本一视同仁 ——
        没有哪个入口能绕过它,也不必逐个入口去记得加判断。

        为什么是「库在不在本机」而不是「我是不是线上」:后者没有可靠的自证方式(主机名、
        路径、环境变量都能被复制),而前者恰好把唯一危险的组合(开发机 + 线上库)与全部
        安全组合(开发机 + 本地库、线上机 + 线上库且显式获准)分开。
        """
        if not self.DATABASE_ALLOWED:
            raise ValueError(self.remote_db_refusal())
        return self

    @property
    def DATABASE_ALLOWED(self) -> bool:
        """这个进程可不可以用 DATABASE_URL 指的这个库。

        连本地库随便用,不需要多配一行;**连远端库必须显式打开 ALLOW_REMOTE_DB**。
        """
        return self.DATABASE_IS_LOCAL or self.ALLOW_REMOTE_DB

    def remote_db_refusal(self) -> str:
        """拒绝时给人看的话。**必须说清连的哪个库、为什么拦、怎么解** —— 否则收到的人
        只会以为服务坏了,然后顺手打开开关绕过去。"""
        return (
            f"DATABASE_URL 指向远端库({self.database_display}),而 ALLOW_REMOTE_DB 没有打开,"
            "拒绝启动。这条护栏挡的是两件真实发生过的事(2026-08-25):"
            "① 开发机上的 worker 替线上认领并执行了真实取数,结果文件落在开发机上,"
            "线上只剩一条「有记录、无结果」的运行;② 本机脚本/测试直接读写线上库。"
            "本机开发与测试请把 DATABASE_URL 换成本地库(sqlite 即可);"
            "这台确实是线上机器的话,在 config.ini 里写 ALLOW_REMOTE_DB = true。"
        )

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
    def result_row_cap(self) -> int | None:
        """生效的取数行数上限;**None = 不限**(MAX_RESULT_ROWS <= 0)。

        「0 表示不限」这个换算只在这里表述一次:取数链路一律读这个属性,别各自去判 <= 0
        —— 漏判一处的后果不是报错,而是 min(50, 0) = 0 那种「一行都不给」的静默走样。
        """
        return self.MAX_RESULT_ROWS if self.MAX_RESULT_ROWS > 0 else None

    @property
    def data_dir_path(self) -> Path:
        """数据产物根目录的绝对路径。下面两个属性都从它派生,**换盘只改 DATA_DIR 一处**。"""
        p = Path(self.DATA_DIR)
        return p if p.is_absolute() else BACKEND_DIR / p

    @property
    def result_dir_path(self) -> Path:
        """取数结果 CSV 的根目录;object_key 即相对它的路径(见 result_service)。"""
        return self.data_dir_path / "results"

    @property
    def backup_dir_path(self) -> Path:
        """维护脚本落行级备份的目录(见 cleanup_mock_users)。"""
        return self.data_dir_path / "backups"


# 已废弃的配置键 -> 该怎么改。命中即**拒绝启动**。
#
# 为什么要专门拦:`extra="ignore"` 会把不认识的键静默丢掉,而这一类键说的是「产物落在哪」。
# 静默丢掉的后果不是报错 —— 服务照常起、取数照常成功、运行记录照常显示「成功 N 行」,
# 只有点下载的人才会发现文件不在那儿(线上 2026-08-25 已经出过一次「有记录、无结果」,
# 排查成本远高于启动时当场报错)。
_REMOVED_KEYS = {
    "RESULT_DIR": (
        "结果目录不再单独配置,改配数据产物根目录 DATA_DIR(结果固定落在它的 results/ 下)。"
        "原先 `RESULT_DIR = /x/results` 的,现在写 `DATA_DIR = /x`;"
        "原先是默认值 `data/results` 的,删掉这行即可(DATA_DIR 默认就是 data)。"
    ),
}


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
    items = dict(parser.items(CONFIG_SECTION))
    for key, hint in sorted(_REMOVED_KEYS.items()):
        if key in items:
            raise ValueError(f"{path} 里的 {key} 已不再生效:{hint}")
    return items


@lru_cache
def get_settings() -> Settings:
    return Settings(**_load_ini())


settings = get_settings()

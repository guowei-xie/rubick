# 拉比克 Rubick 2.0

面向不懂 SQL 的业务人员的自助取数平台:管理员 / 开发者在产品内把带变量的 SQL 建成「取数任务」并上线,
业务人员填参数、在线运行、下载结果。飞书登录、权限管控、全链路审计。

> 术语:产品内的「任务」即一条可复用的取数 SQL(代码里是 `SqlTemplate` / 模板);
> 状态为 草稿 / 已上线 / 已下线(下线后进「回收站」;草稿也可收进回收站,
> 出来时可选「恢复为草稿」或「重新上线」)。

## 功能速览

**任务列表** —— 所有取数任务的唯一入口。卡片上直接看到**任务编号**(`#128`,点一下复制纯数字)、
状态(草稿 / 已上线 / 已下线)、数据源、作者与被授权人;
业务用户点卡片即填参取数,管理者在 ⋮ 菜单里编辑 / 上线 / 下线,右下 **+** 给人授权。
任务名可以重名,编号不会 —— 任务列表的搜索框支持按**编号精确定位**(把复制来的数字粘进去即可),
也照旧按任务名 / 作者 / 团队 / 被授权人模糊搜;编号与审计日志里的 `任务 #12` 是同一个。
任务多起来可切**列表视图**(右上⊞/☰,选择记在本机):同一批任务换成一屏几十行的表格,
第一列就是编号、列头可排序,动作与卡片完全一致。已上线却**超过 90 天没人跑**的任务(阈值可配)会标出闲置天数、卡片降噪并排到末尾,
任务列表标题栏多一枚「闲置」筛选片一键筛出来 —— 只给有管理权的人看,业务侧无感。

![任务列表](docs/assets/manual-tasks.png)

**填参取数 → 预览 → 下载** —— 变量按 SQL 写法自动渲染成表单(单值输入框 / 值列表多选)。值列表打开即带出
**任务内共享的候选值**(作者预置或上手动更新过的,不现跑查询),任何使用者可一键「更新枚举值」、结果全员共用;
也支持上传粘贴批量输入。运行完先看前 50 行,确认无误再下载完整 CSV。

![填参取数与结果预览](docs/assets/manual-run-preview.png)

**任务编辑器** —— 选团队、选数据源、贴 SQL,`:变量` 自动识别成变量卡(`字段 IN (:x)` → 值列表,其余 → 单值);
可「SQL预览」只渲染不执行、「测试运行」用测试值真跑一次,再保存上线。

![任务编辑器](docs/assets/manual-task-editor.png)

**团队协作** —— 任务归属团队:同团队成员互相**可见**,但默认只能编辑自己建的;团队管理员可编辑本团队
全部任务,也可按任务把编辑权授予某个成员(团队页「任务编辑权」可按 任务名 / 编号 / 作者 / 可编辑的人 搜)。不同团队的任务互相不可见;平台管理员不受团队约束。
团队由管理员创建并指定团队管理员,成员只能是开发者。开发者必须先有团队才能建任务。
人员变动时,任务作者可在 ⋮ 菜单**转移**给同团队的在职成员(作者本人、团队管理员、平台管理员都能做),
一次交接一批走列表右上角的「批量交接」(先选接手人 → 不能转的置灰并说明 → 全成功才生效、通知按人合并),
新旧双方各收一条通知 —— 离职交接不必再靠改库。

**按团队隔离取数权限** —— 每个团队在各数据源上登记一套库账号(由团队管理员配置),
任务就用**所属团队**的账号取数:越权与否交由数据库裁决,平台不必复刻一套数据权限。
没登记账号则任务不允许上线、也不允许运行,**不会静默回退到公共账号**;「测试连接」只是可选的
自检,没点过的账号照样能跑。密码加密存储、任何人都读不到(管理员也只能看状态与回收);
库账号名只对团队管理员可见,面向业务方的报错还会把它脱敏。
管理员在「取数账号」页看全平台覆盖矩阵与「此刻缺账号的已上线任务」。

**运营分析** —— 平台被用得怎么样,一页看完:采纳与活跃(取数量、活跃人数、业务自助率、复用倍数)、
运行健康(按来源分开的成功率、失败归因分桶、执行耗时与**排队等待**分位数)、任务资产(闲置、从未被运行、
头部与长尾)、权限与配置(**授权了却从没跑过**的空转授权、失效的编辑权、缺账号的已上线任务)。
**团队管理员也进得来**,但只看得到自己那个团队 —— 页头常驻一条数据范围说明,平台级内容整块不下发;
平台管理员可以下拉切到任意团队下钻。数字上带「此刻」标记的不吃时间范围,灰色 `—` 是「还没有数据」、
实打实的 `0` 才是「跑了很多次一次没出问题」。能点的数字都能下钻到任务列表 / 取数账号 / 审计。

![运营分析](docs/assets/manual-analytics.png)

**审计日志** —— 登录、提交/运行/失败取数(含**用了哪个团队的库账号**)、下载、任务增改与上下线、
枚举候选值更新、授权变更、任务编辑权变更、任务转移团队、任务转移作者、团队与成员变更、角色变更、数据源增改删、
团队取数账号增改删全部留痕,
可按动作 / 资源 / 操作人 / 时间检索并导出 CSV(导出本身也留痕)。

![审计日志](docs/assets/manual-audit.png)

> 以上截图取自演示实例,数据均为合成示例值。
> **需要完整操作说明**(登录、权限、参数填写、运行记录与通知、管理员页面、口径限制、FAQ)请看用户手册:
> [docs/user-manual.md](./docs/user-manual.md) ·
> [飞书在线版](https://wrpnn3mat2.feishu.cn/docx/ZlYBdS3fGoBosXx5btpcWs8yn8Y)
>
> **建任务的同学**(开发者角色)另有一份手册:变量设计、枚举候选值、SQL 安全网关、版本与上线、
> 排障速查与上线前自检清单 —— [docs/developer-manual.md](./docs/developer-manual.md) ·
> [飞书在线版](https://wrpnn3mat2.feishu.cn/docx/SGh0dCub5oRBLpxkiq0cB0X3nrd)

## 技术栈

- 后端:FastAPI + SQLAlchemy + MySQL(平台元数据 / 业务库)
- 前端:React + Vite + Ant Design(构建为静态文件)
- 异步取数:独立 DB 轮询 worker(**不依赖 Redis/Celery**),并发度由 `WORKER_CONCURRENCY` 决定
- 结果存储:本地文件系统(**不依赖 MinIO/对象存储**)
- **无需 Docker**

## 单机部署

下面是从 `git clone` 到启动服务的完整步骤。**单端口部署**:后端 uvicorn 同时托管前端静态产物
(`frontend/dist`)与 `/api`,一个端口即完整应用,无需额外的 Web 服务器组件。

### 0. 前置依赖

- Python 3.9+(推荐 3.11+)
- Node.js 18+(推荐 20+)与 npm —— 前端用 Vite 6,Node 16 无法构建
- 一个可访问的线上 MySQL(平台自身的表会带 `rubick_` 前缀,可安全地与其它系统共用一个库)

### 1. 拉取代码

```bash
git clone https://git.corp.hetao101.com/hetao_growth/rubick.git
cd rubick
```

### 2. 准备配置

```bash
cp backend/config.example.ini backend/config.ini
```

编辑 `backend/config.ini`,至少修改:

- `DATABASE_URL`:线上 MySQL 连接串,如 `mysql+pymysql://用户:密码@主机:3306/库名`
- `ALLOW_REMOTE_DB = true`:**线上这台必须打开**。`DATABASE_URL` 指向本机以外的库时,API / worker / migrate 一律拒绝启动(默认 `false`,挡的是开发机连线上库 —— 那样的 worker 会替线上认领并执行真实取数,结果文件落在开发机上)。本机开发与测试用 sqlite(`DATABASE_URL = sqlite:///./rubick.db`)时不必管这一项
- `JWT_SECRET`:改成随机长字符串(如 `openssl rand -hex 32`)
- `DATA_DIR`:平台数据产物的根目录(取数结果 CSV 在 `results/`、维护脚本的行级备份在 `backups/`)。默认 `data`(即 `backend/data`),**生产请指到独立数据盘**,如 `DATA_DIR = /data/rubick` —— 结果体积由业务用量决定,单份可上百 MB,不该去吃系统盘的余量。日后要换盘:先建新目录、把旧目录内容整体搬过去(库里存的是相对路径,不用改任何一行)、再改这一行并重启
- `BACKEND_HOST` / `BACKEND_PORT`:后端监听地址与端口(单端口部署下 SPA 与 `/api` 都走这个端口)。前面挂了反向代理时设成 `127.0.0.1`,不要用 `0.0.0.0` 把端口直接暴露到公网
- `APP_BASE_URL`:应用对外访问地址(**单一来源**)。本机单端口可留空,自动派生为 `http://localhost:BACKEND_PORT`;独占域名填 `https://rubick.example.com`;挂在网关子路径下则填到子路径为止(如 `https://htba.example.com/rubick`)。`FEISHU_REDIRECT_URI`、`FRONTEND_ORIGIN` 与前端构建的基路径都自动跟随它
- 接入飞书时:`MOCK_AUTH=false` 并填 `FEISHU_APP_ID/SECRET`;飞书开发者后台的「重定向 URL」需与 `{APP_BASE_URL}/auth/callback` 逐字一致
- `FEISHU_APP_APPLY_URL`:飞书应用的**分享链接**(在飞书里把本应用分享给别人时拿到的那条 `https://applink.feishu.cn/...`)。没被授予这个应用的人点「飞书登录」会被飞书挡回来,平台这边帮不上忙 —— 填上之后,登录页会多一行自助申请入口(登出也看得到),任务列表页「使用文档」旁会多一枚「申请链接」(仅开发者 / 管理员可见,点一下复制成一整句,粘进聊天框就能发给对方)。留空 = 两处入口都不出现
- 冷启动管理员:`BOOTSTRAP_ADMINS=你的飞书 open_id`(该账号首次登录自动成为管理员;服务启动时也会对库中已有用户提权一次)。也支持填邮箱,但**推荐 open_id** —— 它是用户表的 upsert 主键、伪造不了,而邮箱是从通讯录补齐进来的普通字段
- 首次上线后(或刚给飞书应用开通邮箱权限后)跑一次 `python -m app.backfill_user_emails --apply`,给存量用户补齐邮箱;新用户在登录/被授权时会自动补,无需再跑

> `MOCK_AUTH` 默认**关闭**(生产安全默认)。本地暂无飞书凭证、想先跑起来时,才临时改成 `true`
> 用 mock 登录;它信任前端传入的 open_id、不校验凭证,生产务必保持 `false`。
> 另有一道护栏:mock 登录会按传入的 open_id 建号,所以只有 `DATABASE_URL` 指向**本地库**
> (sqlite,或 localhost / 回环地址上的 MySQL)时才允许开 —— 连着远端库开这个开关,
> 服务**直接拒绝启动**并说明原因,免得把 `ou_admin` 之类的假账号灌进正式库。

> **取数身份没有配置项**:任务一律用所属团队的库账号取数(由团队管理员在团队页配置)。
> 团队账号未登记时,该团队在该数据源上的任务不允许上线、也拒绝运行(「测试连接」是可选自检,
> 未验过的账号不受影响)。因此**首次上线团队功能后,请先让各团队把账号配齐**,
> 再看管理员的「取数账号」页清零。
> 存量任务会被迁移脚本并入一个「默认团队」(全体开发者/管理员自动入队),上线当天体感无变化;
> 之后再由管理员按实际组织拆分。

### 3. 一键初始化部署

```bash
./deploy.sh init
```

该命令会依次:创建 Python 虚拟环境并装依赖 → 初始化平台元数据表(带 `rubick_` 前缀,幂等)
→ 安装前端依赖并构建 `frontend/dist` → 用 nohup 后台启动**后端 API** 与**取数 worker**。

- 进程 PID:`backend/run/api.pid`、`backend/run/worker.pid`
- 运行日志:`backend/logs/api.log`、`backend/logs/worker.log`

### 4. 常用运维命令

```bash
./deploy.sh status     # 查看 API / worker 运行状态
./deploy.sh logs       # 跟踪 api.log 与 worker.log(可带行数,默认 100)
./deploy.sh restart    # 重启
./deploy.sh stop       # 停止
./deploy.sh update     # 一键更新:git pull → 装依赖 → 建表 → 重建前端 → 排空 → 停旧起新 → 健康检查
```

> `update` **不是无停机滚动更新**:它先停旧进程再起新的,重启期间有几秒到十几秒不可用。
> 但**不会打断正在跑的取数**:`update` / `restart` 停进程之前先排空 —— 只停 worker
> (SIGTERM 对它就是「停止认领新任务、等在跑的跑完」),同时播报还在等谁,清零了才继续。
> **等不到就中止部署**:线上留在旧版本上、任务没被打断,由操作人决定再等还是
> `./deploy.sh update --force`(强停的代价是那些取数变成「运行中断,请重新运行」)。
> 单独 `./deploy.sh stop` 不排空(显式停机是操作人的明确决定),但会当场列出被打断的是谁。
> 排空的等待上限与孤儿回收同一把尺;systemd 模式下还要求 `rubick-worker.service` 的
> `TimeoutStopSec` 已同步到 `/etc/systemd/system/`(没同步时脚本会黄字提醒)。
>
> `start` / `restart` / `update` 拉起进程后会连续请求 `/health`(最多 20 次、每次间隔 1 秒),
> 不通就红字退出并贴出 `api.log` 末 40 行 —— 不做这一步的话,一个因配置写错而反复重启的服务
> 会被报成"完成"。**注意闸门失败时站点是停着的**:旧进程已经停了、新进程没起来,该做的是照
> 那 40 行改配置或回滚上一版,不是"等等看"。

### 5. 开机自启(长期运行的机器建议开)

装上 `deploy/systemd/` 里的两个 unit,即可让 API 与 worker **随机器启动自动恢复、崩溃自动拉起**:

```bash
sudo install -m 644 deploy/systemd/rubick-api.service    /etc/systemd/system/
sudo install -m 644 deploy/systemd/rubick-worker.service /etc/systemd/system/
sudo install -m 644 deploy/systemd/rubick.logrotate      /etc/logrotate.d/rubick
sudo systemctl daemon-reload
sudo systemctl enable --now rubick-api.service rubick-worker.service
```

装好后上面那些 `./deploy.sh` 命令**自动改走 systemctl**(不再 nohup),用法不变。unit 的
`ExecStart` 走 `python -m app.serve`,监听地址直接读 `config.ini`,改端口无需动 unit;
细节(日志轮转、常用命令)见 [deploy/systemd/README.md](deploy/systemd/README.md)。

### 6. 访问应用

`./deploy.sh init` 后,直接访问 `APP_BASE_URL`(默认 `http://localhost:BACKEND_PORT`)即可——
SPA 与 `/api`、`/health` 都由后端这一个端口提供,无需额外组件。

### 7. 挂到域名 / 子路径下

单端口部署本身已可用。若前面挂了反向代理(HTTPS 终止、自定义域名、与其它站点共用 80/443),
前端仍由后端托管,代理只需把请求(含 `/api`)整体转发到后端端口,**无需单独托管 `frontend/dist`**;
同时把 `BACKEND_HOST` 改成 `127.0.0.1`,只留代理对外。

配置侧只有一个开关:`APP_BASE_URL`。独占域名填 `https://rubick.example.com`;挂在网关子路径下则
填到子路径为止(如 `https://htba.example.com/rubick`,代理需剥掉 `/rubick` 前缀再转发,
后端路由无需任何改动)。前端产物的资源前缀与路由 basename 由 `deploy.sh` 据此派生
(见 `settings.BASE_PATH`),飞书回调地址同样自动跟随——记得去飞书开发者后台把新的
`{APP_BASE_URL}/auth/callback` 加进「重定向 URL」白名单。

代理侧另需放宽两项限制:请求体上限(结果文件下载 / 批量参数上传,建议 500m)与读超时
(Hive 等长耗时取数,建议 3600s)。

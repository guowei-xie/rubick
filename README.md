# 拉比克 Rubick 2.0

面向不懂 SQL 的业务人员的自助取数平台:管理员 / 开发者在产品内把带变量的 SQL 建成「取数任务」并上线,
业务人员填参数、在线运行、下载结果。飞书登录、权限管控、全链路审计。

> 术语:产品内的「任务」即一条可复用的取数 SQL(代码里是 `SqlTemplate` / 模板);
> 状态为 草稿 / 已上线 / 已下线(下线后进「回收站」,可重新上线)。

> 产品需求见 [PRD.md](./PRD.md)。

## 功能速览

**任务列表** —— 所有取数任务的唯一入口。卡片上直接看到状态(草稿 / 已上线 / 已下线)、数据源、作者与被授权人;
业务用户点卡片即填参取数,管理者在 ⋮ 菜单里编辑 / 上线 / 下线,右下 **+** 给人授权。

![任务列表](docs/assets/manual-tasks.png)

**填参取数 → 预览 → 下载** —— 变量按 SQL 写法自动渲染成表单(单值输入框 / 值列表多选,值列表可「获取枚举值」
或上传粘贴批量输入),运行完先看前 50 行,确认无误再下载完整 CSV。

![填参取数与结果预览](docs/assets/manual-run-preview.png)

**任务编辑器** —— 选数据源、贴 SQL,`:变量` 自动识别成变量卡(`字段 IN (:x)` → 值列表,其余 → 单值);
可「SQL预览」只渲染不执行、「测试运行」用测试值真跑一次,再保存上线。

![任务编辑器](docs/assets/manual-task-editor.png)

**审计日志** —— 登录、提交/运行/失败取数、下载、任务增改与上下线、授权变更、角色变更、数据源增改删全部留痕,
可按动作 / 资源 / 操作人 / 时间检索并导出 CSV(导出本身也留痕)。

![审计日志](docs/assets/manual-audit.png)

> 以上截图取自演示实例,数据均为合成示例值。
> **需要完整操作说明**(登录、权限、参数填写、运行记录与通知、管理员页面、口径限制、FAQ)请看用户手册:
> [docs/user-manual.md](./docs/user-manual.md) ·
> [飞书在线版](https://wrpnn3mat2.feishu.cn/docx/ZlYBdS3fGoBosXx5btpcWs8yn8Y)

## 技术栈

- 后端:FastAPI + SQLAlchemy + MySQL(平台元数据 / 业务库)
- 前端:React + Vite + Ant Design(构建为静态文件)
- 异步取数:独立 DB 轮询 worker(**不依赖 Redis/Celery**)
- 结果存储:本地文件系统(**不依赖 MinIO/对象存储**)
- **无需 Docker**

## 单机部署

下面是从 `git clone` 到启动服务的完整步骤。**单端口部署**:后端 uvicorn 同时托管前端静态产物
(`frontend/dist`)与 `/api`,一个端口即完整应用,**无需 nginx**。对外要 HTTPS/自定义域名时,
可选在前面加一层 nginx 反代(见文末「可选:nginx 反代」)。

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
- `JWT_SECRET`:改成随机长字符串(如 `openssl rand -hex 32`)
- `BACKEND_HOST` / `BACKEND_PORT`:后端监听地址与端口(单端口部署下 SPA 与 `/api` 都走这个端口)。前面挂了 nginx 时设成 `127.0.0.1`,不要用 `0.0.0.0` 把端口直接暴露到公网
- `APP_BASE_URL`:应用对外访问地址(**单一来源**)。本机单端口可留空,自动派生为 `http://localhost:BACKEND_PORT`;独占域名填 `https://rubick.example.com`;挂在网关子路径下则填到子路径为止(如 `https://htba.example.com/rubick`)。`FEISHU_REDIRECT_URI`、`FRONTEND_ORIGIN` 与前端构建的基路径都自动跟随它
- 接入飞书时:`MOCK_AUTH=false` 并填 `FEISHU_APP_ID/SECRET`;飞书开发者后台的「重定向 URL」需与 `{APP_BASE_URL}/auth/callback` 逐字一致
- 冷启动管理员:`BOOTSTRAP_ADMINS=你的飞书邮箱`(该账号首次登录自动成为管理员)

> `MOCK_AUTH` 默认**关闭**(生产安全默认)。本地暂无飞书凭证、想先跑起来时,才临时改成 `true`
> 用 mock 登录;它信任前端传入的 open_id、不校验凭证,生产务必保持 `false`。

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
./deploy.sh restart    # 重启
./deploy.sh stop       # 停止
./deploy.sh update     # 滚动更新:git pull → 装依赖 → 建表 → 重建前端 → 重启
```

### 5. 访问应用

`./deploy.sh init` 后,直接访问 `APP_BASE_URL`(默认 `http://localhost:BACKEND_PORT`)即可——
SPA 与 `/api`、`/health` 都由后端这一个端口提供,无需额外组件。

### 可选:nginx 反代

单端口部署本身已可用;仅当需要 HTTPS 终止、自定义域名或与其它站点共用 80/443 时,
才在后端前面加一层 nginx。前端仍由后端托管,nginx 只需把请求(含 `/api`)反代过去,
**无需单独托管 `frontend/dist`**。此时把 `BACKEND_HOST` 改成 `127.0.0.1`,只留 nginx 对外。

**独占域名**——整站反代到后端:

```nginx
location / {
    proxy_pass http://127.0.0.1:18091;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

**挂在网关子路径下**(如 `https://htba.example.com/rubick/`)——`proxy_pass` 结尾带 `/`
即剥掉路径前缀转发,后端路由无需任何改动:

```nginx
location = /rubick { return 301 /rubick/; }
location /rubick/ {
    proxy_pass http://127.0.0.1:18091/;   # 结尾的 / 不能省,它负责剥掉 /rubick 前缀
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 500m;            # 结果文件下载 / 批量参数上传
    proxy_read_timeout 3600s;             # Hive 等长耗时取数
}
```

子路径部署只需把 `APP_BASE_URL` 填成 `https://htba.example.com/rubick`:
前端产物的资源前缀与路由 basename 由 `deploy.sh` 据此派生(见 `settings.BASE_PATH`),
飞书回调地址同样自动跟随——记得去飞书开发者后台把新的
`{APP_BASE_URL}/auth/callback` 加进「重定向 URL」白名单。

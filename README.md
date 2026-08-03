# 拉比克 Rubick 2.0

面向不懂 SQL 的业务人员的自助取数平台:管理员在产品内编写并发布带变量的 SQL,
业务人员填参数、在线运行、下载结果。飞书登录、权限管控、全链路审计。

> 产品需求见 [PRD.md](./PRD.md)。

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
- Node.js 16+(推荐 18+)与 npm
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
- `BACKEND_HOST` / `BACKEND_PORT`:后端监听地址与端口(单端口部署下 SPA 与 `/api` 都走这个端口)
- `APP_BASE_URL`:应用对外访问地址(**单一来源**)。本机单端口可留空,自动派生为 `http://localhost:BACKEND_PORT`;服务器部署填对外地址(如 `https://rubick.example.com`)。`FEISHU_REDIRECT_URI` 与 `FRONTEND_ORIGIN` 留空即自动跟随它
- 接入飞书时:`MOCK_AUTH=false` 并填 `FEISHU_APP_ID/SECRET`;飞书开发者后台的「重定向 URL」需与 `{APP_BASE_URL}/auth/callback` 逐字一致
- 冷启动管理员:`BOOTSTRAP_ADMINS=你的飞书邮箱`(该账号首次登录自动成为管理员)

> `MOCK_AUTH` 默认开启,未接飞书时可用 mock 登录先跑起来。

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
才在后端前面加一层 nginx,把所有请求(含 `/api`)反代到 `BACKEND_HOST:BACKEND_PORT` 即可
(此时前端仍由后端托管,无需单独让 nginx 托管 `frontend/dist`)。nginx 配置不在本文范围。

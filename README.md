# 拉比克 Rubick 2.0

面向不懂 SQL 的业务人员的自助取数平台。商分部门在产品内编写并发布带变量的 SQL,
业务人员填参数、在线运行、下载结果。飞书登录、权限管控、全链路审计。

> 产品需求见 [PRD.md](./PRD.md)。本仓库是 **Phase 1 MVP 骨架**。

## 已包含

**Phase 1**
- 飞书 OAuth 登录(含 **mock 登录**,无飞书凭证也能开发)
- 商分工作台:写 SQL、选数据源、定义变量、**测试运行**、发布(生命周期 + 版本)
- 业务用户:浏览有权限的已发布模板 → 填参 → 运行 → 下载 CSV
- 数据源连接器:**MySQL 打通**,Hive 接口就绪(直连 HiveServer2)
- SQL 安全网关(仅单条 SELECT)+ 参数化绑定
- 权限:个人 / 部门授权;基础审计(运行 / 下载 / 登录留痕)
- 结果存 MinIO,时效签名下载链接

**Phase 2(部分)**
- **异步取数**:`/run` 入队(Celery + Redis),worker 后台执行,前端轮询任务状态
- **完成通知**:任务跑完/失败 → 飞书机器人推送(未配置飞书时回退**站内通知铃铛**)

**未含(后续)**:RBAC 角色、字段/行级脱敏、结果水印、需求单模块、定时取数。

## 技术栈

- 后端:FastAPI + SQLAlchemy + MySQL(平台元数据)+ Celery(异步)
- 前端:React + Vite + Ant Design
- 依赖中间件:MySQL / Redis / MinIO

## 快速开始

### 1. 起中间件

方式 A(Docker):

```bash
docker compose up -d          # MySQL(3306) + Redis(6379) + MinIO(9000/9001)
```

方式 B(macOS Homebrew,本机即用此方式):

```bash
brew install mysql minio redis
brew services start mysql minio redis
# 首次创建平台账号与库:
mysql -u root -e "CREATE DATABASE IF NOT EXISTS rubic_meta CHARACTER SET utf8mb4;
  CREATE USER IF NOT EXISTS 'rubic'@'localhost' IDENTIFIED BY 'rubic';
  GRANT ALL PRIVILEGES ON *.* TO 'rubic'@'localhost'; FLUSH PRIVILEGES;"
```

### 2. 后端

> Python 3.9+ 均可(已通过 `eval_type_backport` 兼容 3.9;推荐 3.11+)。

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 默认即可跑通(MOCK_AUTH=true)

python -m app.seed            # 建表 + 造演示数据(用户/数据源/已发布模板/授权)
uvicorn app.main:app --reload # http://localhost:8000  (文档 /docs)
```

### 2.5 启动 Celery worker(异步取数)

另开一个终端(与后端同一 venv):

```bash
cd backend && source .venv/bin/activate
OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES celery -A app.tasks worker --loglevel=info --pool=solo
```

> 无 Redis/worker 时,可在 `.env` 设 `ASYNC_QUERY=false`,取数将在请求内同步执行(便于开发)。

### 3. 前端

```bash
cd frontend
npm install
npm run dev                   # http://localhost:5173
```

## 端到端体验(验证 Phase 1)

打开 http://localhost:5173,用 mock 登录选择身份:

| 身份 | open_id | 能做什么 |
|------|---------|----------|
| 业务小C | `ou_viewer` | 在「取数模板」看到已授权的《按日期查订单》→ 填起始日期 → 运行 → 下载 CSV |
| 商分小B | `ou_analyst` | 「商分工作台」新建/编辑 SQL、测试运行、发布 |
| 管理员小A | `ou_admin` | 「数据源 / 权限 / 审计」:配数据源、给用户或部门授权、查审计日志 |

推荐走一遍:
1. `ou_viewer` 登录 → 取数模板 → 填参运行 → 下载,拿到 5 行订单 CSV。
2. `ou_admin` 登录 → 审计 → 看到刚才的 `run_query` 与 `download` 记录。
3. `ou_analyst` 登录 → 工作台 → 新建一个模板 → 测试运行 → 发布。
4. `ou_admin` → 权限 → 把新模板授权给市场部 → 换 `ou_viewer` 就能看到并运行。

## 接入真实环境

- **飞书**:`.env` 设 `MOCK_AUTH=false` 并填 `FEISHU_APP_ID/SECRET`;实现
  `app/services/feishu_service.py` 中的通讯录同步。
- **Hive**:`pip install "pyhive[hive]" thrift`,在「数据源」新增 engine=hive 的源;
  Kerberos/LDAP 认证参数放数据源 `extra`(见 PRD 开放项)。
- **生产数据源**:务必使用**只读账号**;数据源密码改为密钥管理引用。

## 目录

```
backend/   FastAPI:core / models / connectors / services / api / seed
frontend/  React:pages(templates/jobs/studio/admin) + api + auth
PRD.md     产品需求文档
```

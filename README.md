# 拉比克 Rubick 2.0

面向不懂 SQL 的业务人员的自助取数平台。商分部门在产品内编写并发布带变量的 SQL,
业务人员填参数、在线运行、下载结果。飞书登录、权限管控、全链路审计。

> 产品需求见 [PRD.md](./PRD.md)。本仓库是 **Phase 1 MVP 骨架**。

## 已包含

**Phase 1**
- 飞书 OAuth 登录(含 **mock 登录**,无飞书凭证也能开发)
- **统一任务列表**:一张表承载全部「取数任务」,按角色收窄可见范围、按能力标记
  操作(填参取数 / 代码编辑 / 授权 / 发布下线 / 运行记录),取代旧的「工作台 +
  模板 + 记录」三页分离
- 商分:任务编辑器里写 SQL、选数据源、**解析变量**、定义参数、**测试运行**、
  存草稿 / 确认上线(生命周期 + 版本)
- 业务用户:在任务列表看到被授权的已发布任务 → 填参 → 运行 → 下载 CSV
- 参数=**枚举/列表筛选**:业务可直接输入、「获取枚举值」勾选、上传/粘贴名单、或「全选」
  (不筛该字段);SQL 里 `:变量` 是值列表占位符(详见下方「SQL 变量写法」)
- 数据源连接器:**MySQL 打通**,Hive 接口就绪(直连 HiveServer2);**SQL 方言由
  数据源引擎自动决定**,无需手填
- SQL 安全网关(仅单条 SELECT)+ 参数化绑定
- 权限:任务级「授权」入口,按人 / 部门授权(view/run/download),名单按主体归并展示;
  基础审计(运行 / 下载 / 登录留痕)
- 结果存 MinIO,时效签名下载链接 + 运行记录内**在线预览**(前 50 行)

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

| 身份 | open_id | 在「任务列表」里能做什么 |
|------|---------|----------|
| 业务小C | `ou_viewer` | 只看到被授权的已发布任务;《按日期查订单》→「填参取数」→ 填起始日期 → 运行 → 自动下载 CSV;「运行记录」查看/预览自己跑过的 |
| 商分小B | `ou_analyst` | 「新建任务」/「代码编辑」写 SQL、解析变量、测试运行、存草稿或上线;对自己的任务可「授权」「发布/下线」 |
| 管理员小A | `ou_admin` | 看到全部任务,可编辑/授权/发布任意任务;顶栏另有「用户管理 / 数据源 / 审计」 |

推荐走一遍:
1. `ou_viewer` 登录 → 任务列表 →「填参取数」运行 → 自动下载,拿到 5 行订单 CSV;「运行记录」里可在线预览。
2. `ou_admin` 登录 → 审计 → 看到刚才的 `run_query` 与 `download` 记录。
3. `ou_analyst` 登录 → 任务列表 →「新建任务」→ 解析变量 → 测试运行 → 确认上线。
4. `ou_analyst`(或 `ou_admin`)在该任务上点「授权」→ 授给市场部 → 换 `ou_viewer` 就能看到并运行。

## SQL 变量写法(给写 SQL 的人)

SQL 里的 `:变量名` 是一个**值列表占位符**——业务填参时选/输入的一批值,会被参数化绑定后
填进去(**不是字符串拼接,防注入**)。按标准 SQL 写谓词即可,所见即所得:

| 需求 | 这样写 | 业务选了 `a`、`b` 后实际执行 |
|------|--------|------------------------------|
| 正选(包含) | `字段 IN (:xx)` | `字段 IN ('a', 'b')` |
| 反选(排除) | `字段 NOT IN (:xx)` | `字段 NOT IN ('a', 'b')` |
| 反选且保留 NULL 行 | `(字段 NOT IN (:xx) OR 字段 IS NULL)` | `(字段 NOT IN ('a','b') OR 字段 IS NULL)` |

要点:

- **值是带引号的字符串**(`'a'`)。字段是数字列时 Hive/MySQL 会隐式转换,一般没问题;
  极端场景想要裸数字请自行评估。
- **反选 + NULL 陷阱**:`字段 NOT IN (...)` 会把该字段为 `NULL` 的行一起排除(SQL 三值逻辑),
  想保留就用上表第三行的写法。
- `字段 = :xx` 仍兼容(会自动转成 `IN`),但**推荐直接写 `IN` / `NOT IN`**,更直观。
- 多表 / 别名没问题,例如 `t.用户id IN (:uid)`。
- **「全选」**:业务在填参界面点「全选」时,该字段整条谓词会被中和为 `1=1`(即不筛该字段),
  不跑候选 SQL、也不生成大 `IN`——与 `IN` / `NOT IN` 无关。
- 候选值来源(可选):给变量配一段独立的「枚举值获取 SQL」(返回一列),业务点「获取枚举值」
  时跑它列出候选;不配也行,业务可直接输入或上传 `.csv`/`.txt` 名单。

> ⚠️ 超大名单(上万级)目前仍是内联 `IN`,会拖慢/拖垮 Hive;大名单直传(staging 表 + JOIN)
> 待后续按需实现。

## 接入真实环境

- **飞书**:`.env` 设 `MOCK_AUTH=false` 并填 `FEISHU_APP_ID/SECRET`;实现
  `app/services/feishu_service.py` 中的通讯录同步。
- **Hive**:`pip install "pyhive[hive]" thrift`,在「数据源」新增 engine=hive 的源;
  Kerberos/LDAP 认证参数放数据源 `extra`(见 PRD 开放项)。
- **生产数据源**:务必使用**只读账号**;数据源密码改为密钥管理引用。

## 目录

```
backend/   FastAPI:core / models / connectors / services / api(含 tasks 统一列表)/ seed
frontend/  React:pages(TasksPage 统一任务列表 + admin) + components(TaskEditor /
           RunDrawer / RunRecordsDrawer / GrantModal)+ api + auth
PRD.md     产品需求文档
```

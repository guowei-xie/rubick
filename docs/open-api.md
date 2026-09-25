# 拉比克 Rubick 开放 API 文档

> 面向需要通过脚本或 AI Agent 调用 Rubick 取数能力的开发者。
> 给 AI Agent 用的现成 skill 文件：`https://<host>/rubick-skill.md`，下载后下发给你的 Agent 即可，不必自己实现这套流程；装好后它会在使用时自动更新到平台最新版，不必重装。

## 1. 概述

**一句话**：列出你可见的任务 → 填参触发一次运行 → 轮询状态 → 预览或下载 CSV。

- **Base URL**：平台地址，如 `https://<host>`，下文写作 `{BASE}`。
- 全部端点挂在 `/api/v1` 下，**只接受 API Token 鉴权**（网页登录态不适用，token 也不能用于网页接口）。
- 请求与响应均为 JSON；唯一的例外是 `GET /result`，返回 CSV 文件流。
- 运行是**异步**的：提交立即返回一个 job，需轮询它的 `status` 至 `success` / `failed`。任务可能跑几分钟到几十分钟。

## 2. 鉴权

```
Authorization: Bearer rk_...
```

- Token 在平台界面生成，**每用户一个**，可随时**重置 / 吊销**；重置后旧 token 立即失效。
- **Token 即本人身份**：API 下的可见范围与权限，和你在网页界面上完全一致（详见 [第 7 章](#7-与-web-界面权限的关系)）。
- Token 只在生成时完整展示一次，请妥善保管；怀疑泄漏请立刻到界面吊销。

## 3. 任务

### 3.1 `GET /api/v1/tasks` —— 我可见的任务

响应：任务数组。口径与网页任务列表一致——你看得到哪些任务，这里就返回哪些。每个元素：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int | 任务编号（与界面上的 `#128` 是同一个数） |
| `name` | string | 任务名（可重名，定位请用 `id`） |
| `description` | string \| null | 任务说明（口径、时间范围限制等） |
| `team_id` | int \| null | 所属团队编号 |
| `team_name` | string \| null | 所属团队名 |
| `status` | string | `draft` 草稿 / `published` 已上线 / `archived` 已下线（回收站） |
| `params` | array | 参数定义数组，见下表 |
| `can_run` | bool | 我是否有运行权限（任务已上线且被授权「运行」） |
| `can_download` | bool | 我是否有下载权限 |
| `allow_api` | bool | 任务是否开放 API 触发；新任务默认 `true`，作者可关掉，为 `false` 时 `POST runs` 一律 403 |

`params` 数组的每个元素：

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 参数名，提交运行时的键 |
| `kind` | string | `single` 单值 / `list` 值列表 |
| `value_type` | string | `text` 文本 / `number` 数值 |
| `label` | string \| null | 参数说明，格式提示通常写在这里（如「开始日期（格式 yyyy-mm-dd）」） |
| `enum` | string[] | 共享候选值（仅配了枚举 SQL 的 list 参数给出）；候选之外的值也允许提交 |

关于 `enum`：**只对 `can_run=true` 的任务给出**——跑不了的任务，候选值对你没有用处，
而这个端点一次返回你可见的全部任务，候选值按「任务数 × 值列表变量数 × 每变量上千个值」
增长。空数组有三种来源，对你来说下一步都一样（问用户要值）：这个变量没配枚举 SQL、
从来没人采集过候选、作者改了枚举 SQL 或换了数据源使旧候选作废。

### 3.2 `POST /api/v1/tasks/{id}/runs` —— 触发一次运行

请求体：

| 字段 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `values` | object | 是 | `{参数名: 值}`。`kind=list` 的参数传**数组**（即使只有一个值）；`value_type=number` 的传数字 |
| `fresh` | bool | 否 | 默认 `false`。`true` = 不复用、不接上在途运行，**一定新跑一次**。只在用户明确要「重跑 / 最新数据」时传 |

- **所有参数必填、没有默认值**，缺参返回 400。
- 任务 `allow_api=false`，或你没有该任务的运行权限 → **403**。
- 成功返回 job 对象（见 [4.1](#41-job-对象)）。**提交成功不代表有结果**，先看 `reuse_kind`：

**复用优先，重跑始终可用。** 权限与参数校验全部通过之后，`fresh=false` 时平台依次尝试：

| `reuse_kind` | 条件 | 返回 |
|---|---|---|
| `"result"` | 时效内已有**相同参数**跑成的结果（判据见 [3.3](#33-post-apiv1tasksidrunsreusable--只查有没有现成结果)） | 一条**新的**运行记录，挂在你名下、`status=success`，与来源共用同一份结果文件；**没有执行**，`started_at` / `duration_ms` 为 null，`reused_from_at` 是那份数据的取数时刻 |
| `"inflight"` | 相同参数的运行正在排队或执行（你看得见的，含同事触发的） | **那一条**运行本身，照常轮询即可 |
| `null` | 以上都没有，或 `fresh=true` | 新建的运行，初始 `status` 通常为 `queued` |

复用只看结果、不放宽权限：下载复用记录照旧要你本人有「下载」权限（4.5）。任务作者可以在编辑器里关掉该任务的「结果可复用」。

**在途上限**：每个 token 同时在排队或执行的 API 运行最多 **3** 条（平台配置 `API_MAX_INFLIGHT_PER_USER`），超出返回 **429**，
报错写明「已有 N 条 API 运行在排队或执行」。它与每分钟限流（第 6 节）是两回事：**退避重试没用**，
要等手上的运行结束，或用 [4.6](#46-delete-apiv1runsjob_id--取消排队中的运行) 取消不再需要的那条。
接上在途运行（`inflight`）不占名额；`fresh=true` 照样受这个上限约束。

```bash
curl -X POST {BASE}/api/v1/tasks/128/runs \
  -H "Authorization: Bearer rk_..." \
  -H "Content-Type: application/json" \
  -d '{"values": {"dt": "2026-09-20", "regions": ["华东", "华南"]}}'
```

### 3.3 `POST /api/v1/tasks/{id}/runs/reusable` —— 只查有没有现成结果

3.2 自己就会优先复用，日常直接提交即可；这个端点给「只想问有没有、不想触发」的场景，也作为兼容保留。

请求体与 [3.2](#32-post-apiv1tasksidruns--触发一次运行) 相同（`{"values": {...}}`，`fresh` 在这里没有意义）。返回：

```json
{ "job": { ...job 对象... } }   // 命中：直接拿 job.id 去 preview / result
{ "job": null }                 // 今天没有能用的：再调 3.2 触发
```

命中条件（全部满足，取最新一条）：

- 你**看得见这个任务**（同任务、同版本、同参数的结果与谁跑无关，所以同事、订阅定时跑出来的都算）；
- 在**复用时效**内：Hive 等 T+1 数仓为**当天**（平台所在时区的自然日）；MySQL 是实时库，只认**最近 30 分钟**
  （平台配置 `RESULT_REUSE_MYSQL_MINUTES`）且不跨天。按开始执行的时刻算；
- 任务开着「结果可复用」（编辑器里的开关，默认开）；
- 跑的是任务**当前上线的版本**——任务改过 SQL 重新上线后，旧版本的结果不算；
- **参数相同**：按参数定义归一后比较，值列表参数不看顺序；
- `status=success`、不是作者的试跑，本身不是复用或补推出来的记录，且结果还在（没过保留期、文件没丢）。

说明：

- 这是给「要不要重跑」做判断用的：同参结果今天已经有了，重跑只是白占队列。**job 对象不带参数**，
  所以别自己在 `GET /runs` 里按时间挑——比对只能由平台做。
- 只查不跑，**不要求**运行权限和 `allow_api`；取文件时的下载权照旧由 4.5 把关。
- 参数不合法（缺参、需为数值等）与 3.2 同样返回 **400**；任务不可见返回 **404**。

## 4. 运行

### 4.1 job 对象

`POST runs` 与 `GET /runs/{job_id}` 返回的 job：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int | 运行编号（每跑一次给一个的流水号） |
| `template_id` | int | 所属任务编号 |
| `status` | string | `queued` 排队 / `running` 运行中 / `success` 成功 / `failed` 失败 / `cancelled` 排队中被取消（见 4.6） |
| `row_count` | int \| null | 成功时的结果行数 |
| `duration_ms` | int \| null | 查询执行到结果落盘的耗时，**不含排队等待**；仅成功时有值 |
| `error` | string \| null | 失败原因（面向使用者的报错，库账号名已脱敏） |
| `queue_ahead` | int \| null | 排在前面还有几个任务；**仅 `status=queued` 时有值** |
| `source` | string | 触发来源：API 触发为 `api`（网页取数 `run`、试跑 `test`、订阅定时 `subscribe`） |
| `created_at` | string | 提交时间（ISO 8601） |
| `started_at` | string \| null | 开始执行时间；还没开跑时为 null |
| `result_expired` | bool | 结果是否已过保留期。从 `GET /runs` 里挑历史运行下载前先看它，省一次注定 404 的请求；它只回答保留期，文件因其它原因不在时仍可能 404 |
| `reuse_kind` | string \| null | `result` = 这条记录复用了现成结果、没有执行（列表里也有）；`inflight` = 这次提交接上了在途运行（**只出现在 3.2 的响应里**）；null = 正常执行。见 3.2 |
| `reused_from_at` | string \| null | 复用时：那份数据是什么时候取的（来源运行开始执行的时刻）。应告诉用户 |

### 4.2 `GET /api/v1/runs` —— 我的运行记录列表

返回你**看得见的**运行记录数组（job 对象），按提交时间倒序，最多 100 条。
与网页「运行记录」同一口径：你自己发起的，加上你所属团队任务下的全部运行（含他人发起的），
以及你被授权的任务上的定时运行。所以这里**不只有你自己跑的**——要认自己的那些，看 `id`。

### 4.3 `GET /api/v1/runs/{job_id}` —— 查询运行状态

轮询直到 `status` 为 `success` / `failed` / `cancelled`。**建议指数退避：5 秒起步、每次翻倍、上限 60 秒**，不要打满限流频率。`status=queued` 时可用 `queue_ahead` 向用户报告位次。

**轮询是唯一的完成信号。** API 触发的运行**不推送任何通知**——没有飞书消息，平台站内的通知铃里也不会多出一条，成功失败都一样：失败原因在这里的 `error` 字段里。（网页上点「运行」照旧有通知，静音只看触发来源。）

### 4.4 `GET /api/v1/runs/{job_id}/preview` —— 预览前 50 行

| 字段 | 类型 | 说明 |
|---|---|---|
| `columns` | string[] | 列名（即 CSV 表头） |
| `rows` | array[] | 前 50 行数据 |
| `row_count` | int | 总行数 |

仅 `status=success` 且结果未过期时可用。

### 4.5 `GET /api/v1/runs/{job_id}/result` —— 下载完整 CSV

- 返回 `text/csv` 文件流（UTF-8 BOM，Excel 直接打开不乱码），文件名 `<任务名>_<运行时间 YYYYmmddHHMMSS>.csv`（如 `日报_20260925202030.csv`）。
- 需要「下载」权限（任务对象上的 `can_download`），否则 403。403 的报错会说清该找谁授权；
  **重试无意义**。订阅推送给你的那几期结果除外——订阅本身就带着取走它的资格。
- **结果文件保留 7 天**，过期返回 404，需重新运行。`preview` 同样受保留期约束。

### 4.6 `DELETE /api/v1/runs/{job_id}` —— 取消排队中的运行

- 只能取消**你自己**提交的、**还在排队**（`status=queued`）的运行；成功返回 job 对象，`status=cancelled`，它不会再被执行。
- 已经开始执行或已经结束 → **409**（执行中的查询不支持取消）；别人的运行或不存在 → **404**。
- 用途：用户已经不要这份数了，或要腾出在途名额（3.2）。

## 5. 错误码

| 状态码 | 场景 |
|---|---|
| 400 | 参数缺失或取值不合法（如数值型参数收到非数字） |
| 401 | 未带 token、token 无效或已吊销 |
| 403 | 任务 `allow_api=false`；或没有该任务的运行 / 下载权限 |
| 404 | 任务 / 运行不存在、对你不可见，或结果已过保留期被清理（重跑即可，别改参数重发） |
| 409 | 取消一条已经开始执行或已经结束的运行（4.6） |
| 429 | 超过每分钟限流（第 6 节，退避重试）；或提交运行时超过在途上限（3.2，**重试无用**，等运行结束或取消一条） |

## 6. 限流

**120 请求/分钟/token**，超限返回 429。轮询状态请按退避节奏（5 秒起步、上限 60 秒）而不是打满频率——一个正常轮询的调用方远达不到这个上限。

另有两道**针对队列**的约束（都是平台配置，见 `config.example.ini`）：

- **在途上限**：每 token 同时在排队或执行的 API 运行最多 3 条（`API_MAX_INFLIGHT_PER_USER`，见 3.2）；
- **认领顺序**：worker 先执行网页取数与订阅定时运行，再执行 API 运行（同一档内先到先得）。
  所以 API 运行的 `queue_ahead` 会把排在它前面的网页运行也算进去，后来的网页运行也可能插到它前面。

## 7. 与 Web 界面权限的关系

- **Token = 本人身份。** 网页上能看、能跑、能下载的，API 一样能；网页上没有的权限，API 也没有。授权（查看 / 运行 / 下载）仍由任务作者在网页界面上授予，API 不提供授权入口。
- `can_run` / `can_download` 与界面上的「运行 / 下载」授权是同一口径。
- **`allow_api` 只是运行闸**：它决定这个任务允不允许被 API 触发，**不授予任何人任何权限**。新任务默认开着，作者可随时关掉；要调用某个任务，需要它的 `allow_api` 为 `true`，**并且**你被授权「运行」（下载结果另需「下载」）。
- API 触发的运行在运行记录里 `source=api`，审计日志带 `via=api` 标记，与网页取数区分得开。
- **API 触发不发通知，网页触发照发**（见 4.3）。通知是给在界面上等结果的人用的，而你在轮询；API 又是高频的，不静音会把 token 主人自己在网页上跑的那几条通知一起淹掉。运行记录一条不少，可追溯性不受影响。

## 8. 完整流程示例

```bash
BASE="https://<host>"; TOKEN="rk_..."
H="Authorization: Bearer $TOKEN"

# ① 列出任务，读取参数定义与 allow_api / can_run
curl -s "$BASE/api/v1/tasks" -H "$H"

# ② 提交运行，记下返回的 job.id；reuse_kind=result 说明已复用现成结果（已是 success，直接 ④），
#    inflight 说明接上了在跑的那条。用户要最新数据时在 body 里加 "fresh": true
curl -s -X POST "$BASE/api/v1/tasks/128/runs" \
  -H "$H" -H "Content-Type: application/json" \
  -d '{"values": {"dt": "2026-09-20", "regions": ["华东", "华南"]}}'

# ③ 轮询状态（建议 5s 起步、上限 60s 的指数退避）
curl -s "$BASE/api/v1/runs/12345" -H "$H"

# ④ 成功后：先看前 50 行，或下载完整 CSV
curl -s "$BASE/api/v1/runs/12345/preview" -H "$H"
curl -s "$BASE/api/v1/runs/12345/result" -H "$H" -o result.csv
```

```python
import time
import requests

BASE = "https://<host>"
H = {"Authorization": "Bearer rk_..."}

task = next(t for t in requests.get(f"{BASE}/api/v1/tasks", headers=H).json()
            if t["id"] == 128)
assert task["can_run"] and task["allow_api"], "未授权运行或未开放 API"

job = requests.post(f"{BASE}/api/v1/tasks/{task['id']}/runs", headers=H,
                    json={"values": {"dt": "2026-09-20"}}).json()

delay = 5
while True:
    time.sleep(delay)
    r = requests.get(f"{BASE}/api/v1/runs/{job['id']}", headers=H)
    if r.status_code == 429:                     # 限流，退避
        delay = min(delay * 2, 60)
        continue
    job = r.json()
    if job["status"] in ("success", "failed"):
        break
    delay = min(delay * 2, 60)

if job["status"] == "success":
    csv = requests.get(f"{BASE}/api/v1/runs/{job['id']}/result", headers=H)
    open("result.csv", "wb").write(csv.content)
else:
    print("运行失败：", job["error"])
```

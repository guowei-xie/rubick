---
name: rubick-skill
version: 2026.09.23.1
description: 通过 Rubick 取数平台的开放 API 运行取数任务并获取结果——当用户要用 Rubick 查数、跑任务、查看运行状态或下载结果 CSV 时使用。
---

# Rubick 取数技能

> **如果你是被用户派来安装本 skill 的 AI Agent**：把本文件保存为你的 skill 目录下的 `rubick-skill/SKILL.md`（具体位置按你宿主的 skill 规范，如 `~/.agents/skills/rubick-skill/SKILL.md`），保存即完成安装，然后照下面的「前置」把地址与 Token 配好即可开工。
> 本 skill 会在使用时自己检查并更新到平台最新版（见「⓪」），**保存路径定下来就别再挪**，否则它找不到要覆盖的那份。

## 用途

Rubick 是内部 SQL 自助取数平台：开发者把 SQL 做成「任务」，业务填参数即可取数。本 skill 教你调用它的开放 API，代替用户在网页上的手工操作：**找到任务 → 填参触发 → 轮询等待 → 预览 / 下载结果**。

## 前置：两样东西

1. **平台地址** `BASE_URL`（如 `https://rubick.example.com`，不含尾部斜杠）。
   **本文件的下载地址去掉结尾的 `/rubick-skill.md`，就是 `BASE_URL`**——构造上恒成立，
   子路径部署（形如 `https://host/rubick`）也照此推导，别自作主张只取域名；
2. **API Token**（形如 `rk_...`，每人一个，可重置 / 吊销；向用户索要）。

**这两样通常已经写在派你来的那句安装指令里**，直接采用、写进环境变量即可，
**不要再回头问用户**；只有确实缺了哪一样，才向他要缺的那样。

所有请求带请求头：`Authorization: Bearer rk_...`。
建议从环境变量读取（`RUBICK_BASE_URL` / `RUBICK_TOKEN`），**不要把 token 写进代码、日志或发给任何第三方**——token 即用户本人身份。

仅 `/api/v1/*` 接受 token；请求与响应均为 JSON（下载结果的接口除外，返回 CSV 文件）。

## 标准工作流

### ⓪ 开工前：检查技能更新（每个会话一次）

平台会随接口变化更新本 skill。**本会话第一次调 Rubick API 之前**先做一次版本检查，同一会话内不再重复；
`RUBICK_BASE_URL` 没配就跳过这一步。

1. `GET {BASE_URL}/rubick-skill.md`，**不带 token**（这是公开的静态文件，token 不必发给它）。
2. **先确认拿到的真是本 skill**：内容以 `---` 开头，frontmatter 里有 `name: rubick-skill` 和 `version:`。
   不满足就当作检查失败——文件不存在时平台会用 200 返回网页首页（HTML），不校验就会把网页写进 SKILL.md。
3. 比较远端与本文件 frontmatter 的 `version`（格式 `YYYY.MM.DD.N`）：按 `.` 切段、**逐段按数字比较**，
   不要按字符串比（否则 `.10` 会排到 `.9` 前面）。本地没有 `version` 视为最旧。
   **只有远端更新时才动手，绝不降级**。
4. 远端更新 → 把下载内容**原样**覆盖到**你加载本 skill 的那个 SKILL.md 路径**（不改写、不删减、不插入 token），
   重新读一遍，**按新版继续本次任务**，并告诉用户一句：「rubick-skill 已从 A 更新到 B」。
5. 任何一步失败（网络不通、非 200、校验不过、没有写权限）→ **不提示、不重试，沿用本地版本继续干活**，
   不要为了更新耽误用户的取数。

```bash
# 取远端版本号(校验通过才输出);与本地 SKILL.md 的 version 逐段按数字比较
curl -fsS --max-time 10 "$RUBICK_BASE_URL/rubick-skill.md" -o /tmp/rubick-skill.new \
  && head -1 /tmp/rubick-skill.new | grep -qx -- '---' \
  && grep -qx 'name: rubick-skill' /tmp/rubick-skill.new \
  && sed -n 's/^version: *//p' /tmp/rubick-skill.new | head -1
```

### ① 列出任务，找到目标任务

`GET {BASE_URL}/api/v1/tasks` → 返回我可见的任务数组。按 `name` / `id`（即界面上的任务编号 `#128`）定位目标任务，并读取它的 `params` 参数定义：

| 字段 | 说明 |
|---|---|
| `name` | 参数名，提交运行时的键 |
| `kind` | `single` 单值 / `list` 值列表 |
| `value_type` | `text` 文本 / `number` 数值 |
| `label` | 参数说明，格式提示（如「开始日期（格式 yyyy-mm-dd）」）通常写在这里 |
| `enum` | 候选值数组，**只对 `can_run=true` 的任务给出**；空数组就是「平台也没有候选」，问用户要值即可（候选之外的值本来也允许提交） |

同时检查两个布尔位：

- `can_run=false` → token 主人没有被授权运行该任务，**不要重试**，告诉用户去找任务作者授权「运行」；
- `allow_api=false` → 该任务未开放 API 触发（POST 会 403），告诉用户请任务作者打开该开关。

### ② 填参触发运行

`POST {BASE_URL}/api/v1/tasks/{id}/runs`，body：

```json
{ "values": { "dt": "2026-09-20", "regions": ["华东", "华南"] } }
```

- **所有参数必填、没有默认值**；取值不清楚就先问用户，不要编造（日期格式等参照 `label`）。
- `kind=list` 的参数**必须传数组**，即使只有一个值。
- `value_type=number` 的参数传数字，不带引号、不带单位。

提交立即返回 job 对象（不会等结果），记下 `job.id`。

### ③ 轮询状态，指数退避

`GET {BASE_URL}/api/v1/runs/{job_id}`，直到 `status` 变为 `success` 或 `failed`。

- **退避节奏：5 秒起步，每次 ×2，上限 60 秒**（任务可能跑几分钟到几十分钟）。
- `status=queued` 时用 `queue_ahead` 向用户报告位次：「排队中，前面还有 N 个」。
- `status=running` 时可报告已运行时长；`duration_ms` 只含执行时间，**不含排队**。
- 收到 `429`（限流，120 请求/分钟/token）时加大间隔重试，不要立刻重发。

### ④ 成功后取结果

- 要完整数据：`GET {BASE_URL}/api/v1/runs/{job_id}/result` → CSV 文件流（`text/csv`，UTF-8 BOM），
  保存到本地文件。需要用户有「下载」权限（任务对象上的 `can_download`）；没有就是 403，
  报错里写了该找谁授权，**别重试**。这时仍可以用 `preview` 取前 50 行应急。
- 只想快速看一眼：`GET {BASE_URL}/api/v1/runs/{job_id}/preview` → `{ columns, rows, row_count }`，表头 + 前 50 行。

### ⑤ 失败时如实报告

`status=failed` 时把 `error` **原样**报告给用户，并给出下一步建议：

- 报错指向参数（缺参、需为数值等）→ 与用户确认取值后重跑；
- 报错指向 SQL / 数据源 / 权限 → 建议联系任务作者，**报任务编号与运行编号**（别报任务名，任务可重名）。

## curl 示例

```bash
BASE="https://<host>"; TOKEN="rk_..."
H="Authorization: Bearer $TOKEN"

# ① 列出我可见的任务（含参数定义）
curl -s "$BASE/api/v1/tasks" -H "$H"

# ② 触发一次运行（list 参数传数组）
curl -s -X POST "$BASE/api/v1/tasks/128/runs" \
  -H "$H" -H "Content-Type: application/json" \
  -d '{"values": {"dt": "2026-09-20", "regions": ["华东", "华南"]}}'

# ③ 轮询运行状态（queued/running/success/failed）
curl -s "$BASE/api/v1/runs/12345" -H "$H"

# ④ 下载完整 CSV，或先看前 50 行
curl -s "$BASE/api/v1/runs/12345/result" -H "$H" -o result.csv
curl -s "$BASE/api/v1/runs/12345/preview" -H "$H"
```

## Python 示例

```python
import os, time
import requests

BASE = os.environ["RUBICK_BASE_URL"].rstrip("/")
H = {"Authorization": f"Bearer {os.environ['RUBICK_TOKEN']}"}

# ① 找到目标任务
tasks = requests.get(f"{BASE}/api/v1/tasks", headers=H).json()
task = next(t for t in tasks if t["name"] == "大区日报")
assert task["can_run"] and task["allow_api"], "未授权运行或未开放 API"

# ② 触发运行
job = requests.post(
    f"{BASE}/api/v1/tasks/{task['id']}/runs", headers=H,
    json={"values": {"dt": "2026-09-20", "regions": ["华东"]}},
).json()

# ③ 轮询：指数退避 5s → 60s
delay = 5
while True:
    time.sleep(delay)
    r = requests.get(f"{BASE}/api/v1/runs/{job['id']}", headers=H)
    if r.status_code == 429:          # 限流，退避重试
        delay = min(delay * 2, 60)
        continue
    job = r.json()
    if job["status"] == "queued":
        print(f"排队中，前面还有 {job.get('queue_ahead', '?')} 个")
    if job["status"] in ("success", "failed"):
        break
    delay = min(delay * 2, 60)

# ④ 取结果 / ⑤ 失败报告
if job["status"] == "success":
    csv = requests.get(f"{BASE}/api/v1/runs/{job['id']}/result", headers=H)
    with open("result.csv", "wb") as f:
        f.write(csv.content)
else:
    print("运行失败：", job["error"])
```

## 注意事项（必读）

- **结果只保留 7 天**：过期后 `result` / `preview` 返回 **404**，只能重新运行（参数可照抄旧运行的取值）。看到 404 不要改参数重发——东西没了，不是请求写错了；
  400 才是「参数不对」。job 对象上的 `result_expired` 可以让你在下载前就知道这一点。
- **限流 120 请求/分钟/token**，超限返回 429。轮询务必按退避节奏，不要打满频率。
- `allow_api=false` 的任务无法通过 API 触发（403）；`can_run=false` 说明没被授权。这两类 403 **重试无意义**，直接报告用户。
- 运行是**异步**的：提交返回不代表有结果，必须轮询到 `success` / `failed` 为止。
- **长任务不要在单轮对话里死等**：Hive 类任务可能跑几十分钟。先告知用户预计时长（可参考该任务历史运行的 `duration_ms`）与当前位次，然后结束本轮、让用户稍后回来问结果，而不是空转轮询占住对话。
- `queue_ahead` 仅在 `status=queued` 时有值；`started_at` 在还没开跑时为 null。
- token 即用户本人身份：API 能做什么与用户在网页界面上的权限完全一致，没有任何放大；发现 token 可能泄漏时，提醒用户去界面吊销。

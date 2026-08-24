# systemd 托管（开机自启 + 崩溃自愈）

线上不用 `nohup` 跑后端，而是交给 systemd 托管两个进程：

| unit | 进程 | 日志 |
|---|---|---|
| `rubick-api.service` | `python -m app.serve`（uvicorn，监听地址读 `config.ini`） | `backend/logs/api.log` |
| `rubick-worker.service` | `python -m app.worker`（并发度读 `WORKER_CONCURRENCY`） | `backend/logs/worker.log` |

两者都是 `Restart=always`（3 秒重试）+ `WantedBy=multi-user.target`，所以**进程崩溃自动拉起、机器重启自动恢复**。

## 安装（一次性，root 执行）

unit 里的路径按仓库部署在 `/opt/rubick`、虚拟环境在 `backend/.venv` 写死；换目录要同步改。

```bash
cd /opt/rubick
install -m 644 deploy/systemd/rubick-api.service    /etc/systemd/system/
install -m 644 deploy/systemd/rubick-worker.service /etc/systemd/system/
install -m 644 deploy/systemd/rubick.logrotate      /etc/logrotate.d/rubick
systemctl daemon-reload
systemctl enable --now rubick-api.service rubick-worker.service
```

装好后 `./deploy.sh` 会自动识别 unit，`start/stop/restart/status`（以及 `update` 末尾的重启）
一律改走 `systemctl`，不再 `nohup`/`pkill`；`logs` 在两种模式下都直接跟踪 `backend/logs/*.log`
（unit 也把日志追加到同一处，所以内容一致）。`./deploy.sh update` 依旧是线上更新的唯一入口。

**启动后有一道健康闸门**：`start` / `restart` / `update` 在拉起进程之后会连续请求 `/health`
（最多 20 次、每次间隔 1 秒），不通就打红字并以非零码退出、顺带贴出 `api.log` 末 40 行。
闸门失败时**站点是停着的** —— `update` / `restart` 都是先停旧进程再起新的，红字意味着新进程
没起来而旧进程也已经停了，该照那 40 行改配置或回滚上一版，不是「等等看」。这道闸门对 systemd 路径
尤其要紧 —— `Type=simple` 的 `systemctl start` 立刻返回，而 `Restart=always` 会把一个起不来的
进程反复拉起，`is-active` 看着像好的，于是「更新完成」会印在一个全站 502 的服务上。

## 监听端口的唯一来源

`backend/config.ini` 就是唯一那一份（`APP_BASE_URL` / `BACKEND_HOST` / `BACKEND_PORT`）。
unit 的 `ExecStart` 走 `python -m app.serve`，那个入口直接读配置再起 uvicorn，**不再重复写一遍
端口**，所以改完 `config.ini` 只需 `./deploy.sh restart`，无需动 unit、无需 `daemon-reload`。

**但「进程监听在哪」不等于「外面从哪进来」**：反向代理的 `proxy_pass` 里也写着这个端口，
而它不在 `config.ini` 里。挂在网关下的部署改端口，必须同时改 nginx 并 `nginx -s reload` ——
健康闸门只打 `127.0.0.1:新端口`，所以它照样会绿、`./deploy.sh restart` 照样报成功，对外却是
全站 502。这一节说的「唯一来源」只管**进程监听在哪**，不管**流量从哪进来**。

> 这里从前是两份真相（unit 里写死 `--host 127.0.0.1 --port 18091`，而 `deploy.sh` 的 nohup 路径
> 读 config.ini），靠一句「必须同步改」维持。改了不同步的后果不是报错而是**静默不生效**：
> 服务照常起来，只是网关 502、飞书回调地址也跟着算错。

## 日志

沿用原来的 `backend/logs/*.log` 路径（unit 用 `StandardOutput=append:`），因此：

- 不再像 `nohup` 那样每次重启用 `>` 把日志截断 —— 历史堆栈会保留，方便事后排查；
- 反过来日志会持续增长，靠 `/etc/logrotate.d/rubick` 每天轮转、保留 14 份、单份超 100M 提前轮转，
  用 `copytruncate`（append 模式下安全，无需通知进程重开文件）。

systemd 自身的启停/重启记录另见 `journalctl -u rubick-api -u rubick-worker`。

## 常用命令

```bash
systemctl status rubick-api rubick-worker      # 状态(含 enabled 与重启次数)
systemctl restart rubick-api                   # 只重启 API；它不做健康检查，重启后自己 curl 一次 /health
./deploy.sh logs                               # 同时跟 api.log 与 worker.log
journalctl -u rubick-worker --since "1 hour ago"
tail -f /opt/rubick/backend/logs/api.log
```

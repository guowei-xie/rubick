# systemd 托管（开机自启 + 崩溃自愈）

线上不用 `nohup` 跑后端，而是交给 systemd 托管两个进程：

| unit | 进程 | 日志 |
|---|---|---|
| `rubick-api.service` | `uvicorn app.main:app --host 127.0.0.1 --port 18091` | `backend/logs/api.log` |
| `rubick-worker.service` | `python -m app.worker` | `backend/logs/worker.log` |

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
一律改走 `systemctl`，不再 `nohup`/`pkill`。`./deploy.sh update` 依旧是线上更新的唯一入口。

## 监听端口的唯一来源

对外地址与端口的真源仍是 `backend/config.ini`（`APP_BASE_URL` / `BACKEND_HOST` / `BACKEND_PORT`），
但 unit 的 `ExecStart` 里的 `--host/--port` 是**写死的**。**改了 config.ini 的端口，必须同步改
`rubick-api.service` 并 `systemctl daemon-reload && systemctl restart rubick-api`**，否则实际
监听端口与后端自认的对外地址不一致（表现为网关 502）。

## 日志

沿用原来的 `backend/logs/*.log` 路径（unit 用 `StandardOutput=append:`），因此：

- 不再像 `nohup` 那样每次重启用 `>` 把日志截断 —— 历史堆栈会保留，方便事后排查；
- 反过来日志会持续增长，靠 `/etc/logrotate.d/rubick` 每天轮转、保留 14 份、单份超 100M 提前轮转，
  用 `copytruncate`（append 模式下安全，无需通知进程重开文件）。

systemd 自身的启停/重启记录另见 `journalctl -u rubick-api -u rubick-worker`。

## 常用命令

```bash
systemctl status rubick-api rubick-worker      # 状态(含 enabled 与重启次数)
systemctl restart rubick-api                   # 只重启 API
journalctl -u rubick-worker --since "1 hour ago"
tail -f /opt/rubick/backend/logs/api.log
```

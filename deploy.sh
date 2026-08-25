#!/usr/bin/env bash
# 拉比克一键部署 / 更新脚本(单机)。update 不是无停机滚动更新:先停旧进程再起新的。
#
# 用法:
#   ./deploy.sh init      首次部署:建 venv、装依赖、建表、构建前端、启动服务
#   ./deploy.sh update    一键更新:git pull、装依赖、建表、重建前端、停旧起新、健康检查
#   ./deploy.sh start     启动后端 API + worker(nohup 后台)
#   ./deploy.sh stop      停止后端 API + worker
#   ./deploy.sh restart   重启
#   ./deploy.sh status    查看运行状态
#   ./deploy.sh logs [N]  跟踪 API / worker 日志(从末 N 行起,默认 100)
#
# 进程管理有两种模式,脚本自动识别:
#   systemd 托管(线上):存在 rubick-api.service / rubick-worker.service 时,
#     start/stop/restart/status 一律走 systemctl(开机自启 + 崩溃自愈由 systemd 负责)。
#     部署 unit 见 deploy/systemd/README.md。
#   nohup 前台机(本地/无 systemd):后台运行,PID 写入 backend/run/*.pid。
# 两种模式日志都写 backend/logs/*.log。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
VENV="$BACKEND/.venv"
PY="$VENV/bin/python"
RUN_DIR="$BACKEND/run"
LOG_DIR="$BACKEND/logs"
API_PID="$RUN_DIR/api.pid"
WORKER_PID="$RUN_DIR/worker.pid"
API_UNIT="rubick-api.service"
WORKER_UNIT="rubick-worker.service"

mkdir -p "$RUN_DIR" "$LOG_DIR"

# 非 root 时用 sudo 调 systemctl(线上以 root 跑,SUDO 为空)
SUDO=""
if [ "$(id -u)" != 0 ] && command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi

# 本机是否已安装本项目的 systemd unit —— 决定进程管理走 systemctl 还是 nohup
systemd_managed() {
  command -v systemctl >/dev/null 2>&1 && systemctl cat "$API_UNIT" >/dev/null 2>&1
}

info() { echo -e "\033[36m[deploy]\033[0m $*"; }
warn() { echo -e "\033[33m[deploy]\033[0m $*"; }
die()  { echo -e "\033[31m[deploy] $*\033[0m" >&2; exit 1; }

# 从 config.ini 读取 BACKEND_HOST / BACKEND_PORT(经后端配置层,含默认值兜底)
read_cfg() {
  "$PY" -c "from app.core.config import settings; print(getattr(settings, '$1'))"
}

ensure_config() {
  if [ ! -f "$BACKEND/config.ini" ]; then
    cp "$BACKEND/config.example.ini" "$BACKEND/config.ini"
    warn "已从 config.example.ini 生成 config.ini,请编辑数据库/密钥等配置后重新执行。"
    warn "至少需修改:DATABASE_URL、JWT_SECRET"
    exit 1
  fi
}

setup_backend() {
  info "准备后端虚拟环境与依赖"
  [ -d "$VENV" ] || python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q -r "$BACKEND/requirements.txt"
}

init_db() {
  info "初始化 / 迁移平台元数据表(建表 + 幂等 ALTER + 敏感字段加密升级)"
  ( cd "$BACKEND" && "$PY" -m app.migrate )
}

build_frontend() {
  # 基路径来自 config.ini 的 APP_BASE_URL(后端 settings.BASE_PATH 派生):
  # 独占域名为 "/",挂在网关子路径下则为 "/rubick/"。产物里的资源前缀、
  # 前端路由 basename、/api 前缀都跟随它,与后端认定的对外地址保持一致。
  local base_path
  base_path="$(cd "$BACKEND" && read_cfg BASE_PATH)"
  info "安装前端依赖并构建静态产物(frontend/dist,基路径 $base_path)"
  ( cd "$FRONTEND" && npm install --no-audit --no-fund && VITE_BASE_PATH="$base_path" npm run build )
}

_alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

start() {
  ensure_config
  local host port
  host="$(cd "$BACKEND" && read_cfg BACKEND_HOST)"
  port="$(cd "$BACKEND" && read_cfg BACKEND_PORT)"
  if systemd_managed; then
    info "systemd 托管:启动 $API_UNIT / $WORKER_UNIT"
    $SUDO systemctl start "$API_UNIT" "$WORKER_UNIT"
    # 闸门对 systemd 路径**更要紧**:Type=simple 的 systemctl start 立刻返回,
    # 而 Restart=always 会把一个起不来的进程反复拉起,is-active 看着像好的。
    health_gate "$port"
    worker_gate
    status
    return
  fi

  if _alive "$API_PID"; then
    warn "API 已在运行 (pid $(cat "$API_PID"))"
  else
    info "启动后端 API  ->  http://$host:$port"
    # 走 app.serve 而不是直接给 uvicorn 传 --host/--port:监听地址只有 config.ini 一份真相
    # (systemd unit 也走同一个入口),否则两处早晚对不上而且不报错。
    # 子 shell 忽略 HUP 后 exec 目标进程:$! 即真实进程 PID(不受 nohup fork 行为影响),
    # 再 disown 使父脚本退出时不向其发 SIGHUP。
    ( cd "$BACKEND" && trap '' HUP && exec "$PY" -m app.serve \
        >"$LOG_DIR/api.log" 2>&1 </dev/null ) &
    echo $! >"$API_PID"; disown %% 2>/dev/null || true
  fi

  if _alive "$WORKER_PID"; then
    warn "worker 已在运行 (pid $(cat "$WORKER_PID"))"
  else
    info "启动取数 worker"
    ( cd "$BACKEND" && trap '' HUP && exec "$PY" -m app.worker \
        >"$LOG_DIR/worker.log" 2>&1 </dev/null ) &
    echo $! >"$WORKER_PID"; disown %% 2>/dev/null || true
  fi
  health_gate "$port"
  worker_gate
  status
}

# 启起来了 ≠ 起对了。不请求一次 /health 的话,「配置写错 → uvicorn 导入期就退出 →
# systemd Restart=always 无限重启」会被报成"完成":那一秒的 is-active 很可能还是
# activating,脚本打印成功,人就走了,而站点是全站 502。
health_gate() {
  local url i tries=20
  url="http://127.0.0.1:$1/health"
  for i in $(seq 1 "$tries"); do
    if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
      info "健康检查通过($url,第 ${i} 次)"
      return 0
    fi
    sleep 1
  done
  echo
  die "健康检查失败:${tries} 秒内 $url 没有返回 200。服务没起来,最后 40 行日志:
$(tail -n 40 "$LOG_DIR/api.log" 2>/dev/null)"
}

# /health 是 **API** 的,worker 起没起来它一个字都不说。而 worker 静默不起来的后果同样严重:
# 排队的取数没人认领、订阅计划不再触发 —— 页面一切正常,谁都不会发现,直到有人问
# 「我的取数怎么一直在排队」。2026-08-25 新增的 ALLOW_REMOTE_DB 护栏更让
# 「worker 起不来」变成一种**配置就能触发**的常态失败,必须有闸门看着。
worker_gate() {
  local i tries=8 restarts0 restarts1
  if systemd_managed; then
    restarts0="$($SUDO systemctl show -p NRestarts --value "$WORKER_UNIT" 2>/dev/null || echo 0)"
    for i in $(seq 1 "$tries"); do sleep 1; done
    restarts1="$($SUDO systemctl show -p NRestarts --value "$WORKER_UNIT" 2>/dev/null || echo 0)"
    # 只看 is-active 不够:Restart=always 下一个反复自杀的进程,采样那一刻很可能正好是
    # active。重启次数涨了就是在打转,和压根没起来一样糟。
    if $SUDO systemctl is-active --quiet "$WORKER_UNIT" && [ "${restarts0:-0}" = "${restarts1:-0}" ]; then
      info "worker 检查通过(active,${tries} 秒内没有重启)"
      return 0
    fi
    die "worker 没能稳定运行($WORKER_UNIT:$($SUDO systemctl is-active "$WORKER_UNIT" 2>/dev/null),
重启次数 ${restarts0:-0} -> ${restarts1:-0})。取数会一直排队、订阅计划不再触发。最后 40 行日志:
$(tail -n 40 "$LOG_DIR/worker.log" 2>/dev/null)"
  fi
  for i in $(seq 1 "$tries"); do sleep 1; done
  if _alive "$WORKER_PID"; then
    info "worker 检查通过(pid $(cat "$WORKER_PID"))"
    return 0
  fi
  die "worker 没起来。取数会一直排队、订阅计划不再触发。最后 40 行日志:
$(tail -n 40 "$LOG_DIR/worker.log" 2>/dev/null)"
}

stop() {
  if systemd_managed; then
    info "systemd 托管:停止 $API_UNIT / $WORKER_UNIT"
    # 交给 systemd 停,绝不 pkill —— 否则 Restart=always 会立刻把进程拉回来
    $SUDO systemctl stop "$API_UNIT" "$WORKER_UNIT"
    return
  fi
  for pidf in "$API_PID" "$WORKER_PID"; do
    if _alive "$pidf"; then
      local pid; pid="$(cat "$pidf")"
      info "停止 $(basename "$pidf" .pid) (pid $pid)"
      kill "$pid" 2>/dev/null || true
    fi
    rm -f "$pidf"
  done
  # 兜底:清掉可能残留的本项目进程与端口占用(单机单实例场景安全)
  pkill -f "\-m app.serve" 2>/dev/null || true
  pkill -f "uvicorn app.main:app" 2>/dev/null || true  # 旧式启动命令的残留,过渡期保留
  pkill -f "\-m app.worker" 2>/dev/null || true
  if [ -f "$BACKEND/config.ini" ]; then
    local port; port="$(cd "$BACKEND" && read_cfg BACKEND_PORT 2>/dev/null || true)"
    [ -n "${port:-}" ] && lsof -ti "tcp:$port" 2>/dev/null | xargs kill -9 2>/dev/null || true
  fi
}

unit_state() { # 输出形如 "active (enabled)"
  echo "$($SUDO systemctl is-active "$1" 2>/dev/null || true) ($($SUDO systemctl is-enabled "$1" 2>/dev/null || true))"
}

status() {
  if systemd_managed; then
    echo "  API   : $(unit_state "$API_UNIT")  [systemd $API_UNIT]"
    echo "  worker: $(unit_state "$WORKER_UNIT")  [systemd $WORKER_UNIT]"
    echo "  日志  : $LOG_DIR/api.log  |  $LOG_DIR/worker.log"
    echo "  详情  : systemctl status $API_UNIT"
    return
  fi
  if _alive "$API_PID"; then echo "  API   : 运行中 (pid $(cat "$API_PID"))"; else echo "  API   : 未运行"; fi
  if _alive "$WORKER_PID"; then echo "  worker: 运行中 (pid $(cat "$WORKER_PID"))"; else echo "  worker: 未运行"; fi
  echo "  日志  : $LOG_DIR/api.log  |  $LOG_DIR/worker.log"
}

logs() {
  local n="${1:-100}"
  info "跟踪日志(Ctrl-C 退出):$LOG_DIR/api.log 与 $LOG_DIR/worker.log,末 $n 行起"
  tail -n "$n" -F "$LOG_DIR/api.log" "$LOG_DIR/worker.log"
}

case "${1:-}" in
  init)
    ensure_config
    setup_backend
    init_db
    build_frontend
    stop || true
    start
    info "初始化部署完成。单端口即完整应用,直接访问 APP_BASE_URL 即可(无需 nginx)。"
    ;;
  update)
    info "拉取最新代码"
    ( cd "$ROOT" && git pull --ff-only )
    setup_backend
    init_db
    build_frontend
    info "重启服务"
    stop || true
    start
    info "更新完成(已通过健康检查)。"
    ;;
  start)   start ;;
  stop)    stop ;;
  restart) stop || true; start ;;
  status)  status ;;
  logs)    logs "${2:-}" ;;
  *)
    echo "用法: $0 {init|update|start|stop|restart|status|logs}"
    exit 1
    ;;
esac

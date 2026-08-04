#!/usr/bin/env bash
# 拉比克一键部署 / 滚动更新脚本(单机)。
#
# 用法:
#   ./deploy.sh init      首次部署:建 venv、装依赖、建表、构建前端、启动服务
#   ./deploy.sh update    滚动更新:git pull、装依赖、建表、重建前端、重启服务
#   ./deploy.sh start     启动后端 API + worker(nohup 后台)
#   ./deploy.sh stop      停止后端 API + worker
#   ./deploy.sh restart   重启
#   ./deploy.sh status    查看运行状态
#
# 进程用 nohup 后台运行,PID 写入 backend/run/*.pid,日志写入 backend/logs/*.log。
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

mkdir -p "$RUN_DIR" "$LOG_DIR"

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

  if _alive "$API_PID"; then
    warn "API 已在运行 (pid $(cat "$API_PID"))"
  else
    info "启动后端 API  ->  http://$host:$port"
    # 子 shell 忽略 HUP 后 exec 目标进程:$! 即真实进程 PID(不受 nohup fork 行为影响),
    # 再 disown 使父脚本退出时不向其发 SIGHUP。
    ( cd "$BACKEND" && trap '' HUP && exec "$VENV/bin/uvicorn" app.main:app \
        --host "$host" --port "$port" >"$LOG_DIR/api.log" 2>&1 </dev/null ) &
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
  sleep 1
  status
}

stop() {
  for pidf in "$API_PID" "$WORKER_PID"; do
    if _alive "$pidf"; then
      local pid; pid="$(cat "$pidf")"
      info "停止 $(basename "$pidf" .pid) (pid $pid)"
      kill "$pid" 2>/dev/null || true
    fi
    rm -f "$pidf"
  done
  # 兜底:清掉可能残留的本项目进程与端口占用(单机单实例场景安全)
  pkill -f "uvicorn app.main:app" 2>/dev/null || true
  pkill -f "\-m app.worker" 2>/dev/null || true
  if [ -f "$BACKEND/config.ini" ]; then
    local port; port="$(cd "$BACKEND" && read_cfg BACKEND_PORT 2>/dev/null || true)"
    [ -n "${port:-}" ] && lsof -ti "tcp:$port" 2>/dev/null | xargs kill -9 2>/dev/null || true
  fi
}

status() {
  if _alive "$API_PID"; then echo "  API   : 运行中 (pid $(cat "$API_PID"))"; else echo "  API   : 未运行"; fi
  if _alive "$WORKER_PID"; then echo "  worker: 运行中 (pid $(cat "$WORKER_PID"))"; else echo "  worker: 未运行"; fi
  echo "  日志  : $LOG_DIR/api.log  |  $LOG_DIR/worker.log"
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
    info "滚动更新完成。"
    ;;
  start)   start ;;
  stop)    stop ;;
  restart) stop || true; start ;;
  status)  status ;;
  *)
    echo "用法: $0 {init|update|start|stop|restart|status}"
    exit 1
    ;;
esac

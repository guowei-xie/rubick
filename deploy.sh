#!/usr/bin/env bash
# 拉比克一键部署 / 更新脚本(单机)。update 不是无停机滚动更新:先停旧进程再起新的。
#
# 用法:
#   ./deploy.sh init      首次部署:建 venv、装依赖、建表、构建前端、启动服务
#   ./deploy.sh update    一键更新:git pull、装依赖、建表、重建前端、**排空**、停旧起新、健康检查
#   ./deploy.sh start     启动后端 API + worker(nohup 后台)
#   ./deploy.sh stop      停止后端 API + worker(会打断在跑的取数,并当场告诉你打断了谁)
#   ./deploy.sh restart   重启(同 update:先排空)
#
# 排空(update / restart 默认行为,见 drain_worker):**不打断正在跑的取数**。先让 worker
# 停止认领新任务,再等在跑的跑完;等不到就中止本次部署,线上留在旧版本上。
# 真要立刻停:加 --force,代价是那些取数变成「运行中断,请重新运行」。
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

# ------------------------------------------------------------------ 排空(不打断在跑的取数)
#
# update / restart 都是「先停旧进程再起新的」,而停进程会打断在跑的查询 —— 那条运行记录会
# 卡在 running 直到下次启动的孤儿回收把它标成失败,发起人只看到「运行中断,请重新运行」,
# 而他可能已经等了半小时。**所以停之前先排空**:
#
#   1. 先只停 worker。SIGTERM 对它就是排空信号:停止认领新任务,已经在跑的等它们跑完
#      (见 app/worker.py 的 pool.shutdown(wait=True))。顺序很要紧 —— 反过来「边等边让它
#      继续认领」永远等不到零;
#   2. 同时盯着库里的 running 记录,等它清零(见 app/inflight.py);
#   3. 等不到就**中止部署**,而不是硬停:线上仍是旧版本、任务没被打断,操作人自己决定
#      是等它跑完还是 --force。
DRAIN_FORCE=0
DRAIN_STOP_PID=""

# 问一句「现在有没有在跑的取数」。退出码见 app/inflight.py:0=没有 3=有 2=--wait 等超时,
# 其它=这条命令本身失败(配置错、连不上库、护栏拒绝)。**必须分开** —— 把「命令崩了」当成
# 「有人在跑」会让部署按错误的理由中止或白等一场。
inflight_run() { ( cd "$BACKEND" && "$PY" -m app.inflight "$@" ); }

# 用法:inflight_or_die [--wait ...],结果放进 INFLIGHT_RC。命令自身失败就直接 die。
# 刻意用全局变量而不是 echo 退出码 —— $(...) 会把等待期间的进度输出一起吞掉,那会让操作人
# 对着一个「卡住不动」的脚本等一小时,而它其实每隔几秒都在说还剩几个。
INFLIGHT_RC=0
inflight_or_die() {
  INFLIGHT_RC=0
  inflight_run "$@" || INFLIGHT_RC=$?
  case "$INFLIGHT_RC" in
    0|2|3) return 0 ;;
    *) die "问不出「现在有没有在跑的取数」(python -m app.inflight 退出码 $INFLIGHT_RC),本次操作中止。
      先修好上面的报错 —— 在答案未知的情况下停机,等于赌线上没人在等结果。" ;;
  esac
}

# systemd 的耐心由 unit 的 TimeoutStopSec 决定,它比我们的等待更早到点就白排空了
# (systemd 会 SIGKILL,查询照样被打断)。不硬拦,但必须说出来 —— 这是配置能触发的失效。
check_stop_timeout() {
  systemd_managed || return 0
  local usec; usec="$($SUDO systemctl show -p TimeoutStopUSec --value "$WORKER_UNIT" 2>/dev/null || true)"
  case "$usec" in
    ""|infinity|*y*|*month*|*w*|*d*|*h*) return 0 ;;  # 空/无限/天小时级都够用
  esac
  warn "$WORKER_UNIT 的 TimeoutStopSec 是 $usec —— 到点 systemd 会 SIGKILL,长查询仍会被打断。
      请把 deploy/systemd/rubick-worker.service 里的 TimeoutStopSec 同步到线上并 daemon-reload。"
}

# 优雅停 worker。systemd 下必须走 systemctl stop(不能 kill:Restart=always 会立刻拉回来),
# 而它会阻塞到进程退出,所以放后台跑,让排空的进度由本脚本来播报。
stop_worker_graceful() {
  if systemd_managed; then
    $SUDO systemctl stop "$WORKER_UNIT" >/dev/null 2>&1 &
    DRAIN_STOP_PID=$!
    return
  fi
  if _alive "$WORKER_PID"; then
    kill "$(cat "$WORKER_PID")" 2>/dev/null || true  # SIGTERM = 排空,不是打断
  fi
}

drain_worker() {
  if [ "$DRAIN_FORCE" = 1 ]; then
    warn "--force:不等在跑的取数。它们会被打断,发起人会看到「运行中断,请重新运行」"
    return 0
  fi
  inflight_or_die
  if [ "$INFLIGHT_RC" = 0 ]; then
    return 0  # 一个都没有在跑,直接停
  fi
  check_stop_timeout
  info "先让 worker 停止认领新任务,再等在跑的取数自己跑完(不会打断它们)"
  stop_worker_graceful
  inflight_or_die --wait
  if [ "$INFLIGHT_RC" != 0 ]; then
    die "还有取数没跑完,本次 ${1:-操作} 已中止 —— 线上仍是旧版本,任务没被打断。
      worker 已停止认领新任务,等这些跑完再执行一次即可(或 $0 ${1:-update} --force 明确接受打断)。
      要现在就恢复接单:$0 start"
  fi
  if [ -n "$DRAIN_STOP_PID" ]; then
    wait "$DRAIN_STOP_PID" 2>/dev/null || true
  fi
  return 0
}

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

CMD="${1:-}"
shift || true
for arg in "$@"; do
  case "$arg" in
    --force) DRAIN_FORCE=1 ;;
  esac
done

case "$CMD" in
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
    # 装依赖、建表、构建前端都不打断任何人,所以排空放在最后一刻,线上停摆的窗口最短
    drain_worker update
    info "重启服务"
    stop || true
    start
    info "更新完成(已通过健康检查)。"
    ;;
  start)   start ;;
  stop)
    # 显式 stop 是操作人的明确决定,不替他排空;但该让他知道自己正在打断谁
    inflight_or_die
    if [ "$INFLIGHT_RC" != 0 ]; then
      warn "上面这些取数会被这次 stop 打断(想等它们跑完:$0 restart,那条会先排空)"
    fi
    stop
    ;;
  restart) drain_worker restart; stop || true; start ;;
  status)  status ;;
  logs)    logs "${1:-}" ;;
  *)
    echo "用法: $0 {init|update|start|stop|restart|status|logs} [--force]"
    echo "  update / restart 默认**等在跑的取数跑完**再停(不打断);--force 跳过等待。"
    exit 1
    ;;
esac

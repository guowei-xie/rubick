#!/usr/bin/env bash
# 一键启动拉比克后端 + Celery worker(后台运行,日志写到 /tmp)。
# 用法:  bash backend/start.sh   或   ! bash backend/start.sh
set -u

cd "$(dirname "$0")"          # 切到 backend 目录(脚本所在处)
source .venv/bin/activate

# 先停掉旧进程,避免端口占用 / 多个 worker 抢队列
lsof -ti tcp:8000 | xargs kill -9 2>/dev/null
pkill -f "celery -A app.tasks worker" 2>/dev/null
sleep 1

# 后端 API
nohup uvicorn app.main:app --port 8000 > /tmp/rubic_uv.log 2>&1 &
# 异步取数 worker(macOS 需要 fork 安全开关)
nohup env OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES \
  celery -A app.tasks worker --loglevel=info --pool=solo > /tmp/rubic_celery.log 2>&1 &

# 等后端就绪
for i in $(seq 1 40); do
  curl -s localhost:8000/health >/dev/null 2>&1 && break
  sleep 0.5
done

echo "后端:  $(curl -s localhost:8000/health 2>/dev/null || echo '未就绪,看 /tmp/rubic_uv.log')"
echo "worker: $(pgrep -f 'celery -A app.tasks worker' | wc -l | tr -d ' ') 进程"
echo "日志:  后端 /tmp/rubic_uv.log  |  worker /tmp/rubic_celery.log"
echo "前端:  http://localhost:5173  (若未起: cd frontend && npm run dev)"

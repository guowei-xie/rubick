"""开放 API(/api/v1/*)的内存滑动窗口限流,按用户计数。

进程内状态:API 是单进程 uvicorn(见 main.py 托管 SPA 的注释),窗口放内存足够;
worker 不承接 HTTP,无需参与。重启即清零是刻意的取舍 —— 限流防的是「脚本/Agent
失控打爆平台」这种分钟级的事,不是配额记账,为一分钟的窗口引入 Redis 不值得。
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from app.core.config import settings

_WINDOW_SECONDS = 60.0

_lock = threading.Lock()
_hits: dict[int, deque[float]] = defaultdict(deque)


def check(user_id: int) -> bool:
    """给该用户记一次调用:窗口内未超限返回 True(放行),否则 False。

    上限每次读取 settings.API_RATE_LIMIT_PER_MINUTE 而不是在启动时固化 —— 测试可以
    直接改配置值,改配置也无需重启即生效。<= 0 视为关闭限流(恒放行)。
    """
    limit = settings.API_RATE_LIMIT_PER_MINUTE
    if limit <= 0:
        return True
    now = time.monotonic()
    with _lock:
        dq = _hits[user_id]
        while dq and dq[0] <= now - _WINDOW_SECONDS:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True


def reset() -> None:
    """清空全部窗口状态。**仅供测试** —— 生产路径没有调用它的理由。"""
    with _lock:
        _hits.clear()

"""长驻服务(API / worker)的统一日志配置。

带时间戳/级别的结构化 stdout 日志(部署脚本已把进程 stdout 重定向到 logs/*.log),
便于运维排查——对一个以「审计/可观测」为卖点的平台尤为必要。
(一次性 CLI 脚本如 migrate/seed 仍用 print 直出,无需时间戳。)
"""
from __future__ import annotations

import logging
import sys


def setup_logging(level: int = logging.INFO) -> None:
    # basicConfig 自身幂等:root 已有 handler 时即 no-op,无需额外守卫
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)

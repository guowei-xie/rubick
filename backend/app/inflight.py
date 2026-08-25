"""「现在有没有正在跑的取数」—— 停机之前必须先问的那一句。

**为什么要有这个入口**:停进程会打断在跑的查询,而被打断的取数在业务侧不是「失败重试」
那么轻:那条运行记录会卡在 running,直到下次启动的孤儿回收把它标成失败(见
query_service.reclaim_stale_jobs),发起人只看到「运行中断,请重新运行」——而他可能已经
等了半小时。`deploy.sh update` 每次都可能撞上这件事,所以「能不能停」得有个能被脚本问的答案。

判据只看**库里的 running 记录**,不看进程:取数可能跑在 worker 里,也可能(RUN_INLINE=true)
跑在 API 进程里,而两种情况下「不许打断」是同一件事。queued 不算在内 —— 它们还没开始跑,
重启只是让它们多排一会儿,新 worker 起来照旧认领,没有任何东西会丢。

用法(deploy.sh 用的就是这两条):
    python -m app.inflight            # 打印在跑的取数
    python -m app.inflight --wait     # 等到一个都不剩

退出码刻意避开 1:「有人在跑」和「这条命令自己崩了」(配置写错、连不上库、护栏拒绝启动)
必须能被脚本分开 —— 而 Python 未捕获异常给的就是 1。把两者混在一起的下场是:一个配置错误
被当成「有任务在跑」,部署要么白等一场,要么按错误的理由中止。
    0 = 没有在跑的,现在停机不打断任何人
    2 = --wait 等到上限仍有在跑的
    3 = 有在跑的(报告模式)
    其它 = 这条命令本身失败了,答案未知
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.query_job import JOB_RUNNING, QueryJob
from app.models.template import SqlTemplate
from app.models.user import User
from app.services import query_service

# 轮询间隔(秒)。等的是几分钟到一小时量级的查询,5 秒足够灵敏,又不会把日志刷成瀑布。
DEFAULT_INTERVAL_SECONDS = 5

# 退出码(含义见模块文档;1 留给「命令本身失败」,那是 Python 未捕获异常的默认码)
EXIT_CLEAR = 0
EXIT_WAIT_TIMEOUT = 2
EXIT_BUSY = 3


def inflight(db: Session) -> list[tuple[int, str, str, int]]:
    """在跑的取数:[(job_id, 任务名, 发起人, 已跑秒数)],按开始得早的在前。

    已跑多久用**库时钟**算(updated_at 是状态转成 running 那一刻写的):这套代码里跨时钟
    比较踩过的坑不必再踩一遍,而部署脚本可能跑在与库不同的机器上。
    """
    now = db.scalar(select(func.now()))
    rows = db.execute(
        select(QueryJob.id, SqlTemplate.name, User.name, QueryJob.updated_at)
        .join(SqlTemplate, SqlTemplate.id == QueryJob.template_id, isouter=True)
        .join(User, User.id == QueryJob.user_id, isouter=True)
        .where(QueryJob.status == JOB_RUNNING)
        .order_by(QueryJob.id.asc())
    ).all()
    return [
        (jid, name or f"任务#{jid}", who or "(未知发起人)", _elapsed_seconds(now, started))
        for jid, name, who, started in rows
    ]


def _elapsed_seconds(now, started) -> int:
    if not isinstance(now, datetime) or not isinstance(started, datetime):
        return 0
    return max(0, int((now - started).total_seconds()))


def describe(job: tuple[int, str, str, int]) -> str:
    jid, name, who, elapsed = job
    return f"  运行 #{jid}《{name}》{who} 发起,已跑 {elapsed // 60} 分 {elapsed % 60} 秒"


def report() -> int:
    """打印当前在跑的取数,返回条数(0 = 现在停机不会打断任何人)。"""
    db = SessionLocal()
    try:
        jobs = inflight(db)
    finally:
        db.close()
    if not jobs:
        print("当前没有正在跑的取数,可以安全停机。")
        return 0
    print(f"有 {len(jobs)} 个取数正在跑:")
    for job in jobs:
        print(describe(job))
    return len(jobs)


def default_timeout_seconds() -> int:
    """等到「不可能还有活着的查询」为止 —— 与孤儿回收同一个口径。

    盖住全局默认超时、Hive 默认超时、以及库里某个任务自己配的超时里最大的那个,再加缓冲
    (见 query_service.stale_after_seconds)。自己另编一个数的下场是:配了 6 小时超时的任务
    还在正常跑,而部署脚本已经等不下去了。
    """
    db = SessionLocal()
    try:
        return query_service.stale_after_seconds(db)
    finally:
        db.close()


def wait_until_clear(
    *, timeout: int, interval: int = DEFAULT_INTERVAL_SECONDS, out=print
) -> bool:
    """等到没有在跑的取数为止。清零返回 True,超时返回 False(**不打断任何东西**)。

    每轮都新开会话:别的进程刚提交的状态变化,复用同一个会话是看不见的。
    """
    deadline = time.monotonic() + timeout
    last_seen = -1
    while True:
        db = SessionLocal()
        try:
            jobs = inflight(db)
        finally:
            db.close()
        if not jobs:
            out("在跑的取数已全部结束。")
            return True
        if len(jobs) != last_seen:  # 只在「还剩几个」变化时说话,不刷屏
            out(f"还在等 {len(jobs)} 个取数跑完(不会打断它们):")
            for job in jobs:
                out(describe(job))
            last_seen = len(jobs)
        if time.monotonic() >= deadline:
            out(f"等了 {timeout} 秒仍有 {len(jobs)} 个在跑。")
            return False
        time.sleep(min(interval, max(1, deadline - time.monotonic())))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="查看/等待正在跑的取数(停机前用)")
    parser.add_argument("--wait", action="store_true", help="等到一个都不剩再返回")
    parser.add_argument(
        "--timeout", type=int, default=None,
        help="--wait 的最长等待秒数,默认取「最长可能的查询超时 + 缓冲」",
    )
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS)
    args = parser.parse_args(argv)

    if not args.wait:
        return EXIT_BUSY if report() else EXIT_CLEAR

    timeout = args.timeout if args.timeout is not None else default_timeout_seconds()
    if wait_until_clear(timeout=timeout, interval=args.interval):
        return EXIT_CLEAR
    return EXIT_WAIT_TIMEOUT


if __name__ == "__main__":
    sys.exit(main())

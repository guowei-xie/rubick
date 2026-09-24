"""一次性:给存量代订阅者补齐 view/run/download 业务授权。

背景:代订阅早先只顺带补一条 view(当时的 permission_service.grant_view),于是授权列表里
出现一排「只有查看」的人 —— 授权人以为自己点漏了,业务方收到推送后也只能看、不能自己跑、
不能下载(2026-09 管理员反馈)。口径现已改为代订阅默认
给齐三项(permission_service.grant_business),本脚本补齐改口径之前加进来的那批人。

**目标**:`task_subscriptions.added_by` 非空(代订阅来的,自助订阅的人不动)、任务已上线,
且按 permission_service.business_gaps_among 仍有缺口的人 —— 平台管理员与任务所属团队成员
的权限来自身份,不补行,判据与代订阅本身同一把尺。

**幂等**:只写缺的动作;补过的人重跑为 0 条。每补一人记一条普通的 permission_grant 审计
(detail.via = "backfill_subscriber_grants"),授权人记为当初代订阅的操作者(granted_by =
added_by)—— 「这个人的权限从哪来」在审计里仍是一条连续的时间线。

安全要求(与 backfill_user_emails.py / migrate.py 同):
  - 先在本地 SQLite 验证,再在部署窗口对线上 MySQL 执行;**勿直接改线上库**。
  - 线上先 dry-run 把名单给负责人看,确认后再 --apply。

用法(在 backend 目录):
    python -m app.backfill_subscriber_grants                     # dry-run:只打印将补什么(默认)
    python -m app.backfill_subscriber_grants --template-id 42    # 只看某个任务
    python -m app.backfill_subscriber_grants --apply             # 实际写库
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  注册所有模型
from app.core.database import SessionLocal, engine
from app.models.audit import ACTION_PERMISSION_GRANT
from app.models.permission import BUSINESS_ACTIONS, RESOURCE_TEMPLATE
from app.models.subscription import TaskSubscription
from app.models.template import SqlTemplate
from app.models.user import User
from app.services import audit_service, permission_service

VIA = "backfill_subscriber_grants"


@dataclass
class Gap:
    template_id: int
    template_name: str
    user_id: int
    user_name: str | None
    added_by: int
    missing: list[str]


def find_gaps(db: Session, *, template_id: int | None = None) -> list[Gap]:
    """存量代订阅者里还缺业务授权的人。按任务逐个问 business_gaps_among(每任务固定 2 次查询)。"""
    stmt = (
        select(TaskSubscription.template_id, TaskSubscription.user_id, TaskSubscription.added_by)
        .where(TaskSubscription.added_by.is_not(None))
        .order_by(TaskSubscription.template_id, TaskSubscription.user_id)
    )
    if template_id is not None:
        stmt = stmt.where(TaskSubscription.template_id == template_id)
    by_tmpl: dict[int, dict[int, int]] = {}
    for tid, uid, added_by in db.execute(stmt):
        by_tmpl.setdefault(tid, {})[uid] = added_by

    gaps: list[Gap] = []
    for tid, subs in by_tmpl.items():
        tmpl = db.get(SqlTemplate, tid)
        # 未上线的任务补了也看不见(can_view 对被授权人叠加了已上线),与代订阅同一条前置判据
        if tmpl is None or not permission_service.is_subscribable(tmpl):
            continue
        users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(list(subs))))}
        missing = permission_service.business_gaps_among(db, list(users.values()), tmpl)
        for uid in sorted(missing):
            gaps.append(
                Gap(
                    template_id=tid, template_name=tmpl.name, user_id=uid,
                    user_name=users[uid].name, added_by=subs[uid], missing=missing[uid],
                )
            )
    return gaps


def apply_gaps(db: Session, gaps: list[Gap]) -> int:
    """逐人补齐并记审计,返回新建的授权行数。每人一个事务(审计 log 自带 commit)。"""
    total = 0
    for g in gaps:
        created = permission_service.grant_business(
            db, template_id=g.template_id, user_id=g.user_id,
            actions=g.missing, granted_by=g.added_by,
        )
        db.commit()
        if not created:
            continue
        total += len(created)
        audit_service.log(
            db, user=None, action=ACTION_PERMISSION_GRANT,
            resource_type=RESOURCE_TEMPLATE, resource_id=g.template_id,
            resource_name=g.template_name,
            detail=permission_service.grant_audit_detail(
                subject_id=str(g.user_id), subject_name=g.user_name,
                actions=list(BUSINESS_ACTIONS), actions_created=created,
                granted_by=g.added_by, via=VIA,
            ),
        )
    return total


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="实际写库(默认 dry-run)")
    ap.add_argument("--template-id", type=int, help="只处理这个任务")
    args = ap.parse_args()

    print(f"数据库: {engine.url.render_as_string(hide_password=True)}")
    with SessionLocal() as db:
        gaps = find_gaps(db, template_id=args.template_id)
        for g in gaps:
            print(
                f"  任务#{g.template_id}《{g.template_name}》 "
                f"用户#{g.user_id} {g.user_name or ''} 补: {','.join(g.missing)}"
            )
        print(f"共 {len(gaps)} 人待补")
        if not args.apply:
            print("dry-run:未写库。确认无误后加 --apply 执行。")
            return
        n = apply_gaps(db, gaps)
        print(f"已写入 {n} 条授权")


if __name__ == "__main__":
    main()

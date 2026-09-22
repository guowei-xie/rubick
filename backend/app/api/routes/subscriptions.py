"""任务订阅:自助订阅/退订、订阅者名单、订阅事件留痕。

计划的配置**不在这里**:它随任务保存走(PUT /templates/{id} 的 subscription 字段),
因为「已开订阅的任务不能有变量」是同一次保存内的事务性约束(见
template_service.add_version 与 subscription_service.apply_template_save)。
"""
from __future__ import annotations

from dataclasses import asdict
from uuid import uuid4

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import (
    BatchRejectedError,
    NotFoundError,
    PermissionDeniedError,
    RubicError,
)
from app.models.audit import (
    ACTION_PERMISSION_GRANT,
    ACTION_TASK_SUBSCRIBE,
    ACTION_TASK_SUBSCRIBE_FOR,
    ACTION_TASK_UNSUBSCRIBE,
    ACTION_TASK_UNSUBSCRIBE_FOR,
)
from app.models.permission import ACTION_VIEW, RESOURCE_TEMPLATE, SUBJECT_USER
from app.models.subscription import SUB_EVENT_META
from app.models.template import STATUS_PUBLISHED, SqlTemplate
from app.models.user import User
from app.schemas.template import (
    SUBSCRIBE_FOR_LIMIT,
    SubscribeForIn,
    SubscribeForOut,
    SubscriberOut,
    SubscribersOut,
    SubscriptionEventOut,
)
from app.services import (
    audit_service,
    notify_service,
    permission_service,
    subscription_service,
)

router = APIRouter(prefix="/tasks", tags=["subscriptions"])


def _load(db: Session, template_id: int) -> SqlTemplate:
    tmpl = db.get(SqlTemplate, template_id)
    if tmpl is None:
        raise NotFoundError("任务不存在")
    return tmpl


def _managed(db: Session, user: User, template_id: int) -> SqlTemplate:
    """取回一个「我有权管订阅者的」任务。订阅者名单这个集合资源上的四个动作
    (看名单 / 看留痕 / 代订阅 / 移除)共用同一把尺 = 任务编辑权(含平台管理员)。

    取任务与判权收成一步,是因为它们本就是不可拆的二联:分成两行写,新增 handler 时
    漏掉第二行就是一个静默的越权口子。口径统一还消除了「他看得到却加不了」这种
    要向用户解释的中间态。
    """
    tmpl = _load(db, template_id)
    if not permission_service.can_edit_template(db, user, tmpl.id):
        raise PermissionDeniedError("无权管理该任务的订阅者")
    return tmpl


def _names_of(db: Session, ids: set[int]) -> dict[int, str]:
    """一批 user_id → 姓名(一次查询)。空集合直接回空,免得下发一条空 IN。"""
    if not ids:
        return {}
    return {uid: name for uid, name in db.execute(select(User.id, User.name).where(User.id.in_(ids)))}


@router.put("/{template_id}/subscription")
def subscribe_task(
    template_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    ip: str | None = Depends(client_ip),
):
    """订阅任务(幂等)。资格 = can_view 且已上线(permission_service.can_subscribe),
    且该任务开启了订阅计划。"""
    tmpl = _load(db, template_id)
    scope = permission_service.team_scope(db, user)
    if not permission_service.can_subscribe(scope, tmpl):
        raise PermissionDeniedError("无权订阅该任务(需要该任务的查看权限,且任务已上线)")
    sched = subscription_service.get_schedule(db, tmpl.id)
    if sched is None or not sched.enabled:
        raise RubicError("该任务未开启订阅")
    created = subscription_service.subscribe(db, tmpl, user)
    if created:
        audit_service.log(
            db, user=user, action=ACTION_TASK_SUBSCRIBE,
            resource_type=RESOURCE_TEMPLATE, resource_id=tmpl.id, resource_name=tmpl.name,
            detail={"schedule": subscription_service.describe_schedule(sched)}, ip=ip,
        )
    return {"ok": True, "created": created}


@router.delete("/{template_id}/subscription")
def unsubscribe_task(
    template_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    ip: str | None = Depends(client_ip),
):
    """退订(幂等,只动本人的订阅行)。不设资格守卫:权限被撤的人更应该退得出去。"""
    tmpl = _load(db, template_id)
    removed = subscription_service.unsubscribe(db, tmpl.id, user)
    if removed:
        audit_service.log(
            db, user=user, action=ACTION_TASK_UNSUBSCRIBE,
            resource_type=RESOURCE_TEMPLATE, resource_id=tmpl.id, resource_name=tmpl.name,
            detail={}, ip=ip,
        )
    return {"ok": True, "removed": removed}


@router.get("/{template_id}/subscribers", response_model=SubscribersOut)
def task_subscribers(
    template_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """订阅者名单 + 各自连续未消费期数(开发者判断任务价值用)+ 这一行是谁弄进来的。"""
    tmpl = _managed(db, user, template_id)
    subs = subscription_service.subscribers_of(db, tmpl.id)
    # 代订阅者的名字批量补齐(自助订阅的行 added_by 为空)
    operator_names = _names_of(db, {s.added_by for s in subs if s.added_by})
    items = [
        SubscriberOut(
            user_id=s.user_id,
            name=s.user_name,
            avatar=s.user_avatar,
            miss_streak=s.miss_streak,
            created_at=s.created_at,
            added_by=s.added_by,
            added_by_name=operator_names.get(s.added_by) if s.added_by else None,
        )
        for s in subs
    ]
    return SubscribersOut(threshold=settings.SUBSCRIPTION_MISS_LIMIT, items=items)


@router.get("/{template_id}/subscription-events", response_model=list[SubscriptionEventOut])
def task_subscription_events(
    template_id: int,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """该任务的订阅/退订留痕,倒序分页(append-only,见 models/subscription
    .TaskSubscriptionEvent)。"""
    tmpl = _managed(db, user, template_id)
    events = subscription_service.events_of(
        db, tmpl.id, limit=min(max(limit, 1), 200), offset=max(offset, 0)
    )
    # 当事人/触发者名字批量补齐(系统自动动作 operator 为空)
    names = _names_of(
        db, {e.user_id for e in events} | {e.operator_id for e in events if e.operator_id}
    )
    return [
        SubscriptionEventOut(
            id=e.id,
            user_id=e.user_id,
            user_name=names.get(e.user_id),
            action=e.action,
            action_label=SUB_EVENT_META.get(e.action, e.action),
            operator_id=e.operator_id,
            operator_name=names.get(e.operator_id) if e.operator_id else None,
            detail=e.detail,
            created_at=e.created_at,
        )
        for e in events
    ]


# ---------------------------------------------------------------- 代订阅(管理侧)


@router.post("/{template_id}/subscribers", response_model=SubscribeForOut)
def subscribe_task_for(
    template_id: int,
    data: SubscribeForIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    ip: str | None = Depends(client_ip),
):
    """把这个任务订阅给一批人(开发者替业务方订上)。**全成功才生效。**

    校验相位与写入相位分开,同 tasks.batch_transfer_task_author。**但有一处不同**:
    通讯录搜出来的人要先落库才拿得到 user_id(resolve_subscriber_targets 会 flush),
    所以整批被拒时必须**显式 db.rollback()** —— 那边「连回滚都不需要」的说法在这里不成立。

    没有 view 权的人在同一事务里补一条 view 授权:订阅的产出是运行结果,而结果的可见性
    判据就是 can_view(permission_service.can_access_job);取走那一期 CSV 的资格由
    can_download_job 按在册订阅者单独放行,所以也不必补 download。为什么只补 view,
    见 permission_service.grant_view。
    """
    tmpl = _managed(db, user, template_id)
    if not data.subjects:
        raise RubicError("请先选择要添加的订阅者")
    if len(data.subjects) > SUBSCRIBE_FOR_LIMIT:
        raise RubicError(f"一次最多为 {SUBSCRIBE_FOR_LIMIT} 人代订阅,请分批操作")

    sched = subscription_service.get_schedule(db, tmpl.id)
    if sched is None or not sched.enabled:
        raise RubicError("该任务未开启订阅,请先在任务编辑器里打开「允许订阅」")
    # **必须显式判一次任务侧资格**:can_view 对被授权人叠加了「已上线」,草稿补了 view
    # 授权照样 can_view=False;退一步就算订上了,tick 也只扫 published 的任务。
    # 不拦就是在造一批「补了权限却永远收不到推送」的僵尸订阅行。
    # 人侧那一半不判 —— 这条路径自己会把它补出来。
    if not permission_service.is_subscribable(tmpl):
        raise RubicError("该任务尚未上线,代订阅后对方仍然看不到数据;请先上线再来添加")

    items, skipped, rejections = subscription_service.subscribe_for_plan(db, tmpl, data.subjects)
    if rejections:
        db.rollback()  # 把 resolve 阶段落库的壳用户一并撤掉(见本函数 docstring)
        raise BatchRejectedError(
            f"本次添加未生效(全成功才生效):所选 {len(data.subjects)} 人中,"
            f"有 {len(rejections)} 人不能订阅《{tmpl.name}》。",
            [asdict(r) for r in rejections],
        )

    # 名字与任务快照在 commit 之前留一份:commit 会 expire 掉这些实例,之后循环里每读一次
    # 属性都是一次往返(最多 50 人 × 两条审计 detail)。
    names = {item.user.id: item.user.name for item in items}
    tmpl_id, tmpl_name = tmpl.id, tmpl.name
    schedule_desc = subscription_service.describe_schedule(sched)

    created, granted = subscription_service.subscribe_for(db, tmpl, items, operator=user)
    granted_set = set(granted)

    # 审计在业务 commit 之后写,理由见 subscription_service.subscribe_for。
    # batch_id 让 N 条行重新聚成「同一次操作」,同批量交接。
    batch_id = uuid4().hex
    for uid in created:
        audit_service.log(
            db, user=user, action=ACTION_TASK_SUBSCRIBE_FOR,
            resource_type=RESOURCE_TEMPLATE, resource_id=tmpl_id, resource_name=tmpl_name,
            detail={
                "target_user_id": uid, "target_user_name": names.get(uid),
                "granted_view": uid in granted_set, "schedule": schedule_desc,
                "batch_id": batch_id, "batch_size": len(created),
            },
            ip=ip,
        )
        if uid in granted_set:
            # 顺带补的 view 记成一条**普通的授权**,不另起新码:这样「这个人对这个任务的
            # 查看权从哪来」在审计里是一条连续的时间线,手工授的与代订阅带的躺在同一次筛选里。
            audit_service.log(
                db, user=user, action=ACTION_PERMISSION_GRANT,
                resource_type=RESOURCE_TEMPLATE, resource_id=tmpl_id, resource_name=tmpl_name,
                detail={
                    "subject_type": SUBJECT_USER, "subject_id": str(uid),
                    "subject_name": names.get(uid), "actions": [ACTION_VIEW],
                    "actions_created": [ACTION_VIEW],
                    "via": "subscribe_for", "batch_id": batch_id,
                },
                ip=ip,
            )
    if created:
        notify_service.notify_subscribed_by_operator(
            db, tmpl, created, operator_name=user.name or "任务负责人",
            schedule_desc=schedule_desc, granted_ids=granted_set,
        )
    return SubscribeForOut(created=created, skipped=skipped, granted_view=granted)


@router.delete("/{template_id}/subscribers/{user_id}")
def remove_task_subscriber(
    template_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    ip: str | None = Depends(client_ip),
):
    """把某人移出订阅者名单(幂等)。不在名单里就什么都不做,也**不记审计** ——
    与 permissions.revoke 同一条:什么都没发生时留一条「撤销」是误导。"""
    tmpl = _managed(db, user, template_id)
    removed = subscription_service.unsubscribe_for(db, tmpl.id, user_id, operator=user)
    if removed:
        target = db.get(User, user_id)  # 只在真删了的路径上查,幂等路径什么都不记
        audit_service.log(
            db, user=user, action=ACTION_TASK_UNSUBSCRIBE_FOR,
            resource_type=RESOURCE_TEMPLATE, resource_id=tmpl.id, resource_name=tmpl.name,
            detail={
                "target_user_id": user_id,
                "target_user_name": target.name if target else None,
            },
            ip=ip,
        )
        notify_service.notify_unsubscribed_by_operator(
            db, tmpl, user_id, operator_name=user.name or "任务负责人"
        )
    return {"ok": True, "removed": removed}

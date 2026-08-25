"""任务订阅:自助订阅/退订、订阅者名单、订阅事件留痕。

计划的配置**不在这里**:它随任务保存走(PUT /templates/{id} 的 subscription 字段),
因为「已开订阅的任务不能有变量」是同一次保存内的事务性约束(见
template_service.add_version 与 subscription_service.apply_template_save)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import NotFoundError, PermissionDeniedError, RubicError
from app.models.audit import ACTION_TASK_SUBSCRIBE, ACTION_TASK_UNSUBSCRIBE
from app.models.permission import RESOURCE_TEMPLATE
from app.models.subscription import SUB_EVENT_META
from app.models.template import SqlTemplate
from app.models.user import User
from app.schemas.template import (
    SubscriberOut,
    SubscribersOut,
    SubscriptionEventOut,
)
from app.services import audit_service, permission_service, subscription_service

router = APIRouter(prefix="/tasks", tags=["subscriptions"])


def _load(db: Session, template_id: int) -> SqlTemplate:
    tmpl = db.get(SqlTemplate, template_id)
    if tmpl is None:
        raise NotFoundError("任务不存在")
    return tmpl


def _require_can_view_subscribers(db: Session, user: User, tmpl: SqlTemplate) -> None:
    """订阅者名单/订阅记录的守卫 = 任务编辑权(开发者/团队管理员口径,含平台管理员)。"""
    if not permission_service.can_edit_template(db, user, tmpl.id):
        raise PermissionDeniedError("无权查看该任务的订阅信息")


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
    """订阅者名单 + 各自连续未消费期数(开发者判断任务价值用)。"""
    tmpl = _load(db, template_id)
    _require_can_view_subscribers(db, user, tmpl)
    items = [
        SubscriberOut(
            user_id=s.user_id,
            name=s.user_name,
            avatar=s.user_avatar,
            miss_streak=s.miss_streak,
            created_at=s.created_at,
        )
        for s in subscription_service.subscribers_of(db, tmpl.id)
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
    tmpl = _load(db, template_id)
    _require_can_view_subscribers(db, user, tmpl)
    events = subscription_service.events_of(
        db, tmpl.id, limit=min(max(limit, 1), 200), offset=max(offset, 0)
    )
    # 当事人/触发者名字批量补齐(一次查询;系统自动动作 operator 为空)
    uids = {e.user_id for e in events} | {e.operator_id for e in events if e.operator_id}
    names: dict[int, str] = {}
    if uids:
        names = {
            uid: name
            for uid, name in db.execute(select(User.id, User.name).where(User.id.in_(uids)))
        }
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

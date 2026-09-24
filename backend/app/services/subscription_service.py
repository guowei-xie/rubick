"""任务订阅:计划的 due 判定与调度、订阅关系的增删、消费结算与自动清退。

「订阅」的完整语义分四处协作,各管一件事:
  - 本模块 —— 计划/订阅/事件三张表的**唯一**读写口,due 计算,tick 调度,消费结算;
  - query_service.enqueue_scheduled —— 定时运行的入队(与人发起的 enqueue 同一骨架);
  - notify_service —— 订阅相关的全部收件人名单与文案(_dispatch_subscription_run);
  - template_service.add_version —— 「已开订阅的任务不能有变量」的保存卡点。

调度只存在于 worker 进程(app/worker.py 每 SCHEDULE_SCAN_INTERVAL_SECONDS 调一次 tick),
API 进程不扫描 —— 单进程扫描本身就消除了重复触发;水位原子推进再兜一道,将来多 worker
实例也安全(与 worker._claim_next_job_id 同一套路)。

停机错过的时刻**只补最近一期**:订阅结果按期覆盖(旧期成功即被下一期取代),连补 N 期
只是白烧目标库;而整期跳过会让服务重启 10 分钟就丢一期,代价不对称。latest_planned_at
的形态天然只返回最近一期,水位一次跳到位,更早的自动作废。
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time as time_cls, timedelta

from sqlalchemy import exists, func, or_, select, update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.exceptions import ConflictError, CredentialRequiredError, RubicError
from app.core.logging_setup import get_logger
from app.models.audit import ACTION_TASK_AUTO_UNSUBSCRIBE
from app.models.permission import RESOURCE_TEMPLATE
from app.models.query_job import (
    JOB_QUEUED,
    JOB_RUNNING,
    JOB_SUCCESS,
    SOURCE_API,
    SOURCE_RUN,
    SOURCE_SUBSCRIBE,
    QueryJob,
)
from app.models.subscription import (
    FREQ_DAILY,
    FREQ_MONTHLY,
    FREQ_WEEKLY,
    SUB_EVENT_ADDED,
    SUB_EVENT_AUTO_UNSUBSCRIBE,
    SUB_EVENT_CLOSED_UNSUBSCRIBE,
    SUB_EVENT_MEMBER_REMOVED,
    SUB_EVENT_REMOVED,
    SUB_EVENT_SUBSCRIBE,
    SUB_EVENT_UNSUBSCRIBE,
    TaskSchedule,
    TaskSubscription,
    TaskSubscriptionEvent,
)
from app.models.template import STATUS_PUBLISHED, SqlTemplate
from app.models.user import SYSTEM_SCHEDULER_OPEN_ID, User

log = get_logger("rubick.subscription")

_WEEKDAY_CN = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "日"}


# ---------------------------------------------------------------- 系统身份


def scheduler_user(db: Session) -> User:
    """订阅定时运行的系统用户(migrate._ensure_system_scheduler_user 创建)。

    不存在直接报错而不是现场补建:补建意味着两处各有一套「系统用户长什么样」的定义,
    而缺了它只可能是没跑迁移 —— 那该在部署期修,不该在运行期静默兜底。
    """
    user = db.scalar(select(User).where(User.feishu_open_id == SYSTEM_SCHEDULER_OPEN_ID))
    if user is None:
        raise RubicError("系统用户(定时运行)不存在,请先执行 python -m app.migrate")
    return user


# ---------------------------------------------------------------- due 判定(纯函数)


def latest_planned_at(freq: str, days: list, at_time: str, now: datetime) -> datetime | None:
    """≤ now 的最近一个计划时刻;配置不合法返回 None(fail-closed,永不触发)。

    monthly 对选中的号数 d 取 min(d, 当月天数) —— 29/30/31 遇小月顺延到月末最后一天;
    多个号数 clamp 撞到同一天时只是同一个时刻,水位比较天然去重。
    纯函数、不碰库,月末/跨月这类边界全靠单测钉死(见 tests/test_subscription_due.py)。
    """
    try:
        hh, mm = at_time.split(":")
        at = time_cls(int(hh), int(mm))
    except (AttributeError, TypeError, ValueError):
        return None

    if freq == FREQ_DAILY:
        cand = datetime.combine(now.date(), at)
        return cand if cand <= now else cand - timedelta(days=1)

    if freq == FREQ_WEEKLY:
        wanted = {int(d) for d in (days or []) if isinstance(d, (int, float)) and 1 <= int(d) <= 7}
        if not wanted:
            return None
        # 从今天往回找,第一个命中的就是最近一期(back=7 兜底:同一星期几的上周,必然 ≤ now)
        for back in range(8):
            day = now.date() - timedelta(days=back)
            if day.isoweekday() not in wanted:
                continue
            cand = datetime.combine(day, at)
            if cand <= now:
                return cand
        return None

    if freq == FREQ_MONTHLY:
        wanted = {int(d) for d in (days or []) if isinstance(d, (int, float)) and 1 <= int(d) <= 31}
        if not wanted:
            return None
        best: datetime | None = None
        # 本月与上月各算一遍(上月兜底:本月的候选可能全在未来)
        for months_back in (0, 1):
            year, month = now.year, now.month - months_back
            if month <= 0:
                year, month = year - 1, month + 12
            last_day = calendar.monthrange(year, month)[1]
            for d in wanted:
                cand = datetime.combine(date(year, month, min(d, last_day)), at)
                if cand <= now and (best is None or cand > best):
                    best = cand
        return best

    return None


def planned_between(
    freq: str, days: list, at_time: str, start: datetime, end: datetime
) -> list[datetime]:
    """[start, end) 内的全部计划时刻,升序;配置不合法返回空(与 latest_planned_at 同样 fail-closed)。

    给「未来会不会扎堆」这类前瞻用。命中规则必须与 latest_planned_at **逐条一致**
    (weekly 按 ISO 星期几、monthly 小月顺延到月末)—— 两者对不上时,看板预告的高峰
    与调度器真正 fire 的时刻会差一天,而没人会发现。一致性由 test_subscription_due 交叉钉住。
    """
    try:
        hh, mm = at_time.split(":")
        at = time_cls(int(hh), int(mm))
    except (AttributeError, TypeError, ValueError):
        return []

    if freq == FREQ_DAILY:
        hits = lambda d: True  # noqa: E731
    elif freq == FREQ_WEEKLY:
        wanted = {int(d) for d in (days or []) if isinstance(d, (int, float)) and 1 <= int(d) <= 7}
        hits = lambda d: d.isoweekday() in wanted  # noqa: E731
    elif freq == FREQ_MONTHLY:
        wanted = {int(d) for d in (days or []) if isinstance(d, (int, float)) and 1 <= int(d) <= 31}
        hits = lambda d: any(  # noqa: E731
            d.day == min(w, calendar.monthrange(d.year, d.month)[1]) for w in wanted
        )
    else:
        return []

    out: list[datetime] = []
    day = start.date()
    while day <= end.date():
        cand = datetime.combine(day, at)
        if start <= cand < end and hits(day):
            out.append(cand)
        day += timedelta(days=1)
    return out


def describe_schedule(sched: TaskSchedule | None) -> str | None:
    """计划的中文描述(如「每周一、四 09:00」),给任务卡片直接展示,前端不自己拼。"""
    if sched is None or not sched.enabled:
        return None
    if sched.freq == FREQ_DAILY:
        return f"每天 {sched.at_time}"
    days = sorted({int(d) for d in (sched.days or [])})
    if sched.freq == FREQ_WEEKLY:
        names = "、".join(_WEEKDAY_CN.get(d, str(d)) for d in days)
        return f"每周{names} {sched.at_time}"
    if sched.freq == FREQ_MONTHLY:
        names = "、".join(f"{d}日" for d in days)
        return f"每月{names} {sched.at_time}"
    return None


# ---------------------------------------------------------------- 读口


def get_schedule(db: Session, template_id: int) -> TaskSchedule | None:
    return db.scalar(select(TaskSchedule).where(TaskSchedule.template_id == template_id))


def schedules_by_template(db: Session, template_ids: list[int]) -> dict[int, TaskSchedule]:
    """一批任务的计划行,一次查询(任务列表批量打标记用,避免 N+1)。"""
    if not template_ids:
        return {}
    rows = db.scalars(select(TaskSchedule).where(TaskSchedule.template_id.in_(template_ids)))
    return {s.template_id: s for s in rows}


def planned_runs_between(
    db: Session, start: datetime, end: datetime
) -> list[tuple[datetime, int, str]]:
    """[start, end) 内**会真正入队**的定时运行:[(计划时刻, 任务 id, 任务名)],按时刻升序。

    筛选与 _tick 真正建 job 的条件一致:计划开着、任务已上线且有上线版本、有订阅者
    (无订阅者那一期只推水位不建 job,算进来会把一个不存在的高峰报给运维)。
    """
    rows = db.execute(
        select(TaskSchedule, SqlTemplate.id, SqlTemplate.name)
        .join(SqlTemplate, SqlTemplate.id == TaskSchedule.template_id)
        .where(
            TaskSchedule.enabled.is_(True),
            SqlTemplate.status == STATUS_PUBLISHED,
            SqlTemplate.published_version_id.isnot(None),
            exists().where(TaskSubscription.template_id == SqlTemplate.id),
        )
    ).all()
    out = [
        (at, tid, name)
        for sched, tid, name in rows
        for at in planned_between(sched.freq, sched.days or [], sched.at_time, start, end)
    ]
    return sorted(out, key=lambda r: (r[0], r[1]))


def subscribers_of(db: Session, template_id: int) -> list[TaskSubscription]:
    return list(
        db.scalars(
            select(TaskSubscription)
            .where(TaskSubscription.template_id == template_id)
            .order_by(TaskSubscription.id.asc())
        )
    )


def subscriber_count(db: Session, template_id: int) -> int:
    """在册订阅人数。任务详情回显用 —— 只要个数,不必加载订阅行(那会连带 joined 的 User)。"""
    return db.scalar(
        select(func.count())
        .select_from(TaskSubscription)
        .where(TaskSubscription.template_id == template_id)
    ) or 0


def subscription_facts(
    db: Session, template_ids: list[int], user_id: int
) -> tuple[dict[int, int], set[int]]:
    """任务列表用的批量事实:每个任务的订阅人数 + 当前用户已订阅的任务 id。两次查询。"""
    if not template_ids:
        return {}, set()
    counts = {
        tid: n
        for tid, n in db.execute(
            select(TaskSubscription.template_id, func.count())
            .where(TaskSubscription.template_id.in_(template_ids))
            .group_by(TaskSubscription.template_id)
        )
    }
    mine = set(
        db.scalars(
            select(TaskSubscription.template_id).where(
                TaskSubscription.template_id.in_(template_ids),
                TaskSubscription.user_id == user_id,
            )
        )
    )
    return counts, mine


def events_of(
    db: Session, template_id: int, *, limit: int = 100, offset: int = 0
) -> list[TaskSubscriptionEvent]:
    """该任务的订阅/退订留痕,倒序分页。"""
    return list(
        db.scalars(
            select(TaskSubscriptionEvent)
            .where(TaskSubscriptionEvent.template_id == template_id)
            .order_by(TaskSubscriptionEvent.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )


# ---------------------------------------------------------------- 订阅关系增删(事件同事务)


def _add_event(
    db: Session,
    *,
    template_id: int,
    user_id: int,
    action: str,
    operator_id: int | None,
    detail: dict | None = None,
) -> None:
    """写一条留痕事件。**不提交**,与订阅行的增删跟随同一个事务 —— 回滚时不留孤儿事件。"""
    db.add(
        TaskSubscriptionEvent(
            template_id=template_id,
            user_id=user_id,
            action=action,
            operator_id=operator_id,
            detail=detail,
        )
    )


def is_subscriber(db: Session, template_id: int, user_id: int) -> bool:
    """这个人此刻是不是该任务的在册订阅者。

    公开供 permission_service 的下载闸用(它要判「这一期是不是推送给他的」)——
    订阅三张表的读写口只在本模块,不让权限层自己去 select TaskSubscription。
    """
    return _subscription_row(db, template_id, user_id) is not None


def _subscription_row(db: Session, template_id: int, user_id: int) -> TaskSubscription | None:
    return db.scalar(
        select(TaskSubscription).where(
            TaskSubscription.template_id == template_id, TaskSubscription.user_id == user_id
        )
    )


def _add_subscription(
    db: Session,
    *,
    template_id: int,
    user_id: int,
    action: str,
    operator_id: int,
    added_by: int | None = None,
    detail: dict | None = None,
) -> None:
    """建一行订阅 + 一条留痕。**不提交**,跟随调用方事务。

    「订阅行与事件行是一对」这件事只在这里写一次 —— 自助订阅与代订阅各写一遍的话,
    下一个 TaskSubscription 列(added_by 就是先例)必然只被加进其中一处。
    """
    db.add(TaskSubscription(template_id=template_id, user_id=user_id, added_by=added_by))
    _add_event(
        db, template_id=template_id, user_id=user_id,
        action=action, operator_id=operator_id, detail=detail,
    )


def _drop_subscription(
    db: Session, *, template_id: int, user_id: int, action: str, operator_id: int
) -> bool:
    """删一行订阅 + 一条留痕,返回是否真的删了。**不提交**,跟随调用方事务。
    与 _add_subscription 对称:退订的四条路径(自助/自动/关闭订阅/被移出)共用这一段。"""
    sub = _subscription_row(db, template_id, user_id)
    if sub is None:
        return False
    _add_event(
        db, template_id=template_id, user_id=user_id, action=action, operator_id=operator_id
    )
    db.delete(sub)
    return True


def subscribe(db: Session, tmpl: SqlTemplate, user: User) -> bool:
    """自助订阅,幂等,返回是否新建。资格校验(can_subscribe + 计划已开启)在路由层。"""
    if _subscription_row(db, tmpl.id, user.id) is not None:
        return False
    _add_subscription(
        db, template_id=tmpl.id, user_id=user.id,
        action=SUB_EVENT_SUBSCRIBE, operator_id=user.id,
    )
    db.commit()
    return True


def unsubscribe(db: Session, template_id: int, user: User) -> bool:
    """自助退订,幂等,返回是否真的删了行。"""
    removed = _drop_subscription(
        db, template_id=template_id, user_id=user.id,
        action=SUB_EVENT_UNSUBSCRIBE, operator_id=user.id,
    )
    if removed:
        db.commit()
    return removed


# ---------------------------------------------------------------- 代订阅(管理侧)


@dataclass(frozen=True)
class SubscriberRejection:
    """一个代订阅目标为什么不能订。label 是 BatchRejectedError 明细的行首
    —— 这里被拒的单位是**人**,不是任务。"""

    label: str
    code: str      # not_resolvable / system_user / inactive
    message: str


@dataclass
class SubscriberAddition:
    """要新建的一条代订阅。missing = 这个人还缺的业务授权(view/run/download),
    写入时顺带补齐(见 permission_service.grant_business);空 = 不必补。"""

    user: User
    missing: list[str]


def _resolve_targets(db: Session, subjects) -> tuple[list[User], list[SubscriberRejection]]:
    """把入参主体(已知 user_id 或飞书 open_id)解析成一批 User,去重保序。**不提交。**

    两次批量查询 + 一次批量通讯录,与人数无关:逐人 db.get / 逐人打飞书在 50 人的上限下
    是 50 次往返 + 50 次外网请求。
    """
    from app.services import permission_service

    known_ids = [
        uid
        for sub in subjects
        if not sub.subject_open_id
        and (uid := permission_service.parse_subject_id(sub.subject_id)) is not None
    ]
    by_id = (
        {u.id: u for u in db.scalars(select(User).where(User.id.in_(known_ids)))}
        if known_ids
        else {}
    )
    by_open = permission_service.resolve_subject_users(
        db,
        [
            {
                "open_id": sub.subject_open_id,
                "name": sub.subject_name,
                "avatar": sub.subject_avatar,
            }
            for sub in subjects
            if sub.subject_open_id
        ],
    )

    users: list[User] = []
    rejections: list[SubscriberRejection] = []
    seen: set[int] = set()
    for sub in subjects:
        if sub.subject_open_id:
            user = by_open.get(sub.subject_open_id)
        else:
            uid = permission_service.parse_subject_id(sub.subject_id)
            user = by_id.get(uid) if uid is not None else None
        if user is None:
            rejections.append(
                SubscriberRejection(
                    label=sub.subject_name or "这位同事",
                    code="not_resolvable",
                    message="未能在通讯录里解析到这个人,请重新搜索后再试",
                )
            )
        elif user.id not in seen:  # 同一个人被勾了两次:去重保序,不该订两次、记两条审计
            seen.add(user.id)
            users.append(user)
    return users, rejections


def subscribe_for_plan(
    db: Session, tmpl: SqlTemplate, subjects
) -> tuple[list[SubscriberAddition], list[int], list[SubscriberRejection]]:
    """代订阅的校验相位(解析主体 + 逐人判定,只读业务表)。
    返回 (要新建的, 已在册因而跳过的 user_id, 拒绝清单)。

    「任务能不能被代订阅」(未上线 / 未开计划 / 操作者无编辑权)是**整批**的前置条件,
    由路由层判完再进来 —— 它们与目标是谁无关,放在这里逐人判会把同一句话说 N 遍。

    **已在册的人算跳过、不算拒绝**:自助订阅本身就是幂等的(返回 created=False 而非报错),
    代订阅是同一件事换个主体,不该更严格;挑五个人因为其中一个早订过就整批打回纯属添堵。

    解析 open_id 会**在校验之前就把人落库**(resolve_subject_users 要 flush 才拿得到
    自增 id) —— 所以整批被拒时调用方必须显式 db.rollback(),否则会留下一批壳用户。
    这是代订阅与批量转移作者「连回滚都不需要」的唯一差别,别照抄那句注释。
    """
    from app.services import permission_service

    targets, rejections = _resolve_targets(db, subjects)
    existing = set(
        db.scalars(
            select(TaskSubscription.user_id).where(TaskSubscription.template_id == tmpl.id)
        )
    )
    # 「这批人里谁还缺业务授权」一次问清(固定 3 次查询),不逐人建 TeamScope —— 那是
    # 一个人的全景视角,拿它回答批量问题就是 2N 次往返(见 permission_service.business_gaps_among)
    gaps = permission_service.business_gaps_among(db, targets, tmpl)

    items: list[SubscriberAddition] = []
    skipped: list[int] = []
    for user in targets:
        name = user.name or f"用户#{user.id}"
        if user.feishu_open_id == SYSTEM_SCHEDULER_OPEN_ID:
            rejections.append(
                SubscriberRejection(
                    label=name, code="system_user",
                    message="「定时运行」是平台的系统账号,不是人,不能作为订阅者",
                )
            )
        elif not user.is_active:
            rejections.append(
                SubscriberRejection(
                    label=name, code="inactive",
                    message="已停用或离职,推送到不了他手上,不能为其订阅",
                )
            )
        elif user.id in existing:
            skipped.append(user.id)
        else:
            items.append(SubscriberAddition(user=user, missing=gaps.get(user.id, [])))
    return items, skipped, rejections


def subscribe_for(
    db: Session, tmpl: SqlTemplate, items: list[SubscriberAddition], *, operator: User
) -> tuple[list[int], dict[int, list[str]]]:
    """代订阅的写入相位:补齐业务授权 + 建订阅行 + 写留痕,**一个事务**。
    返回 (created_ids, {user_id: 本次新建的授权动作})—— 后者只含真的补了授权的人。

    这里是这条链路上**唯一的 commit 点** —— permission_service.grant_business 与
    _add_subscription 都不提交;审计与通知由调用方在 commit 之后发(audit_service.log 与
    _push 都自带 commit,夹在中间会把还没写完的一批提前提交,同 template_service.add_version)。
    """
    from app.services import permission_service

    created: list[int] = []
    granted: dict[int, list[str]] = {}
    for item in items:
        actions = item.missing and permission_service.grant_business(
            db, template_id=tmpl.id, user_id=item.user.id,
            actions=item.missing, granted_by=operator.id,
        )
        if actions:
            granted[item.user.id] = actions
        # 早先的事件行记的是 {"granted_view": bool},不回写 —— 读的人按键名分辨新旧口径
        _add_subscription(
            db, template_id=tmpl.id, user_id=item.user.id,
            action=SUB_EVENT_ADDED, operator_id=operator.id, added_by=operator.id,
            detail={"granted_actions": actions},
        )
        created.append(item.user.id)
    db.commit()
    return created, granted


def unsubscribe_for(db: Session, template_id: int, user_id: int, *, operator: User) -> bool:
    """管理者把某人移出订阅者名单(代退订)。幂等,返回是否真的删了行。

    **不连带撤销 view 授权**:授权与订阅是两件事,各有各的入口与审计码;顺手回收会把操作者
    当初手工授的 view 一起吃掉,而点「移除」的人并不知道自己做了一次降权。要收权去 ➕ 授权。

    也**不校验任务是否仍在线、计划是否还开着** —— 移除是收敛动作,越好用越好;一个已离职的人
    挂在名单里,正是最该被移除的情形(同自助退订「权限被撤的人更应该退得出去」)。
    """
    removed = _drop_subscription(
        db, template_id=template_id, user_id=user_id,
        action=SUB_EVENT_REMOVED, operator_id=operator.id,
    )
    if removed:
        db.commit()
    return removed


def remove_subscriptions_for_member(
    db: Session, *, team_id: int, user_id: int, operator_id: int | None = None
) -> list[int]:
    """成员离队时清掉他对该团队任务的订阅,返回涉及的任务 id。

    通知过滤(can_view)已经 fail-closed 地挡住了推送,但订阅行留着就是僵尸:
    订阅者名单里挂着一个再也收不到推送的人。与 revoke_edit_for_member 同一取舍。
    **不提交**,跟随调用方(team_service._detach_member)事务。
    """
    subs = list(
        db.scalars(
            select(TaskSubscription).where(
                TaskSubscription.user_id == user_id,
                TaskSubscription.template_id.in_(
                    select(SqlTemplate.id).where(SqlTemplate.team_id == team_id)
                ),
            )
        )
    )
    for sub in subs:
        _add_event(
            db,
            template_id=sub.template_id,
            user_id=user_id,
            action=SUB_EVENT_MEMBER_REMOVED,
            operator_id=operator_id,
            detail={"team_id": team_id},
        )
        db.delete(sub)
    return sorted(s.template_id for s in subs)


# ---------------------------------------------------------------- 任务保存时的计划落库与卡点


def apply_template_save(
    db: Session, tmpl: SqlTemplate, sub_in, params: list, author: User
) -> list[int]:
    """任务保存时处理订阅计划。返回本次被清退(开发者关闭订阅)的订阅者 user_id ——
    通知由调用方在 commit **之后**发(notify_service 的 _push 自带 commit,夹在事务中间
    会把半成品提交出去)。

    卡点(需求:仅无变量任务可开订阅):按**提交后的净状态**判 ——
      - sub_in 为 None(本次保存未携带订阅配置)时沿用库里现状;
      - 净状态为「开启」且新版本有变量 → 拒绝保存;
      - sub_in 显式 enabled=False 视为「先关闭订阅」:清退订阅者 + 给最新结果盖
        superseded_at(回落常规保留期,免得最后一期文件永久滞留),然后放行变量。

    **不提交**,跟随调用方(template_service)事务。
    """
    sched = get_schedule(db, tmpl.id)
    net_enabled = sub_in.enabled if sub_in is not None else bool(sched and sched.enabled)
    if net_enabled and params:
        raise RubicError("已开启订阅的任务不能有变量:请先关闭订阅,或去掉 SQL 里的 :变量")

    if sub_in is None:
        return []

    if sched is None:
        sched = TaskSchedule(template_id=tmpl.id)
        db.add(sched)
    was_enabled = bool(sched.enabled)
    sched.enabled = sub_in.enabled
    sched.freq = sub_in.freq
    sched.days = list(sub_in.days)
    sched.at_time = sub_in.at_time
    sched.updated_by = author.id

    closed: list[int] = []
    if was_enabled and not sub_in.enabled:
        # 关闭订阅:清退所有订阅者(留痕),最新一期结果回落常规保留期
        for sub in subscribers_of(db, tmpl.id):
            closed.append(sub.user_id)
            _add_event(
                db,
                template_id=tmpl.id,
                user_id=sub.user_id,
                action=SUB_EVENT_CLOSED_UNSUBSCRIBE,
                operator_id=author.id,
            )
            db.delete(sub)
        _supersede_latest_result(db, tmpl.id)
    return closed


def _supersede_latest_result(
    db: Session, template_id: int, *, before_id: int | None = None
) -> None:
    """给该任务最新一条尚未被取代的成功订阅结果盖 superseded_at。不提交。
    before_id:只看这条之前的(补推替换本期时,别把刚建的新版盖掉)。"""
    latest = db.scalar(
        select(QueryJob)
        .where(
            QueryJob.template_id == template_id,
            QueryJob.source == SOURCE_SUBSCRIBE,
            QueryJob.status == JOB_SUCCESS,
            QueryJob.superseded_at.is_(None),
            *([QueryJob.id < before_id] if before_id is not None else []),
        )
        .order_by(QueryJob.id.desc())
        .limit(1)
    )
    if latest is not None:
        latest.superseded_at = datetime.now()


# ---------------------------------------------------------------- 消费打点与结算


def mark_consumed(db: Session, user_id: int, job: QueryJob) -> None:
    """订阅者下载或预览了某期订阅结果 → 推进其消费水位。幂等,水位只增不减;
    非订阅 job / 非订阅者调用零成本(UPDATE 命不中任何行)。"""
    if job.source != SOURCE_SUBSCRIBE or job.status != JOB_SUCCESS:
        return
    db.execute(
        sa_update(TaskSubscription)
        .where(
            TaskSubscription.template_id == job.template_id,
            TaskSubscription.user_id == user_id,
            or_(
                TaskSubscription.last_consumed_job_id.is_(None),
                TaskSubscription.last_consumed_job_id < job.id,
            ),
        )
        .values(last_consumed_job_id=job.id)
    )
    db.commit()


def settle_on_success(db: Session, job: QueryJob) -> list[int]:
    """下一期订阅结果成功落地时结算上一期,返回被自动清退的 user_id。

    结算时机选在这里而不是每日定时:「期」的边界只有下一期成功时才真正闭合 ——
    上一期结果这一刻被取代(盖 superseded_at,与保留期共用同一事件),而「失败的期
    不计入」自动成立:失败不触发结算,订阅者的消费窗口顺延到下一次成功。

    调用方(on_scheduled_run_finished)必须**先结算再发成功通知**,
    刚被清退的人不该再收到「数据已生成」。
    """
    prev = db.scalar(
        select(QueryJob)
        .where(
            QueryJob.template_id == job.template_id,
            QueryJob.source == SOURCE_SUBSCRIBE,
            QueryJob.status == JOB_SUCCESS,
            QueryJob.id < job.id,
        )
        .order_by(QueryJob.id.desc())
        .limit(1)
    )
    if prev is None:
        return []  # 首期,无上一期可结算

    prev.superseded_at = datetime.now()
    # 上一期若被补推替换过就有多个版本,看过其中任何一版都算看过这一期
    period_first_id = prev.period_first_id
    removed: list[tuple[int, int]] = []  # (user_id, miss_streak)
    for sub in subscribers_of(db, job.template_id):
        if sub.created_at and prev.created_at and sub.created_at > prev.created_at:
            continue  # 上期开跑后才订阅的人,那期不算他的
        if sub.last_consumed_job_id is not None and sub.last_consumed_job_id >= period_first_id:
            sub.miss_streak = 0
            continue
        sub.miss_streak += 1
        if sub.miss_streak >= settings.SUBSCRIPTION_MISS_LIMIT:
            removed.append((sub.user_id, sub.miss_streak))
            _add_event(
                db,
                template_id=job.template_id,
                user_id=sub.user_id,
                action=SUB_EVENT_AUTO_UNSUBSCRIBE,
                operator_id=None,  # 平台自动动作,无操作人
                detail={"miss_streak": sub.miss_streak},
            )
            db.delete(sub)
    db.commit()

    if removed:
        # 审计(worker 侧请求外动作,以系统用户名义 —— 与 run_query 同属既定例外)
        from app.services import audit_service

        system = scheduler_user(db)
        for uid, streak in removed:
            audit_service.log(
                db,
                user=system,
                action=ACTION_TASK_AUTO_UNSUBSCRIBE,
                resource_type=RESOURCE_TEMPLATE,
                resource_id=job.template_id,
                detail={"target_user_id": uid, "miss_streak": streak},
            )
    return [uid for uid, _ in removed]


def on_scheduled_run_finished(db: Session, job: QueryJob, error: Exception | None = None) -> None:
    """订阅定时运行落定后的编排(notify_job_done 的 subscribe 分支委托到这里)。

    成功:**先结算再通知** —— settle_on_success 可能自动清退若干人,刚被清退的人收到的
    应该是「订阅已取消」而不是紧接着一条「数据已生成」;期结算(删订阅行、给上一期盖
    superseded_at)是业务状态变更,住在本模块;收件人名单与文案照旧只在 notify_service。
    失败:能修的人收详情 + 订阅者收简讯;缺取数账号再叠加通知(与人发起的失败同口径)。
    """
    from app.services import notify_service

    tmpl = db.get(SqlTemplate, job.template_id)
    if tmpl is None:
        return
    if job.status == JOB_SUCCESS:
        _deliver(db, tmpl, job)
        return
    subscriber_ids = [s.user_id for s in subscribers_of(db, tmpl.id)]
    notify_service.notify_subscription_run_failed(
        db, tmpl, subscriber_ids, reason=(job.error or "执行失败"), job_id=job.id
    )
    if isinstance(error, CredentialRequiredError):
        notify_service.notify_credential_blocked(db, tmpl, requester_id=job.user_id, job_id=job.id)


def _deliver(db: Session, tmpl: SqlTemplate, job: QueryJob) -> int:
    """一期成功的订阅结果落地:先结算、再通知。返回收到「数据已生成/已补发」的人数。
    定时运行成功与补推共用这一段 —— 「先结算再通知」的顺序只写一次。"""
    from app.services import notify_service

    removed = settle_on_success(db, job)
    if removed:
        notify_service.notify_auto_unsubscribed(db, tmpl, removed, job_id=job.id)
    return notify_service.notify_subscription_ready(db, tmpl, job)


# ---------------------------------------------------------------- 补推(管理侧)

# 能拿来补推的运行来源:正式取数与开放 API 触发。试跑可能跑的是草稿 SQL,
# 订阅运行本身已经推过(再推一遍只是重发通知)
PUSHABLE_SOURCES = (SOURCE_RUN, SOURCE_API)


def is_push_candidate(job: QueryJob) -> bool:
    """这一行「长得像」能补推的记录吗(成功的正式取数)。运行记录列表据此决定给不给按钮,
    真正能不能推再由 push_block 判 —— 候选口径只在这里写一次。"""
    return job.source in PUSHABLE_SOURCES and job.status == JOB_SUCCESS


@dataclass
class PushContext:
    """一个任务上「能不能补推」要用的事实,一次查齐;列表逐行打标与补推接口共用它和
    push_block,两边不会一边给按钮一边拒。

    task_block 非空时其余事实都没查(任务本身就推不了,每一行都是同一个答案)。
    """

    tmpl: SqlTemplate
    task_block: RubicError | None = None
    subscriber_count: int = 0
    busy: bool = False  # 已有排队中/运行中的订阅运行
    pushed_from_ids: frozenset[int] = frozenset()
    # 本期起点:period_start 按**应用时钟**(tick 写的,给人看),period_start_db 换算到
    # **库时钟**(拿去和 created_at 比)。两者未必同一时区 —— SQLite 的 CURRENT_TIMESTAMP
    # 是 UTC —— 不掺两个时钟,同 query_service.reclaim_stale_jobs。计划从未到期过时都为 None
    period_start: datetime | None = None
    period_start_db: datetime | None = None
    # 本期已经交付过的那一版(只取判定要用的三列);None = 本期还没交付
    delivered: object | None = None


def push_context(db: Session, tmpl: SqlTemplate, candidate_ids=()) -> PushContext:
    """candidate_ids:只关心哪些记录「已经推过」时传进来,免得把全部历史补推都捞一遍。"""
    if tmpl.status != STATUS_PUBLISHED or tmpl.published_version_id is None:
        return PushContext(tmpl, RubicError("任务未上线,不能推送给订阅者"))
    sched = get_schedule(db, tmpl.id)
    if sched is None or not sched.enabled:
        return PushContext(tmpl, RubicError("任务未开启订阅"))
    n_subs = subscriber_count(db, tmpl.id)
    if n_subs == 0:
        return PushContext(tmpl, RubicError("该任务当前没有订阅者"))

    busy = db.scalar(
        select(
            exists().where(
                QueryJob.template_id == tmpl.id,
                QueryJob.source == SOURCE_SUBSCRIBE,
                QueryJob.status.in_((JOB_QUEUED, JOB_RUNNING)),
            )
        )
    )
    pushed = (
        frozenset(
            db.scalars(
                select(QueryJob.pushed_from_job_id).where(
                    QueryJob.pushed_from_job_id.in_(list(candidate_ids))
                )
            )
        )
        if candidate_ids
        else frozenset()
    )
    start = sched.last_planned_at
    start_db = None
    if start is not None:
        start_db = start + (db.scalar(select(func.now())) - datetime.now())
    latest = db.execute(
        select(QueryJob.id, QueryJob.replaces_job_id, QueryJob.created_at)
        .where(
            QueryJob.template_id == tmpl.id,
            QueryJob.source == SOURCE_SUBSCRIBE,
            QueryJob.status == JOB_SUCCESS,
        )
        .order_by(QueryJob.id.desc())
        .limit(1)
    ).first()
    delivered = latest if latest is not None and (
        start_db is None or latest.created_at >= start_db
    ) else None
    return PushContext(
        tmpl, None, subscriber_count=n_subs, busy=bool(busy), pushed_from_ids=pushed,
        period_start=start, period_start_db=start_db, delivered=delivered,
    )


def push_block(ctx: PushContext, job: QueryJob) -> RubicError | None:
    """这条运行记录**不能**补推的原因;能推返回 None。状态冲突是 ConflictError(409),
    其余是「这条记录不符合条件」(400)。顺序按「先说任务、再说这条记录」。"""
    from app.services import result_service

    if ctx.task_block is not None:
        return ctx.task_block
    if job.source not in PUSHABLE_SOURCES:
        return RubicError("只能推送正式取数的结果(试跑与定时运行的记录不能推送)")
    if job.status != JOB_SUCCESS or not job.result_object_key:
        return RubicError("只能推送运行成功的结果")
    if job.template_version_id != ctx.tmpl.published_version_id:
        return RubicError("这条结果来自旧版本,请用当前上线版本重新取数后再推送")
    if ctx.period_start_db is not None and job.created_at < ctx.period_start_db:
        return RubicError(
            f"这条结果早于本期计划时刻({ctx.period_start:%Y-%m-%d %H:%M}),"
            "可能已经过时,请重新取数后再推送"
        )
    if job.id in ctx.pushed_from_ids:
        return ConflictError(_PUSH_DUPLICATE)
    if ctx.busy:
        return ConflictError("本期定时运行正在进行,请等它结束后再推送")
    if result_service.is_gone(job):  # 最后判:唯一要碰磁盘的一条
        return RubicError("这条结果已过期或文件已不存在,请重新取数")
    return None


_PUSH_DUPLICATE = "这条结果已经推送过给订阅者"


def push_job_to_subscribers(
    db: Session, tmpl: SqlTemplate, source: QueryJob, operator: User
) -> tuple[QueryJob, int]:
    """把一条已确认的运行结果补推给订阅者,作为本期订阅结果。返回 (新订阅记录, 通知人数)。

    定时运行失败后平台不重试,而「补跑即群发」意味着推出去之前没人看过结果 —— 所以依据
    是一条**已经跑完、操作人看过**的运行记录,不重跑 SQL。

    做法是另建一条 source=subscribe 的记录、与来源记录共用结果文件:只有 view 授权的
    订阅者只看得见、取得走订阅记录(job_visibility_condition / can_download_job),
    而可见 / 下载 / 保留(protected_result_keys 按 key 保护)/ 消费打点全部因此沿用
    定时运行那一套。user_id 照旧填系统用户:填成操作人会让 can_access_job 的「本人」
    短路放行他人。

    - 本期还没交付(定时运行失败了):这就是本期,照常结算 + 通知「已补发」;
    - 本期已经交付过:这是「替换本期」—— 期没有闭合,不结算消费,只让旧版进入保留期,
      通知「已更新」。replaces_job_id 指向本期最早一版,看过任一版都算看过本期。

    操作人权限(编辑权 + 看得见来源记录)在路由层判;审计也在路由层写。
    """
    ctx = push_context(db, tmpl, [source.id])
    block = push_block(ctx, source)
    if block is not None:
        raise block

    job = QueryJob.sharing_result_of(
        source,
        user_id=scheduler_user(db).id,
        params={},
        source=SOURCE_SUBSCRIBE,
        pushed_by_id=operator.id,
        pushed_from_job_id=source.id,
        replaces_job_id=(
            (ctx.delivered.replaces_job_id or ctx.delivered.id) if ctx.delivered else None
        ),
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:  # 并发下两次推送同一条记录:唯一索引兜底
        db.rollback()
        raise ConflictError(_PUSH_DUPLICATE)
    db.refresh(job)

    if job.replaces_job_id is None:
        return job, _deliver(db, tmpl, job)
    from app.services import notify_service

    _supersede_latest_result(db, tmpl.id, before_id=job.id)
    db.commit()
    return job, notify_service.notify_subscription_ready(db, tmpl, job)


# ---------------------------------------------------------------- 调度(worker 侧)


def tick(now: datetime | None = None) -> int:
    """扫描到期的订阅计划并入队,返回触发的任务数。自管会话(worker 请求外调用)。

    每条计划:任务必须 published(下线即暂停,不推水位 → 重新上线后自动补跑最近一期);
    水位原子推进成功才 fire;无订阅者时**推水位但不建 job**(该期作废 —— 否则几小时后
    有人订阅时会突然「补跑」一期陈旧计划)。
    """
    db = SessionLocal()
    try:
        return _tick(db, now or datetime.now())
    finally:
        db.close()


def _tick(db: Session, now: datetime) -> int:
    from app.services import notify_service, query_service

    fired = 0
    scheds = list(db.scalars(select(TaskSchedule).where(TaskSchedule.enabled.is_(True))))
    for sched in scheds:
        # 先做纯内存的 due 判定:绝大多数轮次一条计划都不到期,任务行留到确认到期后再取
        # —— 否则这个 30 秒热循环每轮都对每条计划白查一次模板
        planned = latest_planned_at(sched.freq, sched.days or [], sched.at_time, now)
        if planned is None:
            continue
        if sched.last_planned_at is not None and planned <= sched.last_planned_at:
            continue
        tmpl = db.get(SqlTemplate, sched.template_id)
        if tmpl is None or tmpl.status != STATUS_PUBLISHED or tmpl.published_version_id is None:
            continue  # 已下线/未上线:暂停,不推水位(重新上线后自动补跑最近一期)
        # 原子推进水位 = 去重锁:rowcount==1 才继续,多实例/重复 tick 下同一期只 fire 一次
        rowcount = (
            db.execute(
                sa_update(TaskSchedule)
                .where(
                    TaskSchedule.id == sched.id,
                    or_(
                        TaskSchedule.last_planned_at.is_(None),
                        TaskSchedule.last_planned_at < planned,
                    ),
                )
                .values(last_planned_at=planned)
            ).rowcount
            or 0
        )
        db.commit()
        if rowcount != 1:
            continue

        subs = subscribers_of(db, tmpl.id)
        if not subs:
            continue  # 无订阅者:该期不运行(需求 4),水位已推进
        subscriber_ids = [s.user_id for s in subs]
        try:
            query_service.enqueue_scheduled(db, tmpl)
            fired += 1
        except CredentialRequiredError as e:
            notify_service.notify_credential_blocked(
                db, tmpl, requester_id=scheduler_user(db).id
            )
            notify_service.notify_subscription_run_failed(
                db, tmpl, subscriber_ids, reason=str(e)
            )
        except Exception as e:  # noqa: BLE001 -- 单个任务入队失败不拖垮整轮扫描
            log.exception("订阅计划入队失败 template=%s: %s", tmpl.id, e)
            notify_service.notify_subscription_run_failed(
                db, tmpl, subscriber_ids, reason=str(e)[:200]
            )
    return fired


# ---------------------------------------------------------------- 结果保留(worker 清理侧)


def protected_result_keys() -> frozenset[str]:
    """尚未过期的订阅结果文件 key,worker 清理时跳过它们。自管会话(请求外调用)。

    口径与 QueryJob.result_expired 的 subscribe 分支互为镜像:未被下一期取代,
    或还没过常规保留天数,都不许删。两边必须一起改。
    """
    db = SessionLocal()
    try:
        cutoff = datetime.now() - timedelta(days=settings.RESULT_RETENTION_DAYS)
        rows = db.scalars(
            select(QueryJob.result_object_key).where(
                QueryJob.source == SOURCE_SUBSCRIBE,
                QueryJob.status == JOB_SUCCESS,
                QueryJob.result_object_key.is_not(None),
                or_(QueryJob.superseded_at.is_(None), QueryJob.created_at > cutoff),
            )
        )
        return frozenset(rows)
    finally:
        db.close()

"""取数通知:优先飞书机器人推送,始终写一条站内通知作镜像/兜底。

「一次运行该通知谁」这条策略只住在本模块:发起人恒收;若失败原因是**只有别人能修**的
(当前只有「任务所属团队没登记取数账号」一种),再额外通知那些能修的人 ——
即该团队的**团队管理员**(团队账号由他们维护,作者本人可能也无权配)。

「谁能修」的判定不外泄:worker 侧的共享失败路径把原始异常交过来(notify_job_done 按类型分派),
入队阶段则因为**还没有运行记录行**而直接叫 notify_credential_blocked —— 两条路径调用形态不同,
但收件人名单与文案仍只在本模块写一次。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import CredentialRequiredError
from app.core.logging_setup import get_logger
from app.models.notification import Notification
from app.models.query_job import JOB_SUCCESS, SOURCE_SUBSCRIBE, QueryJob
from app.models.template import SqlTemplate
from app.models.user import ROLE_ADMIN, SYSTEM_SCHEDULER_OPEN_ID, User
from app.services import feishu_service

log = get_logger("rubick.notify")


def _push(
    db: Session,
    *,
    user_id: int,
    title: str,
    body: str,
    level: str,
    link: str,
    job_id: int | None,
    template_id: int,
) -> Notification | None:
    """投递一条通知:先试飞书,再写站内记录(飞书失败也要留下站内那条)。

    所有通知都走这一个出口 —— 以后加 Notification 列、加限流、换飞书卡片模板,只改这里。
    job_id 可空:入队阶段就被拦下的取数根本没有运行记录行(见 notify_credential_blocked),
    而那条通知照样要发。

    **系统用户(定时运行)永远收不到通知**:它不是人,登录不进来,给它的通知没有任何人会读到。
    收件人算成了它,只可能是上游把「定时运行的发起人」当成了「该通知的人」—— 定时运行要通知的
    是订阅者(见 on_scheduled_run_finished)。所以这里丢弃并告警,而不是静默写一条没人看的记录:
    2026-08-25 就有一条这样的通知躺在系统用户名下,而订阅者什么都没收到。
    """
    user = db.get(User, user_id)
    if user is not None and user.feishu_open_id == SYSTEM_SCHEDULER_OPEN_ID:
        log.warning(
            "收件人是系统用户(定时运行),已丢弃该通知:title=%s job_id=%s template_id=%s",
            title, job_id, template_id,
        )
        return None
    sent = bool(user) and feishu_service.send_message(user.feishu_open_id, title, body, link)
    note = Notification(
        user_id=user_id,
        title=title,
        body=body,
        job_id=job_id,
        template_id=template_id,
        level=level,
        feishu_sent=sent,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


def _team_fixers(db: Session, tmpl: SqlTemplate, *, include_author: bool = False) -> list[int]:
    """能对该任务负责的人:团队管理员(include_author 时再加**仍在队的**作者)。

    一个都找不到时**退化为通知全部平台管理员** —— 「消息不许掉进无人区」这条兜底
    只在这里表述一次:否则业务用户/订阅者一直干等,而能修的人毫不知情。
    两个消费方:缺取数账号(notify_credential_blocked,只要团队管理员 —— 账号由他们维护)
    与订阅运行失败(notify_subscription_run_failed,作者也要知道 —— SQL 是他写的)。
    """
    from app.services import team_service

    out: list[int] = []
    if tmpl.team_id is not None:
        out.extend(team_service.team_admin_ids(db, tmpl.team_id))
        if include_author:
            author = db.get(User, tmpl.author_id)
            if author is not None and author.id not in out and team_service.is_member(
                db, author, tmpl.team_id
            ):
                out.append(author.id)
    if out:
        return out
    return list(
        db.scalars(select(User.id).where(User.role == ROLE_ADMIN, User.is_active.is_(True)))
    )


def _push_each(
    db: Session,
    user_ids: list[int],
    *,
    title: str,
    body: str,
    level: str,
    link: str,
    job_id: int | None,
    template_id: int,
) -> None:
    """同一条通知逐个投递给一批人。订阅类通知全是「一份文案 × N 个收件人」的形状,
    收在这里,加通知字段时不必改六个循环。"""
    for uid in user_ids:
        _push(
            db, user_id=uid, title=title, body=body, level=level,
            link=link, job_id=job_id, template_id=template_id,
        )


def notify_credential_blocked(
    db: Session, tmpl: SqlTemplate | None, *, requester_id: int, job_id: int | None = None
) -> None:
    """任务因「团队账号未登记」跑不动时,额外给能修的人提个醒。

    常规失败通知只发给发起人,但这件事只有团队管理员能修 —— 不通知他们,业务用户会一直
    干等,而能修的人毫不知情。恰好是发起人本人时不重复发。

    两个调用点、两个阶段:入队时(query_service.enqueue,**常见落点**,那时还没有运行记录行,
    故 job_id 为空)与 worker 执行时(排队期间凭证被改掉/回收)。
    """
    if tmpl is None:
        return
    team_label = f"团队《{tmpl.team_name}》" if tmpl.team_name else "该任务所属团队"
    for uid in _team_fixers(db, tmpl):
        if uid == requester_id:
            continue
        _push(
            db,
            user_id=uid,
            title="任务取数被阻断:缺少团队取数账号",
            body=(
                f"有人运行《{tmpl.name}》时失败:{team_label}尚未登记该任务数据源的取数账号。"
                "请到团队页的「团队取数账号」登记一套。"
            ),
            level="error",
            link=(
                f"{settings.APP_BASE_URL}/teams/{tmpl.team_id}?tab=credentials"
                if tmpl.team_id
                else f"{settings.APP_BASE_URL}/teams"
            ),
            job_id=job_id,
            template_id=tmpl.id,
        )


def notify_job_done(db: Session, job: QueryJob, error: Exception | None = None) -> Notification | None:
    """通知发起人本次运行的结果;error 是失败时的原始异常,用于判断还该通知谁。

    订阅定时运行(source=subscribe)的收件人完全不同(发起人是系统用户,没人在等它),
    在入口处整体分派 —— reclaim_stale_jobs 收回的订阅 job 也天然走同一条路。
    分派后委托给 subscription_service:期结算(清退、盖 superseded_at)是业务状态变更,
    不住在通知层;本模块只负责收件人与文案。
    """
    if job.source == SOURCE_SUBSCRIBE:
        from app.services import subscription_service

        subscription_service.on_scheduled_run_finished(db, job, error)
        return None
    tmpl = db.get(SqlTemplate, job.template_id)
    tmpl_name = tmpl.name if tmpl else f"任务#{job.template_id}"

    if job.status == JOB_SUCCESS:
        title = "取数完成"
        body = f"《{tmpl_name}》已跑完,共 {job.row_count} 行,可在该任务的「运行记录」里预览并下载。"
        level = "success"
    else:
        title = "取数失败"
        body = f"《{tmpl_name}》执行失败:{(job.error or '')[:120]}"
        level = "error"

    note = _push(
        db,
        user_id=job.user_id,
        title=title,
        body=body,
        level=level,
        # 深链到该任务的运行记录(前端 /tasks 读 records 参数打开对应抽屉)
        link=f"{settings.APP_BASE_URL}/tasks?records={job.template_id}",
        job_id=job.id,
        template_id=job.template_id,
    )
    if isinstance(error, CredentialRequiredError):
        notify_credential_blocked(db, tmpl, requester_id=job.user_id, job_id=job.id)
    return note


# ---------------------------------------------------------------- 任务订阅(定时运行)

def notify_subscription_run_failed(
    db: Session,
    tmpl: SqlTemplate,
    subscriber_ids: list[int],
    *,
    reason: str,
    job_id: int | None = None,
) -> None:
    """订阅定时运行失败:能修的人收详情,订阅者收简讯(两边都要知道,但要说的话不同 ——
    订阅者修不了配置,给他细节只是徒增困惑)。同一人兼具两个身份时只收详情那条。

    reason 必须是**已脱敏**的文案(job.error 已由 execute_job 脱敏;入队阶段的异常
    不含库账号)。缺取数账号的「通知能修的人」由调用方另行叠加 notify_credential_blocked,
    这里不重复 —— 否则团队管理员会为同一件事收到两条。
    """
    link = f"{settings.APP_BASE_URL}/tasks?records={tmpl.id}"
    managers = _team_fixers(db, tmpl, include_author=True)
    _push_each(
        db, managers,
        title="订阅任务定时运行失败",
        body=f"《{tmpl.name}》按订阅计划自动运行失败:{reason[:200]}。修复前订阅者收不到本期数据。",
        level="error", link=link, job_id=job_id, template_id=tmpl.id,
    )
    _push_each(
        db, [uid for uid in subscriber_ids if uid not in managers],
        title="订阅数据本期生成失败",
        body=f"你订阅的《{tmpl.name}》本期数据生成失败,平台已通知任务负责人处理。",
        level="error", link=link, job_id=job_id, template_id=tmpl.id,
    )


def notify_subscription_paused(db: Session, tmpl: SqlTemplate, subscriber_ids: list[int]) -> None:
    """任务下线 → 订阅推送暂停(订阅关系保留,重新上线自动恢复)。"""
    _push_each(
        db, subscriber_ids,
        title="订阅推送已暂停",
        body=f"《{tmpl.name}》已下线,订阅推送随之暂停;任务重新上线后自动恢复,无需重新订阅。",
        level="info", link=f"{settings.APP_BASE_URL}/tasks", job_id=None, template_id=tmpl.id,
    )


def notify_subscription_closed(db: Session, tmpl: SqlTemplate, user_ids: list[int]) -> None:
    """开发者关闭了任务的订阅功能 → 告知被清退的订阅者。"""
    _push_each(
        db, user_ids,
        title="订阅已取消",
        body=f"《{tmpl.name}》的订阅功能已被开发者关闭,你的订阅随之取消。",
        level="info", link=f"{settings.APP_BASE_URL}/tasks", job_id=None, template_id=tmpl.id,
    )


def notify_auto_unsubscribed(
    db: Session, tmpl: SqlTemplate, user_ids: list[int], *, job_id: int | None
) -> None:
    """连续未消费被自动清退 → 告知本人(结算本身在 subscription_service.settle_on_success)。"""
    _push_each(
        db, user_ids,
        title="订阅已自动取消",
        body=(
            f"你订阅的《{tmpl.name}》已连续 {settings.SUBSCRIPTION_MISS_LIMIT} 期"
            "未查看数据,为节约取数资源,平台已自动取消订阅;如仍需要,可随时重新订阅。"
        ),
        level="info", link=f"{settings.APP_BASE_URL}/tasks?records={tmpl.id}",
        job_id=job_id, template_id=tmpl.id,
    )


def notify_subscription_ready(db: Session, tmpl: SqlTemplate, job: QueryJob) -> None:
    """本期订阅数据生成成功 → 通知仍在册且仍 can_view 的订阅者(收件人策略住本模块)。

    权限被撤即不再推送,fail-closed;订阅行留给自动清退或本人退订收拾。
    调用方(subscription_service.on_scheduled_run_finished)保证**先结算再调这里**,
    刚被清退的人不会再收到「数据已生成」。
    """
    from app.services import permission_service, subscription_service

    ready_ids = []
    for sub in subscription_service.subscribers_of(db, tmpl.id):
        user = sub.user  # 订阅行 lazy="joined",用户已随行加载
        if user is None:
            continue
        if not permission_service.can_view(permission_service.team_scope(db, user), tmpl):
            continue
        ready_ids.append(user.id)
    _push_each(
        db, ready_ids,
        title="订阅数据已生成",
        body=(
            f"你订阅的《{tmpl.name}》本期数据已生成,共 {job.row_count} 行,"
            "可到该任务的「运行记录」里预览并下载。"
        ),
        level="success", link=f"{settings.APP_BASE_URL}/tasks?records={tmpl.id}",
        job_id=job.id, template_id=tmpl.id,
    )

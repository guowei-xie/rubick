"""取数通知:优先飞书机器人推送,始终写一条站内通知作镜像/兜底。

「一次运行该通知谁」这条策略只住在本模块:发起人恒收;若失败原因是**只有别人能修**的
(当前只有「任务所属团队未配置/未测通取数账号」一种),再额外通知那些能修的人 ——
即该团队的**团队管理员**(团队账号由他们维护,作者本人可能也无权配)。
query_service 只负责把失败原因交过来,不在共享失败路径上按异常类型分叉。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import CredentialRequiredError
from app.models.notification import Notification
from app.models.query_job import JOB_SUCCESS, QueryJob
from app.models.template import SqlTemplate
from app.models.user import User
from app.services import feishu_service


def _push(
    db: Session,
    *,
    user_id: int,
    title: str,
    body: str,
    level: str,
    link: str,
    job: QueryJob,
    template_id: int,
) -> Notification:
    """投递一条通知:先试飞书,再写站内记录(飞书失败也要留下站内那条)。

    所有通知都走这一个出口 —— 以后加 Notification 列、加限流、换飞书卡片模板,只改这里。
    """
    user = db.get(User, user_id)
    sent = bool(user) and feishu_service.send_message(user.feishu_open_id, title, body, link)
    note = Notification(
        user_id=user_id,
        title=title,
        body=body,
        job_id=job.id,
        template_id=template_id,
        level=level,
        feishu_sent=sent,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


def _credential_fixers(db: Session, tmpl: SqlTemplate) -> list[int]:
    """能修「团队账号未就绪」的人:该团队的团队管理员。

    团队一个管理员都没有(被平台管理员清空过)时**退化为通知全部平台管理员** ——
    否则这条消息会掉进无人区,业务用户永远等不到有人去配账号。
    """
    from app.models.user import ROLE_ADMIN, User as UserModel
    from app.services import team_service

    if tmpl.team_id is not None:
        admins = team_service.team_admin_ids(db, tmpl.team_id)
        if admins:
            return admins
    return list(
        db.scalars(
            select(UserModel.id).where(
                UserModel.role == ROLE_ADMIN, UserModel.is_active.is_(True)
            )
        )
    )


def _notify_credential_blocked(db: Session, tmpl: SqlTemplate, job: QueryJob) -> None:
    """任务因「团队账号未配置/未测通」跑不动时,额外给能修的人提个醒。

    常规失败通知只发给发起人,但这件事只有团队管理员能修 —— 不通知他们,业务用户会一直
    干等,而能修的人毫不知情。恰好是发起人本人时不重复发。
    """
    if tmpl is None:
        return
    team_label = f"团队《{tmpl.team_name}》" if tmpl.team_name else "该任务所属团队"
    for uid in _credential_fixers(db, tmpl):
        if uid == job.user_id:
            continue
        _push(
            db,
            user_id=uid,
            title="任务取数被阻断:缺少团队取数账号",
            body=(
                f"有人运行《{tmpl.name}》时失败:{team_label}尚未配置该任务数据源的取数账号,"
                "或账号未通过连接测试。请到团队页的「团队取数账号」配置并测通。"
            ),
            level="error",
            link=(
                f"{settings.APP_BASE_URL}/teams/{tmpl.team_id}?tab=credentials"
                if tmpl.team_id
                else f"{settings.APP_BASE_URL}/teams"
            ),
            job=job,
            template_id=tmpl.id,
        )


def notify_job_done(db: Session, job: QueryJob, error: Exception | None = None) -> Notification:
    """通知发起人本次运行的结果;error 是失败时的原始异常,用于判断还该通知谁。"""
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
        job=job,
        template_id=job.template_id,
    )
    if isinstance(error, CredentialRequiredError):
        _notify_credential_blocked(db, tmpl, job)
    return note

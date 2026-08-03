"""任务完成通知:优先飞书机器人推送,始终写一条站内通知作镜像/兜底。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.notification import Notification
from app.models.query_job import JOB_SUCCESS, QueryJob
from app.models.template import SqlTemplate
from app.models.user import User
from app.services import feishu_service


def notify_job_done(db: Session, job: QueryJob) -> Notification:
    user = db.get(User, job.user_id)
    tmpl = db.get(SqlTemplate, job.template_id)
    tmpl_name = tmpl.name if tmpl else f"模板#{job.template_id}"
    # 深链到该任务的运行记录(前端 /tasks 读 records 参数打开对应抽屉)
    link = f"{settings.APP_BASE_URL}/tasks?records={job.template_id}"

    if job.status == JOB_SUCCESS:
        title = "取数完成"
        body = f"《{tmpl_name}》已跑完,共 {job.row_count} 行,可前往「我的任务」下载。"
        level = "success"
    else:
        title = "取数失败"
        body = f"《{tmpl_name}》执行失败:{(job.error or '')[:120]}"
        level = "error"

    sent = False
    if user:
        sent = feishu_service.send_message(user.feishu_open_id, title, body, link)

    note = Notification(
        user_id=job.user_id,
        title=title,
        body=body,
        job_id=job.id,
        template_id=job.template_id,
        level=level,
        feishu_sent=sent,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note

"""取数通知:优先飞书机器人推送,始终写一条站内通知作镜像/兜底。

「一次运行该通知谁」这条策略只住在本模块:发起人恒收(**开放 API 触发的除外**,见
notify_job_done);若失败原因是**只有别人能修**的(当前只有「任务所属团队没登记取数账号」一种),
再额外通知那些能修的人 —— 即该团队的**团队管理员**(团队账号由他们维护,作者本人可能也无权配)。

「谁能修」的判定不外泄:worker 侧的共享失败路径把原始异常交过来(notify_job_done 按类型分派),
入队阶段则因为**还没有运行记录行**而直接叫 notify_credential_blocked —— 两条路径调用形态不同,
但收件人名单与文案仍只在本模块写一次。
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import CredentialRequiredError
from app.core.logging_setup import get_logger
from app.models.notification import Notification
from app.models.query_job import JOB_SUCCESS, SOURCE_API, SOURCE_SUBSCRIBE, QueryJob
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
    template_id: int | None,
) -> Notification | None:
    """投递一条通知:先试飞书,再写站内记录(飞书失败也要留下站内那条)。

    所有通知都走这一个出口 —— 以后加 Notification 列、加限流、换飞书卡片模板,只改这里。
    job_id 可空:入队阶段就被拦下的取数根本没有运行记录行(见 notify_credential_blocked),
    而那条通知照样要发。
    template_id 同样可空,理由不同:**汇总类通知横跨多个任务,没有「那一个」可指**
    (见 notify_author_transferred_batch)。挑一个当代表会让铃铛把人带到一个与标题不符的
    任务页;留空则前端只标已读、不跳转(NotificationBell 已按 template_id 真值判断)。

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


def _records_link(template_id: int, job_id: int | None = None) -> str:
    """「去看这个任务的运行记录」这条深链的唯一出处(前端 /tasks 读 records / job 参数)。

    带上 job_id 时,落地页会把通知说的**那一次**运行标出来 —— 定时运行攒了几十期,
    订阅者要的是本期那条而不是列表第一行。从前四处各拼一份 f-string,加 &job= 时
    只改到了其中一处,另外几条通知照旧落在「一列记录,自己找」。签名收在这里之后,
    「通知指向具体某次运行」由参数保证,不靠每个调用点记得。
    """
    url = f"{settings.APP_BASE_URL}/tasks?records={template_id}"
    return f"{url}&job={job_id}" if job_id else url


def _tasks_link() -> str:
    """「去任务列表」这条链接的唯一出处。理由同 _records_link:此前 f"{APP_BASE_URL}/tasks"
    散在好几处,子路径部署(/rubick 前缀)一改就得满文件找。"""
    return f"{settings.APP_BASE_URL}/tasks"


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


# 「缺团队取数账号」说的是一个**等人去修的状态**,不是一次性事件:修好之前,每一次运行都会
# 再撞一次同一件事。开放 API 之后这不再是理论问题 —— 一个循环重试的 Agent 能在几分钟内把
# 团队管理员的铃铛刷满,而那几十条说的是同一句话、指向同一个修法。故按(收件人, 任务)在冷却窗
# 内去重。标题提成常量:它同时是文案与去重键,两处对不上就等于没去重。
CREDENTIAL_ALERT_TITLE = "任务取数被阻断:缺少团队取数账号"
CREDENTIAL_ALERT_COOLDOWN_MINUTES = 60


def _alerted_recently(db: Session, *, user_id: int, template_id: int) -> bool:
    """这个人在冷却窗内已经为这个任务收过缺账号告警了吗。

    去重状态直接问 Notification 表,不另建表、不用进程内字典:这条告警由 web 进程(入队阶段)
    与 worker 进程(执行阶段)分别发出,进程内的记号互相看不见,重启也就丢了;而站内通知本来
    就是每条通知的持久镜像,天然跨进程、跨重启。
    """
    # cutoff 必须取**库时钟**:created_at 是 server_default=func.now() 落的库时间,
    # 应用进程与库的时区/时钟未必一致(同 query_service.reclaim_stale_jobs 的算法)。
    cutoff = db.scalar(select(func.now())) - timedelta(minutes=CREDENTIAL_ALERT_COOLDOWN_MINUTES)
    return bool(
        db.scalar(
            select(func.count(Notification.id)).where(
                Notification.user_id == user_id,
                Notification.template_id == template_id,
                Notification.title == CREDENTIAL_ALERT_TITLE,
                Notification.created_at >= cutoff,
            )
        )
    )


def notify_credential_blocked(
    db: Session, tmpl: SqlTemplate | None, *, requester_id: int, job_id: int | None = None
) -> None:
    """任务因「团队账号未登记」跑不动时,额外给能修的人提个醒。

    常规失败通知只发给发起人,但这件事只有团队管理员能修 —— 不通知他们,业务用户会一直
    干等,而能修的人毫不知情。恰好是发起人本人时不重复发。

    两个调用点、两个阶段:入队时(query_service.enqueue,**常见落点**,那时还没有运行记录行,
    故 job_id 为空)与 worker 执行时(排队期间凭证被改掉/回收)。

    **这条告警不随来源静音**:API 触发的运行不通知发起人(它在轮询),但能修的人照样要知道 ——
    否则调用方一直失败,而账号迟迟没人去配。取而代之的是冷却窗去重(见上方常量)。
    """
    if tmpl is None:
        return
    team_label = f"团队《{tmpl.team_name}》" if tmpl.team_name else "该任务所属团队"
    for uid in _team_fixers(db, tmpl):
        if uid == requester_id:
            continue
        if _alerted_recently(db, user_id=uid, template_id=tmpl.id):
            continue
        _push(
            db,
            user_id=uid,
            title=CREDENTIAL_ALERT_TITLE,
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

    按 job.source 在入口处整体分派,两种来源的收件人与常规路径都不同:

    - **订阅定时(subscribe)**:收件人完全不同(发起人是系统用户,没人在等它),委托给
      subscription_service —— 期结算(清退、盖 superseded_at)是业务状态变更,不住在通知层;
      本模块只负责收件人与文案。reclaim_stale_jobs 收回的订阅 job 也天然走同一条路。
    - **开放 API(api)**:**不通知任何人**。通知是给「在界面上等结果的人」用的,而 API 的
      调用方是脚本或 Agent,它靠轮询 GET /api/v1/runs/{id} 拿 status 与 error,这条通知对它
      是纯重复信息;API 调用又天然高频,几百次调用就是几百条飞书卡片,会把 token 主人真正在
      界面上跑的那几条通知一起淹掉。成功失败一视同仁:失败原因在轮询响应的 error 里。

    唯一不受 api 静音影响的是**缺团队取数账号**的告警 —— 它的收件人是能修的团队管理员而不是
    发起人,与「谁在等这次结果」无关(它自己按冷却窗去重,见 notify_credential_blocked)。
    """
    if job.source == SOURCE_SUBSCRIBE:
        from app.services import subscription_service

        subscription_service.on_scheduled_run_finished(db, job, error)
        return None
    tmpl = db.get(SqlTemplate, job.template_id)

    note = None
    if job.source != SOURCE_API:
        tmpl_name = tmpl.name if tmpl else f"任务#{job.template_id}"
        if job.status == JOB_SUCCESS:
            title = "取数完成"
            body = (
                f"《{tmpl_name}》已跑完,共 {job.row_count} 行,可在该任务的「运行记录」里预览并下载。"
            )
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
            link=_records_link(job.template_id, job.id),
            job_id=job.id,
            template_id=job.template_id,
        )
    if isinstance(error, CredentialRequiredError):
        notify_credential_blocked(db, tmpl, requester_id=job.user_id, job_id=job.id)
    return note


# ---------------------------------------------------------------- 任务作者转移
# 两条忠告(新作者「权限从哪来」、原作者「不再能改 + 真要改怎么办」)是这个功能唯一会
# 引发工单的两句话。单任务与批量只差主语和单复数,所以它们只在这里各写一次 ——
# 分开写两份的结果是改一处漏一处,而且没有任何测试会因此变红。


def _new_author_body(subject: str, by: str, *, plural: bool) -> str:
    these, their = ("这些任务", "它们的") if plural else ("这个任务", "该任务的")
    return (
        f"{subject}的作者已转给你{by}。"
        f"你现在可以编辑、上下线{these},并为业务同事授权;"
        f"{their}定时运行失败时也会通知你。"
    )


def _old_author_body(subject: str, by: str, *, plural: bool) -> str:
    # 批量可能横跨几个团队,所以复数那版说「所属团队」而不是「本团队」
    them, whose, team = (
        ("它们", "对应任务", "所属团队") if plural else ("该任务", "该任务", "本团队")
    )
    return (
        f"{subject}的作者已由你转为他人{by}。"
        f"除非你是{team}的团队管理员,否则你对{them}不再有编辑权(仍可见、可运行);"
        f"如仍需编辑,请让团队管理员单独授予你{whose}的编辑权。"
    )


def notify_author_transferred(
    db: Session, tmpl: SqlTemplate, *, old_author_id: int, operator: User
) -> None:
    """任务作者转移 → 新旧作者各收一条,**两边说的话不同**。

    新作者要知道「这件事从今天起归你」以及权限从哪来;原作者要知道「你不再能改它」
    **以及真要改怎么办** —— 少了后半句,他只会在列表里发现编辑按钮没了然后来问人。
    两条都写明操作人:团队管理员代办的离职交接里,收信的两个人都不是发起人。

    原作者已停用时跳过他那条 —— 判断收在 _is_notifiable(与批量版共用),那里也记着
    「为什么不塞进 _push」。
    """
    link = _records_link(tmpl.id)
    team = f"团队《{tmpl.team_name}》" if tmpl.team_name else "该团队"
    by = f"(由 {operator.name} 操作)"

    # 新作者不另传:调用点在 commit 之后,tmpl.author_id 就是他,
    # 多一个参数只是多一个能与事实矛盾的入口
    _push(
        db,
        user_id=tmpl.author_id,
        title="你被指定为任务作者",
        body=_new_author_body(f"{team}的任务《{tmpl.name}》", by, plural=False),
        level="info",
        link=link,
        job_id=None,
        template_id=tmpl.id,
    )

    if not _is_notifiable(db, old_author_id):
        return
    _push(
        db,
        user_id=old_author_id,
        title="你的任务已移交他人",
        body=_old_author_body(f"{team}的任务《{tmpl.name}》", by, plural=False),
        level="info",
        link=link,
        job_id=None,
        template_id=tmpl.id,
    )


def _is_notifiable(db: Session, user_id: int) -> bool:
    """这个人还收得到通知吗。离职清理置 is_active=False,人已经登录不进来了。

    单任务与批量两条路共用 —— 这道判断刻意留在本模块、**不塞进 _push**:那里是所有通知的
    公共出口,给它加一条全局过滤会连带改掉订阅、取数失败等所有通知的行为,爆炸半径远超本功能。
    """
    user = db.get(User, user_id)
    return user is not None and user.is_active


#: 通知正文里最多点名几个任务。一次交接可能有几十个,全抄进飞书卡片没人读得下去
_LISTED_TASKS = 10


def _task_list(tmpls: list[SqlTemplate]) -> str:
    """通知正文里的任务清单;超出就收尾成「等共 N 个」。"""
    head = "、".join(f"《{t.name}》" for t in tmpls[:_LISTED_TASKS])
    return head if len(tmpls) <= _LISTED_TASKS else f"{head} 等共 {len(tmpls)} 个"


def notify_author_transferred_batch(
    db: Session, *, items: list[tuple[SqlTemplate, int]], operator: User
) -> None:
    """一批任务的作者转移 → 新作者收**一条**汇总,每个原作者各收**一条**汇总。

    items = [(任务, 该任务的原作者 id)]。一批里原作者可能不止一个(多选未必都是同一人的
    任务),故按原作者分组 —— 逐条发会让一次 12 个任务的交接给同一个人发 12 条飞书。

    **只有一条时直接转调单任务版**:批量入口勾了一个任务时,收到的通知应当与从 ⋮ 菜单转
    逐字一致,而不是第二套措辞。

    **合并的是「收件人条数」,不是「一次 API 发给多人」**:飞书批量接口的坑是一个失效
    open_id 会让整批 400(见 feishu_service),所以这里仍然逐人 _push —— 坏 id 只毒它自己那条。

    新作者不可能同时是这批里任一条的原作者(那条会在校验阶段被 already_author 拦掉),
    两组收件人天然不相交,不会有人为同一批收到两条口径相反的通知。
    """
    if not items:
        return
    if len(items) == 1:
        tmpl, old_author_id = items[0]
        notify_author_transferred(db, tmpl, old_author_id=old_author_id, operator=operator)
        return

    tmpls = [t for t, _ in items]
    # 新作者不另传:调用点在 commit 之后,author_id 就是他(同 notify_author_transferred)
    new_author_id = tmpls[0].author_id
    by = f"(由 {operator.name} 操作)"
    # 汇总没有「那一个任务」可指:深链落到任务列表,template_id 留空(见 _push 的说明)
    link = _tasks_link()

    _push(
        db,
        user_id=new_author_id,
        title=f"你被指定为 {len(tmpls)} 个任务的作者",
        body=_new_author_body(
            f"以下 {len(tmpls)} 个任务({_task_list(tmpls)})", by, plural=True
        ),
        level="info",
        link=link,
        job_id=None,
        template_id=None,
    )

    grouped: dict[int, list[SqlTemplate]] = {}
    for tmpl, old_author_id in items:
        grouped.setdefault(old_author_id, []).append(tmpl)
    for old_author_id, mine in grouped.items():
        if not _is_notifiable(db, old_author_id):
            continue
        # 这条与单任务版共享同一个要害:不只说「你不再能改」,还要说**真要改怎么办**。
        # 少了后半句,人只会在列表里发现编辑按钮没了然后来问人(见 notify_author_transferred)
        _push(
            db,
            user_id=old_author_id,
            title=f"你的 {len(mine)} 个任务已移交他人",
            body=_old_author_body(
                f"以下 {len(mine)} 个任务({_task_list(mine)})", by, plural=True
            ),
            level="info",
            link=link,
            job_id=None,
            template_id=None,
        )


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
    link = _records_link(tmpl.id, job_id)
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


def notify_subscribed_by_operator(
    db: Session,
    tmpl: SqlTemplate,
    user_ids: list[int],
    *,
    operator_name: str,
    schedule_desc: str | None,
    granted_ids: set[int],
) -> None:
    """有编辑权的人代为订阅 → 告知被订上的业务方。

    **这条必须发**:它是全平台唯一一个「用户什么都没做、状态却变了」的入口,而且此后每一期
    都会有推送落到他手上 —— 不告诉他这是怎么回事、怎么退,就是替人做主。
    操作者不另发:他刚点完按钮,界面已经给了回执(同 permissions.grant 不通知授权人)。

    文案只有一处随人而异(顺带补了授权的多一句),故按这一位切成两批发,
    而不是自己写一个 _push 循环 —— 同 notify_subscription_run_failed 的两批写法。
    """
    body = (
        f"{operator_name} 把《{tmpl.name}》的定时数据推送订阅给了你,"
        f"平台会{schedule_desc or '按计划'}自动运行并把结果推给你;"
        "不需要的话,可以在任务卡片的 ⋮ 里随时退订。"
    )
    live = [uid for uid in user_ids if _is_notifiable(db, uid)]
    for ids, extra in (
        ([u for u in live if u in granted_ids], "你现在也能在任务列表里看到这个任务,并自己运行、下载结果了。"),
        ([u for u in live if u not in granted_ids], ""),
    ):
        _push_each(
            db, ids,
            title="已为你订阅数据推送",
            body=body + extra,
            level="info", link=_tasks_link(), job_id=None, template_id=tmpl.id,
        )


def notify_unsubscribed_by_operator(
    db: Session, tmpl: SqlTemplate, user_id: int, *, operator_name: str
) -> None:
    """被移出订阅者名单 → 告知本人。不解释原因(平台不知道),只说清事实与去处。
    单数签名:移除接口按设计就是一次一人(URL 里是单个 user_id)。"""
    if not _is_notifiable(db, user_id):
        return
    _push(
        db, user_id=user_id,
        title="订阅已取消",
        body=(
            f"{operator_name} 把你移出了《{tmpl.name}》的订阅者名单,"
            "你不会再收到该任务的定时数据推送;如仍需要,可以自己重新订阅。"
        ),
        level="info", link=_tasks_link(), job_id=None, template_id=tmpl.id,
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
        level="info", link=_records_link(tmpl.id, job_id),
        job_id=job_id, template_id=tmpl.id,
    )


def notify_subscription_ready(db: Session, tmpl: SqlTemplate, job: QueryJob) -> int:
    """本期订阅数据生成成功 → 通知仍在册且仍 can_view 的订阅者(收件人策略住本模块)。
    返回收件人数(补推接口要回显「推给了几个人」)。

    权限被撤即不再推送,fail-closed;订阅行留给自动清退或本人退订收拾。
    调用方(subscription_service.on_scheduled_run_finished)保证**先结算再调这里**,
    刚被清退的人不会再收到「数据已生成」。

    补推来的一期(job.pushed_by_id 非空)换一句文案:失败后补上的说「已补发」,
    替换本期已推送结果的说「已更新,以此版为准」—— 订阅者手里可能已经有一版,
    不说清楚他分不出哪份是对的。深链一律指向**这条订阅记录**而不是补推的来源记录:
    后者对只有 view 授权的订阅者不可见。
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
    if job.replaces_job_id is not None:
        title, lead = "订阅数据已更新", "本期数据已更新,请以此版为准"
    elif job.pushed_by_id is not None:
        title, lead = "订阅数据已补发", "本期数据已补发"
    else:
        title, lead = "订阅数据已生成", "本期数据已生成"
    _push_each(
        db, ready_ids,
        title=title,
        body=(
            f"你订阅的《{tmpl.name}》{lead},共 {job.row_count} 行,"
            "可到该任务的「运行记录」里预览并下载。"
        ),
        level="success", link=_records_link(tmpl.id, job.id),
        job_id=job.id, template_id=tmpl.id,
    )
    return len(ready_ids)

"""任务订阅:订阅计划(与任务 1:1)+ 订阅关系 + 订阅事件留痕。

「订阅」只对**无参数任务**开放:参数一律必填且无默认值(见 schemas/common.ParamDef),
定时运行没有人在场填参,有参数的任务根本组不出一次合法运行。这条互斥约束的卡点在
template_service.add_version(所有保存入口必经)。
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, tbl
from app.models.mixins import TimestampMixin

# 运行频次
FREQ_DAILY = "daily"
FREQ_WEEKLY = "weekly"    # days = ISO 星期几(1=周一 … 7=周日),可多选
FREQ_MONTHLY = "monthly"  # days = 几号(1-31),可多选;29/30/31 遇小月顺延到月末最后一天
FREQS = (FREQ_DAILY, FREQ_WEEKLY, FREQ_MONTHLY)

# 订阅事件动作(留痕表)。中文标签是**单一事实来源**,前端「订阅记录」直接消费。
SUB_EVENT_SUBSCRIBE = "subscribe"                    # 用户自助订阅
SUB_EVENT_UNSUBSCRIBE = "unsubscribe"                # 用户自助退订
SUB_EVENT_AUTO_UNSUBSCRIBE = "auto_unsubscribe"      # 连续未消费,平台自动清退
SUB_EVENT_CLOSED_UNSUBSCRIBE = "closed_unsubscribe"  # 开发者关闭订阅,批量清退
SUB_EVENT_MEMBER_REMOVED = "member_removed"          # 离队清理,订阅随之取消
SUB_EVENT_ADDED = "added_by_manager"                 # 有编辑权的人代业务方订阅
SUB_EVENT_REMOVED = "removed_by_manager"             # 有编辑权的人把某人移出订阅者名单

# 标签一律从**当事人视角**写(同「移出团队随之退订」);「谁干的」由 operator_id 那一列回答,
# 不写进标签 —— 否则同一个动作要为「作者干的」「团队管理员干的」各造一个码。
SUB_EVENT_META: dict[str, str] = {
    SUB_EVENT_SUBSCRIBE: "订阅",
    SUB_EVENT_UNSUBSCRIBE: "退订",
    SUB_EVENT_AUTO_UNSUBSCRIBE: "连续未消费自动退订",
    SUB_EVENT_CLOSED_UNSUBSCRIBE: "开发者关闭订阅",
    SUB_EVENT_MEMBER_REMOVED: "移出团队随之退订",
    SUB_EVENT_ADDED: "被添加为订阅者",
    SUB_EVENT_REMOVED: "被移出订阅者名单",
}


class TaskSchedule(Base, TimestampMixin):
    """任务的订阅计划,与任务 1:1。

    「暂停」刻意不设列 —— 调度扫描只认任务 status=published(见 subscription_service.tick),
    下线即暂停、重新上线即恢复,fail-closed:状态只有一份,不会出现「任务已下线但计划还在跑」。
    """

    __tablename__ = tbl("task_schedules")
    __table_args__ = (UniqueConstraint("template_id", name="uq_task_schedule_template"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey(tbl("sql_templates.id")), index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    freq: Mapped[str] = mapped_column(String(16), default=FREQ_DAILY, nullable=False)
    # weekly: [1..7](ISO,周一=1);monthly: [1..31];daily 时为空列表
    days: Mapped[list] = mapped_column(JSON, default=list)
    # "HH:MM",服务器本地时间。存字符串而不是 Time 列:它只参与自写的 due 计算,
    # 不参与任何 SQL 端时间运算,字符串在 MySQL/SQLite 间零歧义。
    at_time: Mapped[str] = mapped_column(String(5), default="09:00", nullable=False)
    # 调度水位:已消化到的**计划时刻**(不是实际运行时刻)。「原子推进水位」就是去重锁
    # (UPDATE ... WHERE last_planned_at < planned,rowcount==1 才 fire),
    # 与 worker._claim_next_job_id 同一套路,多实例安全。
    last_planned_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # 最后修改计划的人,展示用。不做 FK:与 TemplateEnumValues.updated_by 同一取舍
    updated_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class TaskSubscription(Base, TimestampMixin):
    """某用户对某任务的订阅(在册关系)。

    退订(自助/自动/开发者关闭订阅/离队清理)= 删行,让「谁在订」恒等于表里现存的行;
    历史由 TaskSubscriptionEvent 追加留痕,两者同一事务写。
    """

    __tablename__ = tbl("task_subscriptions")
    __table_args__ = (
        UniqueConstraint("template_id", "user_id", name="uq_task_subscription"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey(tbl("sql_templates.id")), index=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(tbl("users.id")), index=True
    )
    # 连续未消费的**成功**期数。只在下一期成功结算时推进(见 subscription_service
    # .settle_on_success);失败的期不触发结算,天然不计入。
    miss_streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 消费水位:已消费(下载或预览)的最大订阅 job id,只增不减。订阅 job 按 id 严格线性,
    # 结算只需回答「消费过上一期没有」,一列水位与消费明细表完全等价 —— 少一张表、零清理负担。
    last_consumed_job_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    # 代订阅的操作者;NULL = 本人自助订阅。让「这一行是谁弄进来的」在名单上直接读得到。
    # 不做 FK:同 TaskSchedule.updated_by 的取舍。
    #
    # **不是事件表的冗余副本**:事件是历史(谁在何时订过/退过),本列是**当前这一行**的事实,
    # 而事件行不指向具体订阅行 —— 想从事件推出它,只能猜「最后一条早于本行 created_at 的
    # added 事件」,那是脆弱推断。退订后自助重订会得到一行 added_by=NULL,两者本就不等价。
    # 不变量:订阅行只有 insert/delete、**从不 UPDATE**,所以本列在行的生命周期内不会与
    # 事件流分叉。将来若出现会改已有订阅行的路径(重新指派/用户合并/回填),必须同时维护它。
    added_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    user = relationship("User", lazy="joined")

    @property
    def user_name(self) -> Optional[str]:
        return self.user.name if self.user else None

    @property
    def user_avatar(self) -> Optional[str]:
        return self.user.avatar if self.user else None


class TaskSubscriptionEvent(Base, TimestampMixin):
    """订阅/退订事件留痕(append-only)。

    在册关系表用删行表达退订,但「谁在什么时候订过/被谁退掉」要经得起按任务审查
    (admin-only 的审计日志按任务检索不便,且开发者/团队管理员看不到)。
    只插入、不更新、不删除;与订阅行的增删**同一事务**提交,回滚时不留孤儿事件。
    """

    __tablename__ = tbl("task_subscription_events")

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey(tbl("sql_templates.id")), index=True
    )
    # 订阅关系的当事人。不做 FK:用户封禁/清理后这条留痕仍要读得懂
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    # 触发者:自助操作=本人,自动清退=系统用户,关闭订阅/离队清理=操作的开发者或管理员
    operator_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    # 补充信息,如自动清退时的 miss_streak
    detail: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

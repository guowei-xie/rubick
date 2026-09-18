"""取数任务(一次运行的记录)。默认由独立 DB 轮询 worker 异步执行
(RUN_INLINE=true 时在请求内同步执行);状态全程记录以便前端展示与审计。"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.database import Base, tbl
from app.models.mixins import TimestampMixin

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_SUCCESS = "success"
JOB_FAILED = "failed"

# 运行来源:业务正式取数 / 作者在编辑器里的试跑 / 订阅计划定时自动运行
SOURCE_RUN = "run"
SOURCE_TEST = "test"
SOURCE_SUBSCRIBE = "subscribe"

# executed_sql 落的是 Text 列(MySQL 上限 65535 **字节**),而它的内容直接由用户填的参数决定:
# 业务方用「上传/粘贴列表」粘 5000 个 10 位 ID(前端 LIST_CAP 就是 5000),渲染出来是 70KB。
# 严格模式下 MySQL 抛 1406,而那次 commit 发生在**把 SQL 发给目标库之前** —— 整次取数在还没
# 查数时就失败,报错还与 SQL 本身毫无关系。SQLite 没有列长度限制,所以这个坑只在线上现形。
# 这一列只是给人查阅的存档、不参与执行,故一律先截断,留 5535 字节余量给多字节边界与标记。
EXECUTED_SQL_MAX_BYTES = 60000
_CLIP_NOTE = "\n-- …(已截断,原文 {n} 字符,完整 SQL 见任务的该版本定义)"


def clip_executed_sql(sql: str | None) -> str | None:
    """按**字节**把最终 SQL 截到列装得下的长度;没超限的原样返回。

    按字节而不是字符:候选值可能是中文(UTF-8 下 3 字节/字),按字符数算会低估两倍。
    截断点用 errors="ignore" 落在字符边界上,免得切出半个多字节序列。
    """
    if sql is None:
        return None
    raw = sql.encode("utf-8")
    if len(raw) <= EXECUTED_SQL_MAX_BYTES:
        return sql
    note = _CLIP_NOTE.format(n=len(sql))
    keep = EXECUTED_SQL_MAX_BYTES - len(note.encode("utf-8"))
    return raw[:keep].decode("utf-8", errors="ignore") + note


class QueryJob(Base, TimestampMixin):
    __tablename__ = tbl("query_jobs")

    # TimestampMixin 不给 created_at 建索引,而运营分析的每一句聚合都以「created_at 落在
    # 时间窗内」开头 —— 与 audit_logs 同样处理(那条也是在模型里单独补的)。
    # 成功率 / 失败归因 / 耗时分位数全是「source=? AND status=? AND 时间窗」,故再加一条复合。
    # **声明在模型里,而不是只写在 migrate 里**:测试库由 create_all 建表、不跑 migrate,
    # 只写在迁移里的话,SQL 预算测试量到的执行计划与线上不是同一个。
    __table_args__ = (
        Index(f"ix_{tbl('query_jobs')}_created_at", "created_at"),
        Index(f"ix_{tbl('query_jobs')}_source_status_created",
              "source", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(tbl("users.id")), index=True)
    template_id: Mapped[int] = mapped_column(ForeignKey(tbl("sql_templates.id")), index=True)
    # 试跑可能发生在模板尚无已发布版本时,故可空
    template_version_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(tbl("template_versions.id")), nullable=True
    )
    datasource_id: Mapped[int] = mapped_column(ForeignKey(tbl("data_sources.id")))

    params: Mapped[dict] = mapped_column(JSON, default=dict)  # 用户填入的参数值
    status: Mapped[str] = mapped_column(String(16), default=JOB_QUEUED, index=True)
    # 运行来源:run=业务正式取数,test=作者在编辑器里的试跑(运行记录里据此区分)
    source: Mapped[str] = mapped_column(String(16), default=SOURCE_RUN, nullable=False, index=True)

    row_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 实际发给数据库的最终 SQL(参数已代入,供运行记录查阅)
    executed_sql: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 本次取数实际使用的**库身份** = 任务所属团队的团队账号。固化下来,审计才能回答
    # 「这次数据是用哪个团队的账号取的」。只存库账号名,不存密码。
    # 刻意**不做外键**:团队被删之后这条审计记录仍要读得懂(外键会阻止删团队或把它 SET NULL,
    # 两种都毁掉这条记录)。与 TemplateEnumValues.updated_by 同一取舍。
    # nullable 是必须的:enqueue 建行时还没解析身份(worker 才解析,见 execute_job),
    # 且 _ensure_column 只能加可空列。为空 = 身份解析之前就失败了。
    run_as_team_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    run_as_username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # 结果文件在本地结果目录(DATA_DIR/results)下的相对路径 key;下载时换带签名 token 的 URL
    # —— 存的是**相对** key,所以换存储盘(改 DATA_DIR + 搬文件)不必改库里的任何一行
    result_object_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    result_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # 开始执行的时刻。`duration_ms` 只算**真正执行**的那段(不含排队),所以在它出现之前
    # 「排了多久」是算不出来的:created_at 是入队时刻,而 updated_at 会被后续每一次状态
    # 流转覆盖(成功时再写一次、reclaim_stale_jobs 还会再写一次),反推不出开始时刻。
    # 排队时长 = started_at - created_at。
    #
    # 为空有两种情形,都不该被当成 0:① 这一行早于本列上线;② 它从未离开队列(还在排,
    # 或在解析身份之前就失败了)。试跑同步执行、压根不入队,也留空 —— 排队统计一律
    # 排除 source='test',否则一堆 0 会把分位数拉平。
    #
    # 用 DB 时钟(func.now())而不是 datetime.now():created_at 由 TimestampMixin 的
    # server_default=func.now() 生成,同样是 DB 时钟。混用两个时钟会在 worker 与 DB
    # 不在同一台机器时算出**负数**排队时长 —— 而那只在生产现形。
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # 仅订阅运行(source=subscribe)使用:本期结果被**下一期成功结果**取代的时刻
    # (下一期成功结算时盖章,见 subscription_service.settle_on_success;开发者关闭订阅时
    # 也补盖一次,免得最后一期永不过期)。它驱动订阅结果的保留期:未被取代就不过期,
    # 订阅者在整个周期内(哪怕周期长于 RESULT_RETENTION_DAYS)都能下载到最新一期。
    superseded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # 关联模板/发起人,便于运行记录展示名称
    template = relationship("SqlTemplate", lazy="joined")
    user = relationship("User", lazy="joined")

    @property
    def template_name(self) -> Optional[str]:
        return self.template.name if self.template else None

    @property
    def user_name(self) -> Optional[str]:
        return self.user.name if self.user else None

    @validates("executed_sql")
    def _clip_executed_sql(self, _key: str, value: str | None) -> str | None:
        """赋值即截断。两条写入路径(execute_job / test_run)从前靠调用方自觉先 clip,
        第三条写入路径出现时必然忘 —— 收进模型层,让「装不下的值进不了这一列」成为结构保证。"""
        return clip_executed_sql(value)

    @property
    def queue_ms(self) -> Optional[int]:
        """排队等待时长(毫秒)。未开始 / 早于 started_at 上线 / 试跑 一律为 None。

        **不要在这里把 None 兜底成 0**:空值的意思是「没有排队记录」,当成 0 会让全部
        历史行变成「零排队」,一上线就把这个指标说成假的。
        """
        if self.started_at is None or self.created_at is None:
            return None
        # 时钟回拨等极端情形下夹到 0:负的排队时长没有意义,但也不该抛
        return max(0, int((self.started_at - self.created_at).total_seconds() * 1000))

    @property
    def result_expired(self) -> bool:
        """结果文件是否已过保留期(到期后由 worker 每小时清一次本地文件,见 result_service.cleanup_expired)。

        订阅运行(source=subscribe)的口径不同:保留到**下一期成功结果产生**(superseded_at
        被盖章)为止,且不低于常规保留天数 —— 月频订阅的用户不该在第 8 天就下载不到本期数据。
        文件清理侧与这里同口径(worker._maintenance 把未过期的订阅结果作为受保护键传给
        result_service.cleanup_expired),两边必须一起改。
        """
        from datetime import timedelta

        from app.core.config import settings

        if self.status != JOB_SUCCESS or not self.result_object_key or not self.created_at:
            return False
        past_retention = datetime.now() > self.created_at + timedelta(
            days=settings.RESULT_RETENTION_DAYS
        )
        if self.source == SOURCE_SUBSCRIBE:
            return self.superseded_at is not None and past_retention
        return past_retention

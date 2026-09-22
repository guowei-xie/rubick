import { Col, Row, Table } from "antd";
import { useNavigate } from "react-router-dom";
import { AnalyticsQuery, AssetsData, MetricNote, TopTemplate, analyticsAssets } from "../../api";
import MetricCard, { CARD_COL } from "../../components/analytics/MetricCard";
import RankBarTable, { taskRankColumns } from "../../components/analytics/RankBarTable";
import SectionCard from "../../components/analytics/SectionCard";
import { fmtDayTime, fmtPercent } from "../../format";
import { taskLink } from "../../taskSearch";
import { useSectionData } from "./useAnalyticsQuery";

export default function AssetsSection({
  query,
  scopeLabel,
  notes,
  teamId,
}: {
  query: AnalyticsQuery;
  scopeLabel: string;
  notes: Record<string, MetricNote>;
  teamId: number | null;
}) {
  const nav = useNavigate();
  const { ref, data, loading, error, reload } = useSectionData<AssetsData>(
    (signal) => analyticsAssets(query, signal),
    query
  );

  /** 下钻到任务列表时带上团队,让落地页的范围与这里一致。 */
  const withTeam = (q: string) =>
    teamId ? `/tasks?${q}&team=${teamId}` : `/tasks?${q}`;

  const asOf = data?.as_of;
  // 闲置提示功能关掉时(阈值 0),这张卡与它的下钻一起隐藏 —— 任务列表页的「闲置」筛选片
  // 在同一条件下也不渲染,留着它只会跳到一张空列表
  const idleUsable = !!asOf && asOf.idle_threshold_days > 0;

  return (
    <SectionCard
      id="assets"
      innerRef={ref}
      title="任务资产"
      scopeLabel={scopeLabel}
      loading={loading}
      error={error}
      onRetry={reload}
    >
      {data && asOf && (
        <>
          <Row gutter={[16, 16]}>
            <Col {...CARD_COL}>
              <MetricCard label="任务总数" metric={asOf.total} to={withTeam("status=")} />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard label="已上线" metric={asOf.published}
                          to={withTeam("status=published")} />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard label="草稿" metric={asOf.draft} to={withTeam("status=draft")} />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard label="回收站" metric={asOf.archived} to={withTeam("recycle=1")} />
            </Col>
            {idleUsable && (
              <Col {...CARD_COL}>
                <MetricCard
                  label={`闲置（>${asOf.idle_threshold_days} 天）`}
                  metric={asOf.idle}
                  to={withTeam("idle=1")}
                  note={notes["idle"]?.note}
                />
              </Col>
            )}
            <Col {...CARD_COL}>
              <MetricCard
                label="从未被运行"
                metric={asOf.never_run}
                note={notes["never_run"]?.note}
              />
            </Col>
          </Row>

          <div className="rk-ana-subtitle">订阅与定时</div>
          <Row gutter={[16, 16]}>
            <Col {...CARD_COL}>
              <MetricCard label="定时运行中的任务" metric={asOf.schedules_enabled}
                          note={notes["schedules_enabled"]?.note} />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard label="订阅关系" metric={asOf.subscribers} />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard label="快被自动退订" metric={asOf.at_risk_subscriptions}
                          note={notes["at_risk_subscriptions"]?.note} />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard label="本区间新建任务" metric={data.window_changes.new_templates} />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard label="本区间新版本" metric={data.window_changes.new_versions} />
            </Col>
            {/* 上线次数只能从审计里数，团队管理员不读审计，所以这个键可能不存在 */}
            {data.window_changes.publishes && (
              <Col {...CARD_COL}>
                <MetricCard label="本区间上线" metric={data.window_changes.publishes} />
              </Col>
            )}
          </Row>

          <div className="rk-ana-subtitle">跑得最多的任务</div>
          <RankBarTable
            rows={data.top_templates}
            rowKey={(r) => r.template_id}
            barKey="run_count"
            barLabel="运行次数"
            emptyText="这段时间没有任务被运行"
            onRow={(r) => ({ onClick: () => nav(taskLink(r.template_id)) })}
            columns={[
              ...taskRankColumns<TopTemplate>(),
              {
                title: "使用人数", dataIndex: "distinct_users", width: 96,
                render: (v: number) => (
                  <span title="只有作者一个人在跑，说明它还没真正被业务用起来">{v}</span>
                ),
              },
              { title: "成功率", dataIndex: "success_rate", width: 88,
                render: (v: number | null) => fmtPercent(v) },
            ]}
          />
          <div className="rk-ana-caption">
            头部 10 个任务占了本区间 {fmtPercent(data.top10_share.value)} 的运行量；
            跑过不超过 1 次的已上线任务有 {data.tail_count.value ?? "—"} 个。
          </div>

          {idleUsable && data.idle_list.length > 0 && (
            <>
              <div className="rk-ana-subtitle">闲置最久的任务</div>
              <Table
                size="small"
                pagination={false}
                rowKey="template_id"
                dataSource={data.idle_list}
                scroll={{ x: "max-content" }}
                onRow={(r) => ({ onClick: () => nav(taskLink(r.template_id)) })}
                columns={[
                  { title: "编号", dataIndex: "template_id", width: 72,
                    render: (v: number) => <span className="rk-ana-id">#{v}</span> },
                  { title: "任务", dataIndex: "name", ellipsis: true },
                  { title: "作者", dataIndex: "author_name", width: 110 },
                  { title: "闲置天数", dataIndex: "idle_days", width: 96 },
                  { title: "最后运行", dataIndex: "last_run_at", width: 130,
                    render: (v: string | null) => (v ? fmtDayTime(v) : "从未运行") },
                ]}
              />
            </>
          )}
        </>
      )}
    </SectionCard>
  );
}

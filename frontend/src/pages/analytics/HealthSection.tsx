import { useMemo } from "react";
import { Col, Row, Table, Tag, Tooltip } from "antd";
import { AnalyticsQuery, HealthData, MetricNote, analyticsHealth } from "../../api";
import Chart from "../../components/analytics/Chart";
import MetricCard, { CARD_COL } from "../../components/analytics/MetricCard";
import SectionCard from "../../components/analytics/SectionCard";
import {
  INK_MUTED, SERIES_COLORS, STATUS_COLORS, baseOption, timeAxis, valueAxis,
} from "../../components/analytics/chartTheme";
import { JOB_SOURCE_LONG } from "../../components/StatusTag";
import { fmtDayTime, fmtDuration, fmtPercent } from "../../format";
import { useSectionData } from "./useAnalyticsQuery";

export default function HealthSection({
  query,
  scopeLabel,
  notes,
}: {
  query: AnalyticsQuery;
  scopeLabel: string;
  notes: Record<string, MetricNote>;
}) {
  const { ref, data, loading, error, reload } = useSectionData<HealthData>(
    (signal) => analyticsHealth(query, signal),
    query
  );

  const option = useMemo(() => {
    const pts = data?.daily_series ?? [];
    return {
      ...baseOption,
      legend: { ...baseOption.legend, data: ["成功", "失败"] },
      xAxis: { ...timeAxis, boundaryGap: true, data: pts.map((p) => p.date.slice(5)) },
      yAxis: valueAxis,
      series: [
        { name: "成功", type: "bar", stack: "t", data: pts.map((p) => p.success),
          itemStyle: { color: STATUS_COLORS.success, borderRadius: [0, 0, 0, 0] } },
        { name: "失败", type: "bar", stack: "t", data: pts.map((p) => p.failed),
          itemStyle: { color: STATUS_COLORS.failed, borderRadius: [3, 3, 0, 0] } },
      ],
    } as any;
  }, [data]);

  const failOption = useMemo(() => {
    const rows = (data?.failure_buckets ?? []).slice().reverse();
    return {
      ...baseOption,
      tooltip: { ...baseOption.tooltip, trigger: "item" },
      legend: { show: false },
      grid: { ...baseOption.grid, left: 8, right: 24 },
      xAxis: { ...valueAxis, splitLine: { show: false } },
      yAxis: {
        type: "category",
        data: rows.map((b) => b.label),
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: INK_MUTED, fontSize: 11 },
      },
      series: [{
        type: "bar",
        data: rows.map((b, i) => ({
          value: b.count,
          itemStyle: { color: SERIES_COLORS[(rows.length - 1 - i) % SERIES_COLORS.length] },
        })),
        barMaxWidth: 18,
        itemStyle: { borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: "right", fontSize: 11, color: INK_MUTED },
      }],
    } as any;
  }, [data]);

  const pts = data?.daily_series ?? [];
  const queue = data?.queue;
  // 排队记录覆盖率偏低时要说清楚从什么时候起可用 —— 否则那个 p50 会被当成全量口径
  const queueThin = !!queue && queue.coverage !== null && queue.coverage < 0.9;

  return (
    <SectionCard
      id="health"
      innerRef={ref}
      title="运行健康"
      scopeLabel={scopeLabel}
      loading={loading}
      error={error}
      onRetry={reload}
    >
      {data && (
        <>
          <Row gutter={[16, 16]}>
            <Col {...CARD_COL}>
              <MetricCard
                label="正式取数成功率"
                metric={data.run_success_rate}
                format="percent"
                note={notes["success_rate"]?.note}
              />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard label="失败次数" metric={data.run_failed} />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard label="成功但 0 行" metric={data.zero_row_jobs}
                          note={notes["zero_row_jobs"]?.note} />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard
                label="排队中位数"
                metric={data.queue_p50_ms}
                format="duration"
                note={notes["queue"]?.note}
              />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard label="等待超过 1 分钟" metric={data.queue_over_60s} />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard
                label="此刻排队"
                metric={data.queued_now}
                note={notes["in_flight"]?.note}
                suffix={
                  data.in_flight.running ? (
                    <span className="rk-metric-sub"> / {data.in_flight.running} 在跑</span>
                  ) : null
                }
              />
            </Col>
          </Row>

          {queueThin && (
            <div className="rk-ana-caption">
              排队数据自 {fmtDayTime(queue!.stats_since)} 起可用；
              更早的运行没有开始时刻记录，未计入（不会当成零排队）。
            </div>
          )}

          <div className="rk-ana-subtitle">按来源分开看</div>
          <Row gutter={[16, 16]}>
            {(["run", "test", "subscribe"] as const).map((src) => {
              const r = data.by_source[src];
              return (
                <Col key={src} xs={24} md={8}>
                  <div className="rk-ana-rate">
                    <div className="rk-ana-rate-head">
                      <b>{JOB_SOURCE_LONG[src]}</b>
                      <span>{fmtPercent(r?.success_rate)}</span>
                    </div>
                    <div className="rk-ana-rate-bar">
                      <span
                        style={{
                          width: `${(r?.success_rate ?? 0) * 100}%`,
                          background: STATUS_COLORS.success,
                        }}
                      />
                    </div>
                    <div className="rk-ana-rate-sub">
                      {r?.total ? `${r.total} 次终态 · 失败 ${r.failed}` : "本区间没有运行"}
                    </div>
                  </div>
                </Col>
              );
            })}
          </Row>
          <div className="rk-ana-caption">
            试跑失败率天然偏高——写 SQL 本就是试错；定时运行失败才是需要处理的事故。
            这也是三者不合并成一个数字的原因。
          </div>

          <div className="rk-ana-subtitle">每日成败</div>
          <Chart option={option} empty={pts.length === 0} emptyText="这段时间没有运行记录" />

          <div className="rk-ana-subtitle">失败归因</div>
          <Chart
            option={failOption}
            height={Math.max(140, (data.failure_buckets.length || 1) * 34 + 40)}
            empty={data.failure_buckets.length === 0}
            emptyText="这段时间没有失败的取数"
          />
          {data.unbucketed_samples.length > 0 && (
            <div className="rk-ana-caption">
              未归类样例（用于完善归因规则）：
              {data.unbucketed_samples.slice(0, 3).map((s, i) => (
                <Tag key={i} style={{ marginInlineStart: 6 }}>{s.slice(0, 40)}</Tag>
              ))}
            </div>
          )}

          <div className="rk-ana-subtitle">耗时与数据源</div>
          <Row gutter={[16, 16]}>
            {Object.entries(data.duration_by_engine).map(([engine, d]) => (
              <Col key={engine} xs={24} md={12}>
                <div className="rk-ana-rate">
                  <div className="rk-ana-rate-head">
                    <b>{engine.toUpperCase()} 执行耗时</b>
                    <span>{d.samples} 次成功</span>
                  </div>
                  <div className="rk-ana-rate-sub">
                    P50 {fmtDuration(d.p50_ms)} · P90 {fmtDuration(d.p90_ms)} ·
                    P95 {fmtDuration(d.p95_ms)}
                  </div>
                </div>
              </Col>
            ))}
          </Row>
          <Table
            size="small"
            style={{ marginTop: 12 }}
            pagination={false}
            rowKey="datasource_id"
            dataSource={data.by_datasource}
            scroll={{ x: "max-content" }}
            locale={{ emptyText: "这段时间没有运行记录" }}
            columns={[
              { title: "数据源", dataIndex: "name" },
              { title: "引擎", dataIndex: "engine",
                render: (v: string) => <Tag>{v.toUpperCase()}</Tag> },
              { title: "运行", dataIndex: "total" },
              { title: "失败", dataIndex: "failed" },
              {
                title: "失败率", dataIndex: "fail_rate",
                render: (v: number | null) => (
                  <Tooltip title="一个数据源上多个任务都在失败，多半是库侧的问题，而不是 SQL 写得不好">
                    <span style={{ color: (v ?? 0) > 0.2 ? STATUS_COLORS.failed : undefined }}>
                      {fmtPercent(v)}
                    </span>
                  </Tooltip>
                ),
              },
            ]}
          />
        </>
      )}
    </SectionCard>
  );
}

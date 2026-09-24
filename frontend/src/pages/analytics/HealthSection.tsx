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
import { fmtDuration, fmtPercent } from "../../format";
import { useSectionData } from "./useAnalyticsQuery";

export default function HealthSection({
  query,
  scopeLabel,
  notes,
  showInFlight = true,
}: {
  query: AnalyticsQuery;
  scopeLabel: string;
  notes: Record<string, MetricNote>;
  /** 全平台视角下顶部有实时负载板,「此刻排队」在那边有明细且会自动刷新 —— 这里再摆一张
   *  只取一次的同名卡,两个数一刷新就对不上。团队视角没有实时板,仍由这张卡兜底 */
  showInFlight?: boolean;
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
              <MetricCard label="等待超过 1 分钟" metric={data.queue_over_60s}
                          note={notes["queue"]?.note} />
            </Col>
            {showInFlight && <Col {...CARD_COL}>
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
            </Col>}
            <Col {...CARD_COL}>
              <MetricCard label="复用命中" metric={data.reuse_hits}
                          note={notes["reuse_hits"]?.note} />
            </Col>
          </Row>

          {/* 正式取数的成功率已在顶部那张卡上,这里只摆另外两路 */}
          <div className="rk-ana-subtitle">试跑与定时分开看</div>
          <Row gutter={[16, 16]}>
            {(["test", "subscribe"] as const).map((src) => {
              const r = data.by_source[src];
              return (
                <Col key={src} xs={24} md={12}>
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
                    <b>{engine.toUpperCase()} 执行耗时 P90</b>
                    <span>{fmtDuration(d.p90_ms)}</span>
                  </div>
                  <div className="rk-ana-rate-sub">{d.samples} 次成功运行，不含排队</div>
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

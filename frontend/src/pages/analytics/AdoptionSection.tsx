import { useMemo } from "react";
import { Col, Row } from "antd";
import { AdoptionData, AnalyticsQuery, MetricNote, analyticsAdoption } from "../../api";
import Chart from "../../components/analytics/Chart";
import MetricCard, { CARD_COL } from "../../components/analytics/MetricCard";
import SectionCard from "../../components/analytics/SectionCard";
import { SOURCE_COLORS, baseOption, timeAxis, valueAxis } from "../../components/analytics/chartTheme";
import { useSectionData } from "./useAnalyticsQuery";

export default function AdoptionSection({
  query,
  scopeLabel,
  isTeamView,
  notes,
}: {
  query: AnalyticsQuery;
  scopeLabel: string;
  isTeamView: boolean;
  notes: Record<string, MetricNote>;
}) {
  const { data, loading, error, reload } = useSectionData<AdoptionData>(
    (signal) => analyticsAdoption(query, signal),
    query,
    { eager: true } // 首屏这一块不等滚动,直接取
  );

  const option = useMemo(() => {
    const pts = data?.daily_series ?? [];
    return {
      ...baseOption,
      legend: { ...baseOption.legend, data: ["正式取数", "作者试跑", "定时运行"] },
      xAxis: { ...timeAxis, data: pts.map((p) => p.date.slice(5)) },
      yAxis: valueAxis,
      series: [
        // 三条线分开画,**永不合并成一条「总运行次数」** —— 它们的受众、成本、
        // 健康含义完全不同,合起来那条线涨了也说明不了任何事
        { name: "正式取数", type: "line", smooth: true, symbol: pts.length < 4 ? "circle" : "none",
          symbolSize: 6, data: pts.map((p) => p.run), itemStyle: { color: SOURCE_COLORS.run },
          areaStyle: { opacity: 0.08 } },
        { name: "作者试跑", type: "line", smooth: true, symbol: pts.length < 4 ? "circle" : "none",
          symbolSize: 6, data: pts.map((p) => p.test), itemStyle: { color: SOURCE_COLORS.test } },
        { name: "定时运行", type: "line", smooth: true, symbol: pts.length < 4 ? "circle" : "none",
          symbolSize: 6, data: pts.map((p) => p.subscribe),
          itemStyle: { color: SOURCE_COLORS.subscribe } },
      ],
    } as any;
  }, [data]);

  const pts = data?.daily_series ?? [];
  const note = (k: string) => notes[k]?.note;
  // 一两天数据也照画,但要显式说明 —— 两个点连成的直线看着像一条有意义的趋势
  const thin = pts.length > 0 && pts.length <= 2;

  return (
    <SectionCard
      id="adoption"
      title="采纳与活跃"
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
                label={isTeamView ? "跑过本团队任务的人" : "活跃取数人"}
                metric={data.active_users}
                note={note("active_users")}
              />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard label="正式取数" metric={data.run_jobs} note={note("run_jobs")} />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard label="定时运行" metric={data.scheduled_jobs}
                          note={note("scheduled_jobs")} />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard label="开发侧活跃" metric={data.active_authors}
                          note={note("active_authors")} />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard label="结果下载" metric={data.download_events} />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard label="下载转化" metric={data.download_per_success}
                          format="percent" note={note("download_per_success")} />
            </Col>
          </Row>

          <div className="rk-ana-subtitle">复用与自动化</div>
          <Row gutter={[16, 16]}>
            <Col {...CARD_COL}>
              <MetricCard label="业务自助率" metric={data.self_service_ratio}
                          format="percent" note={note("self_service_ratio")} />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard label="复用倍数" metric={data.reuse_multiple}
                          format="multiple" note={note("reuse_multiple")} />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard label="免人工率" metric={data.automation_ratio}
                          format="percent" note={note("automation_ratio")} />
            </Col>
            {/* 平台专属:团队视角下这两个键**不存在**,整块不渲染 */}
            {data.new_users && (
              <Col {...CARD_COL}>
                <MetricCard label="新增用户" metric={data.new_users} />
              </Col>
            )}
            {data.retention_rate && (
              <Col {...CARD_COL}>
                <MetricCard label="回访率" metric={data.retention_rate} format="percent" />
              </Col>
            )}
            {data.new_task_users && (
              <Col {...CARD_COL}>
                <MetricCard label="首次使用本团队任务" metric={data.new_task_users} />
              </Col>
            )}
          </Row>

          <div className="rk-ana-subtitle">每日取数趋势</div>
          <Chart
            option={option}
            empty={pts.length === 0}
            emptyText="这段时间没有取数记录"
          />
          {thin && (
            <div className="rk-ana-caption">
              仅 {pts.length} 天有数据，趋势参考意义有限
            </div>
          )}
        </>
      )}
    </SectionCard>
  );
}

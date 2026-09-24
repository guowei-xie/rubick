import { useMemo } from "react";
import { Col, Row } from "antd";
import { AdoptionData, AnalyticsQuery, MetricNote, analyticsAdoption } from "../../api";
import Chart from "../../components/analytics/Chart";
import MetricCard, { CARD_COL } from "../../components/analytics/MetricCard";
import SectionCard from "../../components/analytics/SectionCard";
import { SOURCE_COLORS, baseOption, timeAxis, valueAxis } from "../../components/analytics/chartTheme";
import { JOB_SOURCE_LONG } from "../../components/StatusTag";
import { useSectionData } from "./useAnalyticsQuery";

/** 趋势图的四条线。**永不合并成一条「总运行次数」** —— 它们的受众、成本、健康含义完全不同,
 *  合起来那条线涨了也说明不了任何事。试跑没有自己的卡,只在这里露面。 */
const SERIES = ["run", "test", "subscribe", "api"] as const;

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
      legend: { ...baseOption.legend, data: SERIES.map((k) => JOB_SOURCE_LONG[k]) },
      xAxis: { ...timeAxis, data: pts.map((p) => p.date.slice(5)) },
      yAxis: valueAxis,
      series: SERIES.map((k) => ({
        name: JOB_SOURCE_LONG[k], type: "line", smooth: true,
        symbol: pts.length < 4 ? "circle" : "none", symbolSize: 6,
        data: pts.map((p) => p[k]), itemStyle: { color: SOURCE_COLORS[k] },
        ...(k === "run" ? { areaStyle: { opacity: 0.08 } } : {}),
      })),
    } as any;
  }, [data]);

  const pts = data?.daily_series ?? [];
  const note = (k: string) => notes[k]?.note;
  // 一两天数据也照画,但要显式说明 —— 两个点连成的直线看着像一条有意义的趋势
  const thin = pts.length > 0 && pts.length <= 2;
  // 开放 API 是可选能力:一枚 token 都没发过、且**全期**一次 API 运行都没有时,
  // 两张卡整行不摆,免得没接过 API 的平台多出两张「—」。
  // 判据用 has_data 不用 value —— 后者会把「本区间没人调、但上个月天天调」误判成没开张
  const apiInUse = !!data && (data.api_runs.has_data || data.tokens_issued.has_data);
  // 「1」单独摆着看不出是好是坏,「1 / 8」才读得出「发了一堆没人用」
  const issued = data?.tokens_issued.value ?? 0;

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
              <MetricCard label="业务自助率" metric={data.self_service_ratio}
                          format="percent" note={note("self_service_ratio")} />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard label="下载转化" metric={data.download_per_success}
                          format="percent" note={note("download_per_success")} />
            </Col>
          </Row>

          {apiInUse && (
            <>
              <div className="rk-ana-subtitle">
                开放 API
                <span className="rk-ana-subtitle-note">
                  Token 是此刻口径、「用过」固定看近 7 天，不随上方的时间范围变化
                </span>
              </div>
              <Row gutter={[16, 16]}>
                <Col {...CARD_COL}>
                  <MetricCard label="API 调用" metric={data.api_runs} note={note("api_runs")} />
                </Col>
                <Col {...CARD_COL}>
                  <MetricCard
                    label="近 7 天用过的 Token"
                    metric={data.tokens_active_7d}
                    note={note("tokens_active_7d")}
                    // 分母跟在数字后面。issued 为 0 时不挂 ——「/ 0」不是信息,只是噪声
                    suffix={issued ? <span className="rk-metric-sub"> / {issued}</span> : null}
                  />
                </Col>
              </Row>
            </>
          )}

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

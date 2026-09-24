import { useEffect, useMemo, useState } from "react";
import { Button, Col, Progress, Row, Table, Tooltip } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import { LiveData, MetricNote, WorkerState, analyticsLive } from "../../api";
import Chart from "../../components/analytics/Chart";
import { CARD_COL } from "../../components/analytics/MetricCard";
import SectionCard from "../../components/analytics/SectionCard";
import {
  BRAND, INK_MUTED, SOURCE_COLORS, STATUS_COLORS, baseOption, timeAxis, valueAxis,
} from "../../components/analytics/chartTheme";
import StatusTag, { JOB_SOURCE } from "../../components/StatusTag";
import { fmtDuration } from "../../format";
import type { RunsFilterState } from "./RunsSection";
import { useAnalyticsQuery } from "./useAnalyticsQuery";

/** 轮询间隔。worker 每 2 秒认领一次、定时每 30 秒扫一次,15 秒足够看出「在不在动」,
 *  又不至于让一个开着没人看的标签页每分钟打几十个聚合查询。 */
const POLL_MS = 15_000;

const ORANGE = "#fa8c16";

const WORKER_TEXT: Record<WorkerState, { label: string; color?: string; hint: string }> = {
  idle: { label: "空闲", color: STATUS_COLORS.success, hint: "没有排队" },
  ok: { label: "正常", color: STATUS_COLORS.success, hint: "排队的马上会被领走" },
  busy: { label: "满载", color: ORANGE, hint: "槽位全满，新提交在排队" },
  stalled: { label: "疑似停摆", color: STATUS_COLORS.failed, hint: "槽位空着却没人领，去看 worker 服务" },
  inline: { label: "内联执行", hint: "RUN_INLINE 开着，取数在 API 进程里直接跑" },
};

const secs = (s: number | null | undefined) => (s == null ? "—" : fmtDuration(s * 1000));

/**
 * 实时负载。**只在平台视角渲染**(AnalyticsPage 决定),后端也只对平台视角放行 ——
 * 槽位与 worker 是全平台共用的,团队视角下「满载」却只列出本队一条,是个没法行动的画面。
 *
 * 与其余四块不同:不吃时间范围、进页就取(它就是首屏最该看的东西)、页面可见时每 15 秒刷新。
 * 切到别的标签页就停,切回来立刻补一次 —— 看板开一整天没人看,不该一直在打库。
 */
export default function LiveSection({ notes, onDrill }: {
  notes: Record<string, MetricNote>;
  /** 跳到运行明细并带上筛选:这里只看得到此刻,要翻历史去那边 */
  onDrill?: (f: RunsFilterState) => void;
}) {
  const { data, loading, error, reload } = useAnalyticsQuery<LiveData>(
    (signal) => analyticsLive(signal),
    []
  );
  const [fetchedAt, setFetchedAt] = useState<dayjs.Dayjs | null>(null);

  useEffect(() => {
    if (data) setFetchedAt(dayjs());
  }, [data]);

  useEffect(() => {
    const visible = () => document.visibilityState === "visible";
    const timer = window.setInterval(() => visible() && reload(), POLL_MS);
    const onVis = () => visible() && reload();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [reload]);

  const todayOption = useMemo(() => {
    const pts = data?.today ?? [];
    const cap = data?.capacity ?? 0;
    return {
      ...baseOption,
      legend: { ...baseOption.legend, right: 12, data: ["提交", "峰值并发", "槽位上限"] },
      tooltip: {
        ...baseOption.tooltip,
        formatter: (ps: any[]) => {
          const p = pts[ps[0]?.dataIndex];
          if (!p) return "";
          return [
            `<b>${p.hour}:00 – ${p.hour}:59</b>`,
            `提交 ${p.submitted} 次`,
            `峰值并发 ${p.peak_concurrency} / 槽位 ${cap}`,
            `排队等待 P90 ${secs(p.wait_p90_s)}`,
          ].join("<br/>");
        },
      },
      xAxis: { ...timeAxis, boundaryGap: true, data: pts.map((p) => `${p.hour}时`) },
      yAxis: { ...valueAxis, minInterval: 1 },
      series: [
        { name: "提交", type: "bar", data: pts.map((p) => p.submitted),
          itemStyle: { color: BRAND, borderRadius: [3, 3, 0, 0] }, barMaxWidth: 18 },
        { name: "峰值并发", type: "line", data: pts.map((p) => p.peak_concurrency),
          itemStyle: { color: ORANGE }, symbolSize: 5 },
        // 不引 markLine 组件:一条水平的虚线序列就够表达「顶到线了没有」
        { name: "槽位上限", type: "line", data: pts.map(() => cap), symbol: "none",
          lineStyle: { color: INK_MUTED, type: "dashed", width: 1 }, itemStyle: { color: INK_MUTED } },
      ],
    } as any;
  }, [data]);

  const scheduleOption = useMemo(() => {
    const buckets = data?.schedule_24h ?? [];
    return {
      ...baseOption,
      legend: { show: false },
      tooltip: {
        ...baseOption.tooltip,
        formatter: (ps: any[]) => {
          const b = buckets[ps[0]?.dataIndex];
          if (!b) return "";
          const head = `<b>${dayjs(b.hour).format("MM-DD HH:00")}</b> · ${b.count} 个定时运行`;
          const more = b.count > b.tasks.length ? `<br/>…另有 ${b.count - b.tasks.length} 个` : "";
          return [head, ...b.tasks.map((t) => `${t.at} ${t.name}`)].join("<br/>") + more;
        },
      },
      xAxis: { ...timeAxis, boundaryGap: true,
               data: buckets.map((b) => dayjs(b.hour).format("HH时")) },
      yAxis: { ...valueAxis, minInterval: 1 },
      series: [{
        type: "bar",
        barMaxWidth: 16,
        data: buckets.map((b) => ({
          value: b.count,
          itemStyle: {
            color: b.crowded ? STATUS_COLORS.failed : SOURCE_COLORS.subscribe,
            borderRadius: [3, 3, 0, 0],
          },
        })),
      }],
    } as any;
  }, [data]);

  const worker = data ? WORKER_TEXT[data.worker_state] : null;
  const crowded = (data?.schedule_24h ?? []).filter((b) => b.crowded);
  const hasSchedule = (data?.schedule_24h ?? []).some((b) => b.count > 0);

  return (
    <SectionCard
      id="live"
      title="实时负载"
      scopeLabel="全平台"
      loading={loading && !data}
      // 轮询中某一次失败不把整块换成红字:保留上一次的数,在右上角提示
      error={data ? null : error}
      onRetry={reload}
      extra={
        <span className="rk-ana-subtitle-note">
          {error && data ? <span style={{ color: STATUS_COLORS.failed }}>刷新失败 · </span> : null}
          {fetchedAt ? `更新于 ${fetchedAt.format("HH:mm:ss")} · 每 15 秒自动刷新` : ""}
          <Button type="text" size="small" icon={<ReloadOutlined />} onClick={reload}
                  loading={loading && !!data} />
        </span>
      }
    >
      {data && worker && (
        <>
          <Row gutter={[16, 16]}>
            <Col {...CARD_COL}>
              <Tile label="槽位占用" note={notes["live_slots"]?.note}
                    value={<>{data.running - data.overdue}<span className="rk-metric-sub"> / {data.capacity}</span></>}
                    hint={data.overdue ? `另有 ${data.overdue} 条超时仍挂着` : undefined}
                    color={data.running - data.overdue >= data.capacity ? ORANGE : undefined} />
            </Col>
            <Col {...CARD_COL}>
              <Tile label="排队" note={notes["live_queue"]?.note} value={data.queued}
                    hint={data.queued ? `最久已等 ${secs(data.oldest_wait_s)}` : "没有排队"} />
            </Col>
            <Col {...CARD_COL}>
              <Tile label="Worker" note={notes["live_worker"]?.note}
                    value={worker.label} color={worker.color}
                    // 出问题时给该做什么;平稳时给「最近一次开跑在多久前」,一眼看出它还在动
                    hint={data.worker_state === "stalled" || data.worker_state === "busy"
                      || data.last_started_ago_s == null
                      ? worker.hint : `最近开跑在 ${secs(data.last_started_ago_s)}前`} />
            </Col>
            <Col {...CARD_COL}>
              <Tile label="编辑器试跑" note={notes["live_test"]?.note} value={data.test_running}
                    hint="不占 worker 槽位" />
            </Col>
          </Row>

          {data.running_list.length > 0 && (
            <>
              <div className="rk-ana-subtitle">
                在跑
                {onDrill && <a className="rk-ana-subtitle-note" onClick={() => onDrill({})}>翻看历史运行 →</a>}
              </div>
              <Table
                size="small" pagination={false} rowKey="job_id" scroll={{ x: "max-content" }}
                dataSource={data.running_list}
                columns={[
                  { title: "任务", dataIndex: "template_name" },
                  { title: "发起人", dataIndex: "user_name", render: (v) => v || "—" },
                  { title: "来源", dataIndex: "source",
                    render: (v: string) => <StatusTag map={JOB_SOURCE} value={v} /> },
                  { title: "数据源", dataIndex: "datasource", render: (v) => v || "—" },
                  {
                    title: "已跑 / 超时上限", key: "elapsed", width: 240,
                    render: (_: unknown, r) => {
                      const pct = r.timeout_s ? Math.min(100, ((r.elapsed_s ?? 0) / r.timeout_s) * 100) : 0;
                      return (
                        <Tooltip title={r.overdue ? "已超过超时上限仍显示运行中，多半已中断，等待孤儿回收" : undefined}>
                          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                            <Progress percent={pct} showInfo={false} size="small" style={{ width: 90, margin: 0 }}
                                      strokeColor={r.overdue ? STATUS_COLORS.failed : pct >= 80 ? ORANGE : BRAND} />
                            <span>{secs(r.elapsed_s)} / {secs(r.timeout_s)}</span>
                          </div>
                        </Tooltip>
                      );
                    },
                  },
                ]}
              />
            </>
          )}

          {data.queued_list.length > 0 && (
            <>
              <div className="rk-ana-subtitle">
                排队
                <span className="rk-ana-subtitle-note">
                  按 worker 的认领顺序{data.queued > data.queued_list.length
                    ? `，只列前 ${data.queued_list.length} 条（共 ${data.queued} 条）` : ""}
                </span>
                {onDrill && data.queued > data.queued_list.length && (
                  <a className="rk-ana-subtitle-note" onClick={() => onDrill({ status: ["queued"] })}>看全部排队 →</a>
                )}
              </div>
              <Table
                size="small" pagination={false} rowKey="job_id" scroll={{ x: "max-content" }}
                dataSource={data.queued_list}
                columns={[
                  { title: "#", dataIndex: "position", width: 48 },
                  { title: "任务", dataIndex: "template_name" },
                  { title: "发起人", dataIndex: "user_name", render: (v) => v || "—" },
                  { title: "来源", dataIndex: "source",
                    render: (v: string) => <StatusTag map={JOB_SOURCE} value={v} /> },
                  { title: "数据源", dataIndex: "datasource", render: (v) => v || "—" },
                  { title: "已等待", dataIndex: "waited_s", render: (v: number | null) => secs(v) },
                ]}
              />
            </>
          )}

          <div className="rk-ana-subtitle">
            今日分时
            <Tooltip title={notes["live_today"]?.note}>
              <span className="rk-ana-subtitle-note">峰值并发常年顶着槽位线，就该加并发或错峰</span>
            </Tooltip>
          </div>
          <Chart option={todayOption} height={220}
                 empty={data.today.every((p) => !p.submitted && !p.peak_concurrency)}
                 emptyText="今天还没有取数" />

          <div className="rk-ana-subtitle">
            未来 24 小时定时
            <Tooltip title={notes["live_schedule"]?.note}>
              <span className="rk-ana-subtitle-note">
                {crowded.length
                  ? `${crowded.map((b) => dayjs(b.hour).format("HH时")).join("、")} 扎堆（超过 ${data.crowded_threshold} 个），建议错开几分钟`
                  : "没有扎堆的时段"}
              </span>
            </Tooltip>
          </div>
          <Chart option={scheduleOption} height={180} empty={!hasSchedule}
                 emptyText="未来 24 小时没有会执行的定时运行" />
        </>
      )}
    </SectionCard>
  );
}

/** 实时板的数字卡。不复用 MetricCard:那张卡的三态(有值 / 本区间为 0 / 从来没有)与环比
 *  都是为「时间窗」设计的,而这里每个数都是此刻的读数,还要能显示「满载」这样的文字。 */
function Tile({ label, value, hint, note, color }: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  note?: string;
  color?: string;
}) {
  const body = (
    <div className="rk-metric">
      <div className="rk-metric-label"><span>{label}</span></div>
      <div className="rk-metric-value" style={color ? { color } : undefined}>{value}</div>
      <div className="rk-metric-hint">{hint}</div>
    </div>
  );
  return note ? <Tooltip title={note}>{body}</Tooltip> : body;
}

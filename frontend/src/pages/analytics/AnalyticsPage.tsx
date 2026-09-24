import { useCallback, useMemo, useState } from "react";
import { Button, Result, Spin } from "antd";
import { useNavigate } from "react-router-dom";
import dayjs from "dayjs";
import { AnalyticsMeta, MetricNote, analyticsMeta } from "../../api";
import { isPlatformAdmin, useAuth } from "../../auth";
import AdoptionSection from "./AdoptionSection";
import AnalyticsHeader from "./AnalyticsHeader";
import AssetsSection from "./AssetsSection";
import GovernanceSection from "./GovernanceSection";
import HealthSection from "./HealthSection";
import LiveSection from "./LiveSection";
import RunsSection, { RunsFilterState } from "./RunsSection";
import { useAnalyticsQuery } from "./useAnalyticsQuery";
import { useAnalyticsScope } from "./useAnalyticsScope";
import "./analytics.css";

/**
 * 运营分析页。
 *
 * 结构上是**一屏到底的板块列表 + 锚点**,不用 Tab:这页的核心动作是「扫一眼有没有异常」,
 * 而异常可能出现在任意一个视角里 —— Tab 会把大部分答案藏在点击后面,
 * 于是「运行健康正常、但三个团队缺账号」这种组合永远要点好几次才发现。
 * 这些视角之间本来也有因果链(缺账号 → 运行失败 → 任务闲置 → 采纳下降),顺着读更自然。
 *
 * 取数上**首屏只打两个请求**(meta + 板块①;全平台视角再加实时负载),其余滚到跟前再取(useSectionData)。
 * 每块各发一个请求,一起并发在本地 SQLite 上会串行排队,而用户实际只先看第一块。
 */
export default function AnalyticsPage() {
  const { user } = useAuth();
  const nav = useNavigate();

  const { data: meta, loading: metaLoading, error: metaError, reload } =
    useAnalyticsQuery<AnalyticsMeta>((signal) => analyticsMeta(signal), []);

  const scope = useAnalyticsScope(user, meta);

  // 运行明细的筛选提到页面这一层:运行健康、实时负载可以带着条件跳过去(下钻)
  const [runsFilter, setRunsFilter] = useState<RunsFilterState>({});
  const drillRuns = useCallback((f: RunsFilterState) => {
    setRunsFilter(f);
    document.getElementById("runs")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  /** 口径说明按 key 索引,给各板块的 tooltip 用。服务端下发,前端不自己写一份。 */
  const notes = useMemo<Record<string, MetricNote>>(
    () => Object.fromEntries((meta?.metric_notes ?? []).map((n) => [n.key, n])),
    [meta]
  );

  const platform = isPlatformAdmin(user);
  // 「团队视角」= 这一屏的数字只涵盖一个团队。平台管理员下钻到某个队时同样成立 ——
  // 两种身份共用一套渲染,团队态的文案因此会被平台管理员天天用到,写错了马上有人发现。
  const isTeamView = scope.teamId != null;
  const scopeLabel = scope.currentOption?.name ?? (isTeamView ? "本团队" : "全平台");

  const windowText = useMemo(() => {
    const q = scope.query;
    const end = q.end ? dayjs(q.end) : dayjs();
    const start = q.start ? dayjs(q.start) : end.subtract(q.days ?? 30, "day");
    const today = end.isSame(dayjs(), "day");
    return (
      `${start.format("YYYY-MM-DD")} ~ ${end.format("YYYY-MM-DD")}` +
      // 「今天不完整」这句很重要:不说的话,每天早上看都会觉得「怎么掉这么多」
      (today ? "（含今天，今天的数据还不完整）" : "")
    );
  }, [scope.query]);

  if (metaLoading && !meta) return <Spin style={{ margin: 80 }} />;
  if (metaError)
    return (
      <Result
        status="warning"
        title="运营分析没能加载出来"
        subTitle={metaError}
        extra={<Button onClick={reload}>重试</Button>}
      />
    );
  if (!meta) return null;

  // 平台还没被用起来:一屏全是「—」的骨架卡比一句「还没开张」难看得多,也更难读懂。
  // 如实说清进展到哪一步,并给出下一步的入口。
  if (!meta.bootstrapped) {
    return (
      <Result
        icon={<span style={{ fontSize: 40 }}>📊</span>}
        title="还没有运营数据"
        subTitle={
          <>
            运营分析要等平台开始被使用之后才有意义。
            <div style={{ marginTop: 8, color: "var(--ink-secondary)" }}>
              当前：{meta.counts.teams} 个团队 · {meta.counts.templates} 个任务 ·
              {" "}
              {meta.counts.runs} 次取数
            </div>
          </>
        }
        extra={
          <>
            <Button type="primary" onClick={() => nav("/tasks")}>
              去任务列表
            </Button>
            {platform && <Button onClick={() => nav("/admin/teams")}>管理团队</Button>}
          </>
        }
      />
    );
  }

  const shared = { query: scope.query, scopeLabel, notes };
  const showLive = platform && !isTeamView;

  return (
    <div className="rk-ana">
      <AnalyticsHeader
        scopeLabel={scopeLabel}
        windowText={windowText}
        options={meta.scope_options}
        teamId={scope.teamId}
        onTeam={scope.setTeam}
        presets={scope.presets}
        preset={scope.preset}
        onPreset={scope.setPreset}
        customRange={scope.customRange}
        onRange={scope.setRange}
        platform={platform}
        isTeamView={isTeamView}
      />

      {/* 实时负载只在全平台视角:槽位与 worker 是全平台共用的,团队视角下没有可行动的读法 */}
      {showLive && <LiveSection notes={notes} onDrill={drillRuns} />}
      <AdoptionSection {...shared} isTeamView={isTeamView} />
      <HealthSection {...shared} showInFlight={!showLive} onDrill={drillRuns} />
      <RunsSection {...shared} teamId={scope.teamId} filter={runsFilter} onFilter={setRunsFilter} />
      <AssetsSection {...shared} teamId={scope.teamId} />
      <GovernanceSection {...shared} platform={platform} teamId={scope.teamId} />
    </div>
  );
}

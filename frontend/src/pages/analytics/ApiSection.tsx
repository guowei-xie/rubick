import { Col, Row } from "antd";
import { useNavigate } from "react-router-dom";

import { AnalyticsQuery, ApiUsageData, MetricNote, analyticsApi } from "../../api";
import MetricCard, { CARD_COL } from "../../components/analytics/MetricCard";
import RankBarTable from "../../components/analytics/RankBarTable";
import SectionCard from "../../components/analytics/SectionCard";
import { taskLink } from "../../taskSearch";
import { useSectionData } from "./useAnalyticsQuery";

/**
 * 板块⑤「开放 API」:平台的取数能力有没有真的被脚本与 Agent 接走。
 *
 * 版面刻意**先用量、后凭证**:发了一堆 token 却没人调用,正是这块板要戳破的假象 ——
 * 把发放数摆在第一张,读者会先记住那个大数字,再看调用数时已经有了先入为主的印象。
 *
 * 「近 7 天用过」是全页唯一一个**既不是本区间、也不是此刻**的口径,所以它一次说三遍:
 * 卡片标题里带「近 7 天」、小标题右边挂一句口径、卡片自己由 windowed=false 挂「此刻」标记。
 * 只留「此刻」标记是不够的 —— 那句 tooltip 说的是「反映当前状态」,读者无从知道窗口多长,
 * 于是会拿它跟上面按 30 天算的调用数对账,对不上就当成 bug 提过来。
 *
 * 这块板**没有平台专属字段**(token 按团队成员收窄、运行按任务归属收窄,两种视角形状相同),
 * 所以它不像 Governance 那样要收 platform / teamId —— 别顺手加,加了就得永远维护那条分支。
 */
export default function ApiSection({
  query,
  scopeLabel,
  notes,
}: {
  query: AnalyticsQuery;
  scopeLabel: string;
  notes: Record<string, MetricNote>;
}) {
  const nav = useNavigate();
  const { ref, data, loading, error, reload } = useSectionData<ApiUsageData>(
    (signal) => analyticsApi(query, signal),
    query
  );

  // 一枚 token 都没发过、且**全期**一次 API 运行都没有 ⇒ 整块换成一句「还没开张」。
  // 其余四块不这么做(平台开张了就一定有任务、有权限),而开放 API 是可选能力:
  // 没接过是常态,给一屏「—」不如直接说清怎么开始。
  // 判据用 has_data 不用 value —— 后者会把「本区间没人调、但上个月天天调」误判成没开张。
  // 注意 tokens_issued 的 has_data 走的是 metric() 的默认推导(非 None 且非 0),
  // 哪天后端给它显式传 has_data,这里的空态就会失真(test_analytics_api 钉着这条)。
  const neverUsed = !!data && !data.tokens_issued.has_data && !data.api_runs.has_data;

  // 「1」单独摆着看不出是好是坏,「1 / 8」才读得出「发了一堆没人用」
  const issued = data?.tokens_issued.value ?? 0;

  return (
    <SectionCard
      id="api"
      innerRef={ref}
      title="开放 API"
      scopeLabel={scopeLabel}
      loading={loading}
      error={error}
      onRetry={reload}
      empty={neverUsed}
      emptyText={
        "还没有人通过开放 API 取数。要走通这条路:调用人在头像菜单里生成 API Token," +
        "任务作者打开任务上的「允许 API 调用」,并给调用人授权「运行」。"
      }
    >
      {data && (
        <>
          <Row gutter={[16, 16]}>
            <Col {...CARD_COL}>
              <MetricCard
                label="API 调用"
                metric={data.api_runs}
                note={notes["api_runs"]?.note}
              />
            </Col>
            <Col {...CARD_COL}>
              {/* 单一比率直接做成百分比卡,不画图 —— 一个数画成饼图不会变得更好懂 */}
              <MetricCard
                label="API 占比"
                metric={data.api_run_share}
                format="percent"
                note={notes["api_run_share"]?.note}
              />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard
                label="API 下载"
                metric={data.api_downloads}
                note={notes["api_downloads"]?.note}
              />
            </Col>
          </Row>

          <div className="rk-ana-subtitle">
            调用凭证
            <span className="rk-ana-subtitle-note">
              此刻口径;「用过」固定看近 7 天,两者都不随上方的时间范围变化
            </span>
          </div>
          <Row gutter={[16, 16]}>
            <Col {...CARD_COL}>
              <MetricCard
                label="已发放 Token"
                metric={data.tokens_issued}
                note={notes["tokens_issued"]?.note}
              />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard
                label="近 7 天用过"
                metric={data.tokens_active_7d}
                note={notes["tokens_active_7d"]?.note}
                // 分母跟在数字后面。issued 为 0 时不挂 ——「/ 0」不是信息,只是噪声
                suffix={issued ? <span className="rk-metric-sub"> / {issued}</span> : null}
              />
            </Col>
          </Row>

          <div className="rk-ana-subtitle">被调用最多的任务</div>
          <RankBarTable
            rows={data.top_tasks}
            rowKey={(r) => r.template_id}
            barKey="run_count"
            barLabel="API 调用次数"
            emptyText="这段时间没有任务被 API 调用过"
            onRow={(r) => ({ onClick: () => nav(taskLink(r.template_id)) })}
            columns={[
              {
                title: "编号",
                dataIndex: "template_id",
                width: 72,
                render: (v: number) => <span className="rk-ana-id">#{v}</span>,
              },
              {
                // 任务被硬删后名字就取不到了,但运行记录还在。留一行「已不存在」比整行消失好:
                // 编号还在,顺着它能在审计里查到这批调用是谁发起的
                title: "任务",
                dataIndex: "name",
                ellipsis: true,
                render: (v: string | null) => v ?? "(任务已不存在)",
              },
              {
                title: "团队",
                dataIndex: "team_name",
                ellipsis: true,
                render: (v: string | null) => v ?? "—",
              },
            ]}
          />
          <div className="rk-ana-caption">
            只数「API 触发」的运行,同一张任务在界面上被跑的次数不计入这里。一张任务能被调用要两件事
            都做到:作者打开「允许 API 调用」,且调用人本人被授权「运行」—— 开关不放宽任何权限。
          </div>
        </>
      )}
    </SectionCard>
  );
}

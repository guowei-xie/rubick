import { Col, Row, Table } from "antd";
import { useNavigate } from "react-router-dom";
import { AnalyticsQuery, GovernanceData, MetricNote, analyticsGovernance } from "../../api";
import MetricCard, { CARD_COL } from "../../components/analytics/MetricCard";
import RankBarTable, { taskRankColumns } from "../../components/analytics/RankBarTable";
import SectionCard from "../../components/analytics/SectionCard";
import { fmtDayTime } from "../../format";
import { taskLink } from "../../taskSearch";
import { useSectionData } from "./useAnalyticsQuery";

export default function GovernanceSection({
  query,
  scopeLabel,
  notes,
  platform,
  teamId,
}: {
  query: AnalyticsQuery;
  scopeLabel: string;
  notes: Record<string, MetricNote>;
  platform: boolean;
  teamId: number | null;
}) {
  const nav = useNavigate();
  const { ref, data, loading, error, reload } = useSectionData<GovernanceData>(
    (signal) => analyticsGovernance(query, signal),
    query
  );

  const asOf = data?.as_of;
  const cred = asOf?.credentials;

  return (
    <SectionCard
      id="governance"
      innerRef={ref}
      title="权限与配置"
      scopeLabel={scopeLabel}
      loading={loading}
      error={error}
      onRetry={reload}
    >
      {data && asOf && cred && (
        <>
          <Row gutter={[16, 16]}>
            <Col {...CARD_COL}>
              <MetricCard label="运行授权" metric={asOf.grants_total} />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard
                label="授权后从没跑过"
                metric={asOf.dormant_grants}
                note={notes["dormant_grants"]?.note}
              />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard
                label="失效的编辑权"
                metric={asOf.stale_edit_grants}
                note={notes["stale_edit_grants"]?.note}
              />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard
                label="缺账号的已上线任务"
                metric={cred.not_ready}
                to={platform ? "/admin/credentials" : teamId ? `/teams/${teamId}?tab=credentials` : undefined}
                note={notes["credential_not_ready"]?.note}
              />
            </Col>
            <Col {...CARD_COL}>
              <MetricCard
                label="取数账号覆盖"
                metric={cred.coverage}
                format="percent"
                suffix={
                  <span className="rk-metric-sub">
                    {" "}
                    {cred.configured}/{cred.required}
                  </span>
                }
              />
            </Col>
            {/* 平台专属。团队视角下 data.platform 这个键**不存在**,这张卡不渲染 ——
                而不是渲染成 0 或「无权限」,那会让人去猜后面藏了什么 */}
            {data.platform && (
              <Col {...CARD_COL}>
                <MetricCard
                  label="没有团队管理员"
                  metric={data.platform.teams_without_admin}
                  to="/admin/teams"
                  note={notes["teams_without_admin"]?.note}
                />
              </Col>
            )}
          </Row>

          {data.dormant_detail.length > 0 && (
            <>
              <div className="rk-ana-subtitle">
                空转授权明细
                <span className="rk-ana-subtitle-note">全期口径，不随时间范围变化</span>
              </div>
              <Table
                size="small"
                pagination={false}
                rowKey={(r) => `${r.user_id}-${r.template_id}`}
                dataSource={data.dormant_detail}
                scroll={{ x: "max-content" }}
                onRow={(r) => ({ onClick: () => nav(taskLink(r.template_id)) })}
                columns={[
                  { title: "被授权人", dataIndex: "user_name", width: 120 },
                  { title: "任务", dataIndex: "template_name", ellipsis: true },
                  { title: "编号", dataIndex: "template_id", width: 72,
                    render: (v: number) => <span className="rk-ana-id">#{v}</span> },
                  { title: "谁授的", dataIndex: "granted_by_name", width: 110 },
                  { title: "授权时间", dataIndex: "granted_at", width: 130,
                    render: (v: string | null) => fmtDayTime(v) },
                ]}
              />
            </>
          )}

          {data.wide_access_tasks.length > 0 && (
            <>
              <div className="rk-ana-subtitle">授权面最大的任务</div>
              <RankBarTable
                rows={data.wide_access_tasks}
                rowKey={(r) => r.template_id}
                barKey="granted_users"
                barLabel="被授权人数"
                onRow={(r) => ({ onClick: () => nav(taskLink(r.template_id)) })}
                columns={taskRankColumns<GovernanceData["wide_access_tasks"][number]>()}
              />
            </>
          )}

        </>
      )}
    </SectionCard>
  );
}

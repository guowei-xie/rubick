import { useEffect, useState } from "react";
import {
  Button, Checkbox, Descriptions, Drawer, Input, InputNumber, Space, Spin, Table, Tag, Tooltip,
} from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import {
  AnalyticsQuery, MetricNote, RunDetail, RunRow, RunsData, RunsFilter, RunsSort,
  analyticsRunDetail, analyticsRuns,
} from "../../api";
import SectionCard from "../../components/analytics/SectionCard";
import SqlModal from "../../components/SqlModal";
import StatusTag, {
  EMPTY_CELL as EMPTY, JOB_SOURCE, JOB_SOURCE_LONG, JOB_STATUS, JobSourceTag,
} from "../../components/StatusTag";
import { fmtDayTime, fmtDuration, fmtParamValue, fmtTime } from "../../format";
import { DRAWER } from "../../widths";
import { useAnalyticsQuery, useSectionData } from "./useAnalyticsQuery";

/** 筛选状态。`*_label` 只给界面显示那枚可关掉的标签用(从别的板下钻进来时带着),不发给后端 */
export type RunsFilterState = RunsFilter & { bucket_label?: string; datasource_label?: string };

const STATUS_KEYS = Object.keys(JOB_STATUS);
// 「补推」不是库里的来源值(见 jobSourceKey),筛不了
const SOURCE_KEYS = Object.keys(JOB_SOURCE).filter((k) => k !== "pushed");
const PAGE_SIZES = [20, 50, 100];
const toggle = (list: string[] | undefined, v: string) =>
  list?.includes(v) ? list.filter((x) => x !== v) : [...(list ?? []), v];

/**
 * 运行明细:时间范围内的每一次运行,可筛选、分页、看错误。
 *
 * 实时负载只看「此刻」、运行健康只给聚合 —— 运维排障要落到「是哪几次、卡在哪、报了什么」,
 * 就在这里。筛选状态由 AnalyticsPage 持有:运行健康的失败归因、实时负载的在跑/排队可以
 * 带着条件跳过来(下钻),而不是让人自己再点一遍。
 */
export default function RunsSection({
  query,
  scopeLabel,
  notes,
  teamId,
  filter,
  onFilter,
}: {
  query: AnalyticsQuery;
  scopeLabel: string;
  notes: Record<string, MetricNote>;
  teamId: number | null;
  filter: RunsFilterState;
  onFilter: (f: RunsFilterState) => void;
}) {
  const nav = useNavigate();
  const [sort, setSort] = useState<RunsSort>("created");
  const [pageSize, setPageSize] = useState(PAGE_SIZES[0]);
  const [detailId, setDetailId] = useState<number | null>(null);

  // 条件一变回到第一页:停在第 5 页换了筛选,多半落在一页空白上。页码记在「它属于哪组条件」
  // 名下、换了条件即视为 1 —— 在渲染时推出来,而不是事后用 effect 改回去(那会先按旧页码多打一次请求)
  const { bucket_label, datasource_label, ...sent } = filter;
  const pageKey = JSON.stringify([query, sent, sort, pageSize]);
  const [pager, setPager] = useState({ key: pageKey, page: 1 });
  const page = pager.key === pageKey ? pager.page : 1;
  const setPage = (p: number) => setPager({ key: pageKey, page: p });
  const { ref, data, loading, error, reload } = useSectionData<RunsData>(
    (signal) => analyticsRuns(query, sent, { sort, page, page_size: pageSize }, signal),
    [pageKey, page]
  );

  const set = (patch: Partial<RunsFilterState>) => onFilter({ ...filter, ...patch });
  const hasFilter = Object.values(filter).some(
    (v) => v !== undefined && v !== "" && v !== false && !(Array.isArray(v) && !v.length)
  );

  return (
    <SectionCard
      id="runs"
      innerRef={ref}
      title="运行明细"
      scopeLabel={scopeLabel}
      loading={loading && !data}
      error={error}
      onRetry={reload}
      extra={
        <Button type="text" size="small" icon={<ReloadOutlined />} onClick={reload}>刷新</Button>
      }
    >
      <div className="rk-runs-filters">
        <div className="rk-runs-chips">
          <span className="rk-runs-chip-label">状态</span>
          {STATUS_KEYS.map((k) => (
            <Tag.CheckableTag key={k} checked={!!filter.status?.includes(k)}
                              onChange={() => set({ status: toggle(filter.status, k) })}>
              {JOB_STATUS[k].label}
              <span className="rk-runs-chip-n">{data?.status_counts[k] ?? 0}</span>
            </Tag.CheckableTag>
          ))}
          <span className="rk-runs-chip-label" style={{ marginInlineStart: 12 }}>来源</span>
          {SOURCE_KEYS.map((k) => (
            <Tag.CheckableTag key={k} checked={!!filter.source?.includes(k)}
                              onChange={() => set({ source: toggle(filter.source, k) })}>
              {JOB_SOURCE_LONG[k]}
            </Tag.CheckableTag>
          ))}
        </div>
        <Space wrap size={[8, 8]}>
          <TextFilter placeholder="任务名" value={filter.template_kw}
                      onCommit={(v) => set({ template_kw: v })} />
          <TextFilter placeholder="运行人姓名 / 邮箱" value={filter.user_kw}
                      onCommit={(v) => set({ user_kw: v })} />
          <TextFilter placeholder="错误关键字" value={filter.error_kw}
                      onCommit={(v) => set({ error_kw: v })} />
          <InputNumber min={1} placeholder="耗时 ≥ 秒" style={{ width: 120 }}
                       value={filter.min_duration_s}
                       onChange={(v) => set({ min_duration_s: v ?? undefined })} />
          <InputNumber min={1} placeholder="排队 ≥ 秒" style={{ width: 120 }}
                       value={filter.min_queue_s}
                       onChange={(v) => set({ min_queue_s: v ?? undefined })} />
          <Checkbox checked={!!filter.executed_only}
                    onChange={(e) => set({ executed_only: e.target.checked })}>
            <Tooltip title="排除补推与结果复用（它们没有执行），与运行健康同口径">只看真正执行的</Tooltip>
          </Checkbox>
          {filter.bucket && (
            <Tag closable onClose={() => set({ bucket: undefined, bucket_label: undefined })}>
              归因：{bucket_label ?? filter.bucket}
            </Tag>
          )}
          {filter.datasource_id != null && (
            <Tag closable
                 onClose={() => set({ datasource_id: undefined, datasource_label: undefined })}>
              数据源：{datasource_label ?? `#${filter.datasource_id}`}
            </Tag>
          )}
          {hasFilter && <Button size="small" type="link" onClick={() => onFilter({})}>清空筛选</Button>}
        </Space>
      </div>

      {data?.bucket_capped && (
        <div className="rk-ana-caption">按归因筛选只在最近若干条失败里查找，更早的可能没列出来；可缩短时间范围。</div>
      )}

      <Table<RunRow>
        size="small"
        rowKey="job_id"
        dataSource={data?.items ?? []}
        scroll={{ x: "max-content" }}
        locale={{ emptyText: hasFilter ? "没有符合条件的运行" : "这段时间没有运行记录" }}
        pagination={{
          current: page,
          pageSize,
          total: data?.total ?? 0,
          pageSizeOptions: PAGE_SIZES,
          showSizeChanger: true,
          showTotal: (n) => `共 ${n} 次`,
          // 改每页条数会换 pageKey,页码随之回到 1
          onChange: (p, s) => (s !== pageSize ? setPageSize(s) : setPage(p)),
        }}
        onChange={(_p, _f, sorter) => {
          const s = Array.isArray(sorter) ? sorter[0] : sorter;
          setSort(s?.order ? (s.columnKey as RunsSort) : "created");
        }}
        columns={[
          { title: "提交时间", dataIndex: "created_at", width: 110,
            render: (v: string) => <Tooltip title={fmtTime(v)}>{fmtDayTime(v)}</Tooltip> },
          {
            title: "任务", dataIndex: "template_name",
            render: (v: string, r) => (
              <div style={{ maxWidth: 240 }}>
                <div className="rk-runs-ellipsis" title={v}>{v}</div>
                {!teamId && r.team_name && <div className="rk-runs-sub">{r.team_name}</div>}
              </div>
            ),
          },
          { title: "运行人", dataIndex: "user_name", render: (v) => v || EMPTY },
          {
            title: "类型", key: "source",
            render: (_: unknown, r) => <JobSourceTag r={r} />,
          },
          {
            title: "状态", dataIndex: "status",
            render: (v: string, r) => (
              <Space size={4}>
                <StatusTag map={JOB_STATUS} value={v} />
                {r.elapsed_s != null && (
                  <span className="rk-runs-sub">已跑 {fmtDuration(r.elapsed_s * 1000)}</span>
                )}
              </Space>
            ),
          },
          {
            title: <Tooltip title={notes["runs"]?.note}>排队</Tooltip>, key: "queue",
            dataIndex: "queue_ms", sorter: true, sortDirections: ["descend"],
            sortOrder: sort === "queue" ? "descend" : null,
            render: (v: number | null) => (v == null ? EMPTY : fmtDuration(v)),
          },
          {
            title: <Tooltip title={notes["runs"]?.note}>耗时</Tooltip>, key: "duration",
            dataIndex: "duration_ms", sorter: true, sortDirections: ["descend"],
            sortOrder: sort === "duration" ? "descend" : null,
            render: (v: number | null, r) =>
              v == null ? EMPTY : r.duration_approx ? (
                <Tooltip title="失败的运行没有精确耗时，这是从开始执行到失败的近似">
                  ≈ {fmtDuration(v)}
                </Tooltip>
              ) : fmtDuration(v),
          },
          { title: "行数", dataIndex: "row_count", render: (v) => (v == null ? EMPTY : v) },
          { title: "数据源", dataIndex: "datasource", render: (v) => v || EMPTY },
          {
            title: "错误", key: "error",
            render: (_: unknown, r) =>
              !r.error_excerpt ? EMPTY : (
                <Tooltip title={r.error_excerpt} overlayStyle={{ maxWidth: 480 }}>
                  <div className="rk-runs-ellipsis" style={{ maxWidth: 260 }}>
                    {r.error_label && <Tag color="red">{r.error_label}</Tag>}
                    {r.error_excerpt}
                  </div>
                </Tooltip>
              ),
          },
          {
            title: "", key: "ops", fixed: "right",
            render: (_: unknown, r) => (
              <Space size={0}>
                <Button type="link" size="small" onClick={() => setDetailId(r.job_id)}>详情</Button>
                <Button type="link" size="small"
                        onClick={() => nav(`/tasks?records=${r.template_id}&job=${r.job_id}`)}>
                  去任务
                </Button>
              </Space>
            ),
          },
        ]}
      />

      <RunDetailDrawer jobId={detailId} teamId={teamId} onClose={() => setDetailId(null)} />
    </SectionCard>
  );
}

/** 文本筛选:回车或失焦才提交 —— 边打字边发请求,一个关键字要打七八个聚合查询。 */
function TextFilter({ placeholder, value, onCommit }: {
  placeholder: string;
  value?: string;
  onCommit: (v: string | undefined) => void;
}) {
  const [draft, setDraft] = useState(value ?? "");
  useEffect(() => setDraft(value ?? ""), [value]);
  const commit = () => {
    const v = draft.trim() || undefined;
    if (v !== (value || undefined)) onCommit(v);
  };
  return (
    <Input.Search allowClear placeholder={placeholder} style={{ width: 180 }} value={draft}
                  onChange={(e) => setDraft(e.target.value)} onSearch={commit} onBlur={commit} />
  );
}

function RunDetailDrawer({ jobId, teamId, onClose }: {
  jobId: number | null;
  teamId: number | null;
  onClose: () => void;
}) {
  const { data: fetched, error } = useAnalyticsQuery<RunDetail>(
    () => analyticsRunDetail(jobId!, teamId),
    [jobId, teamId],
    { enabled: jobId != null }
  );
  const [sql, setSql] = useState<string | null>(null);
  // 切到另一条时别先闪一下上一条的内容
  const data = fetched?.job_id === jobId ? fetched : null;

  const params = Object.entries(data?.params ?? {});
  return (
    <Drawer title={jobId ? `运行 #${jobId}` : ""} open={jobId != null} onClose={onClose}
            width={DRAWER.run} destroyOnClose>
      {error ? <div style={{ color: "#ff4d4f" }}>{error}</div> : !data ? <Spin /> : (
        <>
          <Descriptions size="small" column={1} bordered
                        labelStyle={{ width: 96, whiteSpace: "nowrap" }}>
            <Descriptions.Item label="任务">
              {data.template_name}{data.team_name ? `（${data.team_name}）` : ""}
            </Descriptions.Item>
            <Descriptions.Item label="运行人">{data.user_name || "—"}</Descriptions.Item>
            <Descriptions.Item label="类型">
              <JobSourceTag r={data} />
            </Descriptions.Item>
            <Descriptions.Item label="状态"><StatusTag map={JOB_STATUS} value={data.status} /></Descriptions.Item>
            <Descriptions.Item label="提交">{fmtTime(data.created_at)}</Descriptions.Item>
            <Descriptions.Item label="开始执行">{fmtTime(data.started_at)}</Descriptions.Item>
            <Descriptions.Item label="排队">{fmtDuration(data.queue_ms)}</Descriptions.Item>
            <Descriptions.Item label="耗时">
              {data.duration_approx ? `≈ ${fmtDuration(data.duration_ms)}（开始执行到失败）` : fmtDuration(data.duration_ms)}
            </Descriptions.Item>
            <Descriptions.Item label="行数">{data.row_count ?? "—"}</Descriptions.Item>
            <Descriptions.Item label="数据源">
              {data.datasource ?? "—"}{data.engine ? ` · ${data.engine.toUpperCase()}` : ""}
            </Descriptions.Item>
          </Descriptions>

          {data.error && (
            <>
              <div className="rk-ana-subtitle">
                错误{data.error_label && <Tag color="red" style={{ marginInlineStart: 8 }}>{data.error_label}</Tag>}
              </div>
              <pre className="rk-runs-pre">{data.error}</pre>
            </>
          )}

          <div className="rk-ana-subtitle">参数</div>
          {params.length ? (
            <Descriptions size="small" column={1} bordered>
              {params.map(([k, v]) => (
                <Descriptions.Item key={k} label={k}>
                  {fmtParamValue(v)}
                </Descriptions.Item>
              ))}
            </Descriptions>
          ) : <div className="rk-runs-sub">无参数</div>}

          <div style={{ marginTop: 16 }}>
            <Button disabled={!data.executed_sql} onClick={() => setSql(data.executed_sql)}>
              {data.executed_sql ? "查看执行 SQL" : "没有执行 SQL（未执行到那一步）"}
            </Button>
          </div>
          <SqlModal sql={sql} onClose={() => setSql(null)} />
        </>
      )}
    </Drawer>
  );
}
